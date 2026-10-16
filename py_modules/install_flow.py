from __future__ import annotations

# Semantic index:
# - owner: docs/upstream-conflict/sideload/plugin-management.md -- persist exact-task cancel and notice intent, drain full rechecks through persistence, reject uninstall rechecks after install submission, and preserve success evidence.
# - participant: docs/upstream-conflict/sideload/diagnostics.md -- observe only persisted stage changes and isolate observer failures.
import asyncio
import json
import os
import time
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from install_ops import backup_data, download_asset, file_sha256, inspect_archive, plugin_stamp, verify_installed
from persist import save_settings
from scan import read_plugins_dir

TERMINAL = {"failed", "closed", "cancelled"}


class InstallFlow:
    def __init__(self, home: str, runtime: str, self_name: str, self_folder: str, releases_for, ssl_context, transport_factory=None, observer=None):
        if not home or not runtime or not Path(runtime).is_absolute():
            raise RuntimeError("Decky 运行目录不可用")
        self.home, self.root = Path(home), Path(runtime) / "installations"
        self.self_name, self.self_folder = self_name, self_folder
        self.releases_for, self.ssl_context = releases_for, ssl_context
        self.transport_factory = transport_factory
        self.observer = observer
        self.stopping = False
        self.download_cancel = threading.Event()
        self.file = self.root / "operation.json"
        self.operation = None
        self.task = None
        self.validation = None
        self.cancellation_waiters = 0
        self.lock = asyncio.Lock()
        if self.file.is_file():
            self.operation = json.loads(self.file.read_text())
            if self.operation["stage"] == "uninstalling":
                self._update(stage="unknown", uninstall_blocked=True, error="卸载被插件重载中断，不会继续安装或重放卸载")
            if self.operation.get("cancel_requested") and self.operation["stage"] == "awaiting":
                self._update(stage="unknown", error="取消请求被重载中断，结果未知，不会重放原生请求")
            if self.operation.get("cancel_requested") and self.operation["stage"] == "ready":
                self._update(stage="cancelled")
            if self.operation["stage"] in ("checking", "backup", "validating"):
                download = self.operation.get("download")
                if download and download["status"] != "complete":
                    self.operation["download"] = {**download, "status": "failed"}
                self._update(stage="cancelled" if self.operation.get("cancel_requested") else "failed", error="准备被插件重载中断，尚未交给 Loader。请重新准备。")

    def _update(self, **changes):
        previous = self.operation
        candidate = {**self.operation, **changes}
        save_settings(str(self.file), candidate)
        self.operation = candidate
        if self.observer:
            try:
                if not changes or candidate.get("stage") != previous.get("stage"):
                    self.observer("operation_stage", operationId=candidate["id"], stage=candidate["stage"], status="success" if candidate.get("outcome") == "success" else candidate["stage"])
                download = candidate.get("download") or {}
                before = previous.get("download") or {}
                if (download.get("status"), download.get("route"), download.get("attempt")) != (before.get("status"), before.get("route"), before.get("attempt")):
                    self.observer("download_stage", operationId=candidate["id"], stage=candidate["stage"], status=download.get("status"), route=download.get("route"), attempt=download.get("attempt"))
            except Exception:
                pass

    def _public(self):
        if not self.operation:
            return None
        result = {k: v for k, v in self.operation.items() if k not in ("previous_stamp", "package", "source")}
        if self.operation.get("package"):
            result["package"] = {k: v for k, v in self.operation["package"].items() if k != "hashes"}
        return result

    async def prepare(self, repo: str, tag: str, asset_url: str, existing_name: str | None, target: dict | None = None, mode: str = "install"):
        if mode not in ("install", "reinstall") or mode == "reinstall" and not existing_name:
            raise RuntimeError("卸载并安装必须选择现有插件")
        if self.stopping:
            raise RuntimeError("插件正在卸载")
        if self.cancellation_waiters or self.operation and self.operation["stage"] not in TERMINAL:
            raise RuntimeError("已有安装任务，请先查看其结果")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise RuntimeError("准备目录不能是符号链接")
        size = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file() and not p.is_symlink())
        if size > 2 * 1024 * 1024 * 1024 - 640 * 1024 * 1024:
            raise RuntimeError(f"安装记录与备份超过 2 GiB，请先检查并清理 {self.root}")
        identifier = uuid.uuid4().hex
        directory = self.root / identifier
        directory.mkdir(mode=0o700)
        self.download_cancel = threading.Event()
        transport = self.transport_factory() if self.transport_factory else None
        self.operation = {"id": identifier, "stage": "checking", "repo": repo, "tag": tag, "existing_name": existing_name, "target": target,
                          "network_mode": transport.mode if transport else "direct",
                          "download": None,
                          "created_at": datetime.now(timezone.utc).isoformat(), "error": None, "backup": None,
                          "artifact": "file://" + str(directory / "plugin.zip"), "request_id": None, "package": None,
                          "saw_finish": False, "saw_start": False, "binding_saved": False, "mode": mode,
                          "outcome": None, "directory": str(directory)}
        self._update()
        self.task = asyncio.create_task(self._prepare(directory, repo, tag, asset_url, existing_name, target, transport))
        return self._public()

    def _download_progress(self, identifier, cancel, snapshot):
        if not self.stopping and not cancel.is_set() and self.operation and self.operation["id"] == identifier and not self.operation.get("cancel_requested") and self.operation["stage"] == "checking":
            self._update(download=snapshot)

    async def _work(self, function, *args):
        worker = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                await worker
            except Exception:
                pass
            raise

    def _require_running(self, identifier, cancel=None):
        if not self.operation or self.operation["id"] != identifier or self.operation.get("cancel_requested") or self.stopping or cancel and cancel.is_set():
            raise RuntimeError("任务已取消，不会继续准备或提交")

    async def _prepare_work(self, identifier, cancel, function, *args):
        self._require_running(identifier, cancel)
        result = await self._work(function, *args)
        self._require_running(identifier, cancel)
        return result

    def _finish_cancelled(self):
        download = self.operation.get("download")
        changes = {"stage": "cancelled" if self.operation.get("cancel_requested") else "failed",
                   "error": "任务已取消，未继续安装" if self.operation.get("cancel_requested") else "准备被插件卸载中断，尚未交给 Loader。请重新准备。"}
        if download and download["status"] != "complete":
            changes["download"] = {**download, "status": "failed"}
        self._update(**changes)

    async def cancel(self, identifier: str):
        self.cancellation_waiters += 1
        try:
            return await self._cancel(identifier)
        finally:
            self.cancellation_waiters -= 1

    async def _cancel(self, identifier: str):
        # Intention is persisted outside the validation lock: hashing may be in flight.
        op = self.operation
        if not op or op["id"] != identifier:
            raise RuntimeError("任务已变化")
        if op["stage"] in TERMINAL:
            return self._public()
        if op["stage"] in ("installing", "verified") or op.get("saw_start"):
            raise RuntimeError("Decky安装已开始，无法中断；仍将核验并保存结果")
        if not op.get("cancel_requested"):
            if op["stage"] == "unknown" or op["stage"] == "awaiting" and not op.get("request_id"):
                raise RuntimeError("原生请求尚未确认，无法安全取消")
            self._update(cancel_requested=True)
            self.download_cancel.set()
        if op["stage"] in ("checking", "backup", "validating"):
            task = self.validation if op["stage"] == "validating" else self.task
            if task and task is not asyncio.current_task():
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
            if self.operation["id"] == identifier and self.operation["stage"] in ("checking", "backup", "validating"):
                self._finish_cancelled()
        elif op["stage"] == "ready" or op["stage"] == "uninstalling" and not op.get("uninstall_started"):
            self._finish_cancelled()
        return self._public()

    def _can_recheck_cancelled_uninstall(self):
        op = self.operation
        return bool(op and op.get("mode") == "reinstall" and op.get("cancel_requested") and op.get("uninstall_started")
                    and op["stage"] in ("uninstalling", "unknown", "cancelled")
                    and "submitted_at" not in op and not op.get("request_id") and not op.get("saw_start") and not op.get("saw_finish"))

    async def complete_cancelled_uninstall(self, identifier):
        async with self.lock:
            if self.stopping:
                raise RuntimeError("插件正在卸载")
            op = self.operation
            if not op or op["id"] != identifier or not self._can_recheck_cancelled_uninstall():
                raise RuntimeError("没有可回查的已取消卸载任务")
            if op["stage"] == "cancelled":
                return self._public()
            self.validation = asyncio.current_task()
            try:
                self._update(uninstall_result_received=True)
                try:
                    await self._check_prepared(absent=True)
                except Exception as error:
                    self._update(stage="unknown", uninstall_blocked=True, error=f"已停止后续安装，卸载结果尚未确认：{error}")
                else:
                    self._finish_cancelled()
                return self._public()
            finally:
                self.validation = None

    async def _prepare(self, directory: Path, repo: str, tag: str, asset_url: str, existing_name: str | None, target: dict | None, transport=None):
        identifier = directory.name
        cancel = self.download_cancel
        worker = None
        try:
            installed = await self._prepare_work(identifier, cancel, read_plugins_dir, str(self.home / "plugins"))
            existing = next((p for p in installed if p["name"] == existing_name), None) if existing_name else None
            if existing_name and not existing:
                raise RuntimeError("原插件已不存在，请刷新列表")
            releases = await self._prepare_work(identifier, cancel, self.releases_for, repo, existing.get("version") if existing else None)
            release = next((r for r in releases if r["tag"] == tag), None)
            if not release or not any(z["url"] == asset_url for z in release["zips"]):
                raise RuntimeError("所选安装包不属于此仓库和版本，请重新读取")
            self._update(repo=release["repo"])
            archive = directory / "plugin.zip"
            loop = asyncio.get_running_loop()
            def progress(snapshot):
                loop.call_soon_threadsafe(self._download_progress, identifier, cancel, snapshot)
            def download_work():
                try:
                    download_asset(asset_url, archive, self.ssl_context(), transport=transport, progress=progress, cancel=cancel)
                except Exception as error:
                    return error
                return None
            worker = asyncio.create_task(asyncio.to_thread(download_work))
            error = await asyncio.shield(worker)
            if error is not None:
                raise error
            self._require_running(identifier, cancel)
            package = await self._prepare_work(identifier, cancel, inspect_archive, archive, existing, installed, self.self_name, self.self_folder, target)
            previous = await self._prepare_work(identifier, cancel, plugin_stamp, self.home, package["folder"])
            if not existing and ((self.home / "plugins" / package["folder"]).exists() or (self.home / "plugins" / package["folder"]).is_symlink()):
                raise RuntimeError("目标目录已存在，请从已安装插件进入管理")
            self._update(package=package, previous_stamp=previous, stage="backup")
            if existing:
                backup = await self._work(backup_data, self.home, existing["folder"], directory / "data-backup.zip")
                backup["created_at"] = datetime.now(timezone.utc).isoformat()
                self._update(backup=backup)
                self._require_running(identifier, cancel)
            self._update(stage="ready")
        except asyncio.CancelledError:
            cancel.set()
            if worker:
                try:
                    await worker
                except Exception:
                    pass
            if self.operation and self.operation["id"] == identifier:
                self._finish_cancelled()
            raise
        except Exception as error:
            if self.operation and self.operation["id"] == identifier:
                if cancel.is_set() or self.operation.get("cancel_requested"):
                    self._finish_cancelled()
                else:
                    self._update(stage="failed", error=str(error))

    async def _check_prepared(self, absent=False):
        op = self.operation
        archive = Path(op["directory"]) / "plugin.zip"
        if await self._work(file_sha256, archive) != op["package"]["sha256"]:
            raise RuntimeError("准备后的安装包发生变化")
        if op["backup"] and await self._work(file_sha256, Path(op["backup"]["archive"])) != op["backup"]["sha256"]:
            raise RuntimeError("备份发生变化，已阻止操作")
        if absent:
            root = self.home / "plugins"
            target = root / op["package"]["folder"]
            if root.is_symlink() or not root.is_dir() or os.path.lexists(target):
                raise RuntimeError("目标程序目录仍存在或被重新占用，已阻止安装")
            installed = await self._work(read_plugins_dir, str(root))
            if any(p["name"] == op["package"]["name"] for p in installed):
                raise RuntimeError("同名插件仍存在，已阻止安装")
        elif await self._work(plugin_stamp, self.home, op["package"]["folder"]) != op["previous_stamp"]:
            raise RuntimeError("原插件在准备后发生变化，请重新检查")

    async def claim_uninstall(self, identifier: str):
        async with self.lock:
            op = self.operation
            if not op or op["id"] != identifier or op["stage"] != "ready" or op.get("mode") != "reinstall":
                raise RuntimeError("任务尚未准备好或卸载已经领取")
            self._require_running(identifier)
            self.validation = asyncio.current_task()
            self._update(stage="validating")
            try:
                await self._check_prepared()
                self._require_running(identifier)
                self._update(stage="uninstalling", uninstall_claimed_at=time.time())
                return self._public()
            except Exception as error:
                if self.operation.get("cancel_requested"):
                    self._finish_cancelled()
                else:
                    self._update(stage="failed", error=str(error))
                raise
            finally:
                self.validation = None

    async def submit(self, identifier: str, after_uninstall=False):
        async with self.lock:
            op = self.operation
            expected = "uninstalling" if after_uninstall else "ready"
            if not op or op["id"] != identifier or op["stage"] != expected:
                raise RuntimeError("任务尚未准备好或已经提交，禁止重复安装")
            reinstall = op.get("mode") == "reinstall"
            if reinstall != after_uninstall:
                raise RuntimeError("卸载并安装必须先完成原生卸载")
            if after_uninstall and time.time() - op["uninstall_claimed_at"] > 120:
                self._update(stage="unknown", error="卸载确认超时，不会继续安装")
                raise RuntimeError("卸载确认超时，不会继续安装")
            self._require_running(identifier)
            self.validation = asyncio.current_task()
            self._update(stage="validating")
            try:
                await self._check_prepared(absent=after_uninstall)
                self._require_running(identifier)
                self._update(stage="awaiting", submitted_at=time.time())
                return self._public()
            except Exception as error:
                if self.operation.get("cancel_requested"):
                    # An actual uninstall still needs absence/result accounting.
                    if after_uninstall and self.operation.get("uninstall_started"):
                        self._update(stage="unknown", uninstall_blocked=True, error="已停止后续安装，等待卸载结果回查")
                    else:
                        self._finish_cancelled()
                else:
                    self._update(stage="unknown" if after_uninstall else "failed", error=str(error))
                raise
            finally:
                self.validation = None

    async def event(self, identifier: str, kind: str, name: str, value: str = "", sha256: str = ""):
        async with self.lock:
            op = self.operation
            if not op or op["id"] != identifier or not op.get("package") or name != op["package"]["name"]:
                return self._public()
            if kind == "dispatch_not_started" and op["stage"] == "awaiting" and not op.get("request_id") and not op.get("saw_start"):
                self._update(stage="cancelled", cancel_requested=True, error="已取消，未发出原生安装请求")
            elif kind == "uninstall_started" and op["stage"] == "uninstalling" and not op.get("cancel_requested"):
                self._update(uninstall_started=True)
            elif kind == "uninstall_not_started" and op.get("cancel_requested") and op["stage"] in ("uninstalling", "cancelled"):
                self._finish_cancelled()
            elif kind == "uninstall_cancelled" and op["stage"] == "ready" and op.get("mode") == "reinstall":
                self._update(stage="cancelled")
            elif kind == "uninstall_error" and op["stage"] == "uninstalling":
                self._update(stage="unknown", error=value or "卸载结果未知，不会继续安装", uninstall_blocked=True)
            elif op["stage"] in ("awaiting", "installing", "unknown"):
                if kind == "prompt" and value:
                    if sha256 != op["package"]["sha256"] or op.get("request_id") not in (None, value):
                        self._update(stage="unknown", evidence_conflict=True, error="检测到同名的其他安装请求，无法关联完成事件")
                    elif not op.get("request_id"):
                        self._update(request_id=value)
                elif kind == "start" and op.get("request_id"):
                    self._update(stage="installing", saw_start=True)
                elif kind == "finish" and op.get("request_id"):
                    if op.get("saw_start"):
                        self._update(saw_finish=True)
                    else:
                        self._update(stage="unknown", error="缺少本次安装开始事件，无法关联完成证据")
                elif kind == "dispatch_error":
                    self._update(stage="unknown", evidence_conflict=True, error=value or "安装请求结果未知，禁止自动重试")
                elif kind == "cancel_unknown" and op.get("cancel_requested") and not op.get("saw_start"):
                    self._update(stage="unknown", error="取消结果无法确认，不会重放请求；请核对Decky任务")
                elif kind == "cancelled" and op.get("request_id") and not op.get("saw_start"):
                    self._update(stage="cancelled")
            return self._public()

    async def status(self, finalize=None):
        async with self.lock:
            op = self.operation
            if not op or self.stopping:
                return self._public()
            self.validation = asyncio.current_task()
            try:
                if op["stage"] == "unknown" and op.get("uninstall_result_received") and self._can_recheck_cancelled_uninstall():
                    try:
                        await self._check_prepared(absent=True)
                    except Exception:
                        pass
                    else:
                        self._finish_cancelled()
                    op = self.operation
                if op["stage"] == "uninstalling" and time.time() - op.get("uninstall_claimed_at", 0) > 120:
                    self._update(stage="unknown", uninstall_blocked=True, error="卸载结果超时，不会继续安装")
                elif op["stage"] in ("awaiting", "installing", "unknown"):
                    if op.get("saw_finish") and not op.get("evidence_conflict") and not op.get("uninstall_blocked"):
                        matches = await self._work(verify_installed, self.home, op["package"], op["previous_stamp"])
                        if matches:
                            self._update(stage="verified", error=None)
                        else:
                            self._update(stage="unknown", error="Loader 流程已结束，但安装文件尚未通过回查。旧版可能已卸载，请检查 Decky。")
                    elif "submitted_at" in op and time.time() - op["submitted_at"] > 120:
                        self._update(stage="unknown", error=op.get("error") or "未取得完成证据。可能仍在等待确认、已取消或安装未完成；不会自动重试。")
                if self.operation["stage"] == "verified" and finalize:
                    # Binding writes are idempotent; a failed result write retries only finalization.
                    finalize(self._public())
                    self._update(stage="closed", binding_saved=True, outcome="success", close_reason="verified", completed_at=datetime.now(timezone.utc).isoformat())
                return self._public()
            finally:
                self.validation = None

    async def close(self, identifier: str):
        async with self.lock:
            if not self.operation or self.operation["id"] != identifier:
                raise RuntimeError("任务已变化")
            if self.operation["stage"] == "unknown" and (self.operation.get("cancel_requested") or self.operation.get("uninstall_blocked")):
                raise RuntimeError("原生操作结果未知，不能结束跟踪并放行新任务")
            if self.operation["stage"] in ("checking", "backup", "validating", "installing", "awaiting", "uninstalling", "verified"):
                raise RuntimeError("任务仍在进行或收尾，请等待结果")
            if self.operation["stage"] != "closed":
                self._update(stage="closed", close_reason="manual")
            return self._public()

    async def dismiss(self, identifier: str):
        async with self.lock:
            if not self.operation or self.operation["id"] != identifier:
                raise RuntimeError("任务已变化")
            if self.operation["stage"] not in TERMINAL:
                raise RuntimeError("活动任务不能关闭提示")
            if not self.operation.get("notice_dismissed"):
                self._update(notice_dismissed=True)
            return self._public()

    async def unload(self):
        self.stopping = True
        self.download_cancel.set()
        # Do not cancel a coroutine that owns a to_thread file/query worker.
        # Download has cooperative cancellation; drain full query/file coroutines
        # through their final persistence, not just their underlying threads.
        for task in (self.task, self.validation):
            if task and not task.done() and task is not asyncio.current_task():
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
