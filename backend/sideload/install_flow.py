from __future__ import annotations

import asyncio
import json
import os
import stat
import time
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sideload.backups import clean_backups
from sideload.install_ops import backup_data, download_asset, file_sha256, inspect_archive, plugin_stamp, verify_installed
from sideload.remote_binaries import prepare_remote_binaries
from sideload.zip_version import probe_release_zip
from sideload.persist import save_settings
from sideload.scan import read_plugins_dir
from sideload.diagnostic_context import error_fields, operation_context
from sideload.diagnostic_snapshot import operation_snapshot

TERMINAL = {"failed", "closed", "cancelled"}


class InstallFlow:
    def __init__(self, home: str, runtime: str, self_name: str, self_folder: str, releases_for, ssl_context, transport_factory=None, observer=None, timeout_seconds=None):
        if not home or not runtime or not Path(runtime).is_absolute():
            raise RuntimeError("Decky 运行目录不可用")
        self.home, self.root = Path(home), Path(runtime) / "installations"
        self.self_name, self.self_folder = self_name, self_folder
        self.releases_for, self.ssl_context = releases_for, ssl_context
        self.transport_factory = transport_factory
        self.observer = observer
        self.timeout_seconds = timeout_seconds or (lambda: 180)
        self.stopping = False
        self.download_cancel = threading.Event()
        self.file = self.root / "operation.json"
        self.operation = None
        self.task = None
        self.validation = None
        self.cancellation_waiters = 0
        self.cleanup = None
        self.lock = asyncio.Lock()
        if self.file.is_file():
            self.operation = json.loads(self.file.read_text())
            if self.operation.get("outcome") == "success" and not self.operation.get("saw_finish"):
                self._update(stage="unknown", outcome=None, close_reason=None, error="旧任务未取得 Loader 完成证据，已撤销成功判定。请核对 Decky 安装结果。")
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
        if "error" in changes:
            changes.setdefault("diagnostic_error", {"errorCode": "evidence_conflict" if changes.get("evidence_conflict") else "loader_failed" if self.operation.get("stage") in ("awaiting", "installing", "uninstalling") else "invalid_state"} if changes["error"] else {})
        if changes.get("stage") in ("failed", "cancelled"):
            dependency = self.operation.get("dependency_download")
            if dependency and dependency["status"] in ("connecting", "downloading"):
                changes["dependency_download"] = {**dependency, "status": "failed"}
        observations = [dict(row) for row in self.operation.get("observations", [])]
        stage = changes.get("stage")
        if stage and stage != self.operation.get("stage"):
            now = datetime.now(timezone.utc).isoformat()
            for row in observations:
                if row["status"] == "running" and stage != "unknown":
                    row.update(status="failed" if stage in ("failed", "cancelled") else "complete", updated_at=now)
                    if stage in ("failed", "cancelled"):
                        row["detail"] = changes.get("error") or "任务已取消"
            labels = {"backup": "数据备份", "awaiting": "等待原生确认", "installing": "Loader 安装", "verified": "保存安装结果", "uninstalling": "原生卸载"}
            if stage in labels:
                observations = [row for row in observations if row["id"] != stage]
                observations.append({"id": stage, "label": labels[stage], "status": "running", "updated_at": now})
            changes.setdefault("observations", observations)
        previous = self.operation
        candidate = {**self.operation, **changes}
        save_settings(str(self.file), candidate)
        self.operation = candidate
        if self.observer:
            try:
                evidence = ("stage", "outcome", "saw_start", "saw_finish", "binding_saved", "cancel_requested", "evidence_conflict", "uninstall_started", "uninstall_blocked", "uninstall_result_received", "error", "artifact_deleted", "backup_deleted")
                if not changes or any(candidate.get(key) != previous.get(key) for key in evidence):
                    self.observer("operation_stage", operationId=candidate["id"], stage=candidate["stage"], status="success" if candidate.get("outcome") == "success" else candidate["stage"], operation=operation_snapshot(candidate), **candidate.get("diagnostic_error", {}))
                for key, event in (("download", "download_stage"), ("dependency_download", "dependency_stage")):
                    download = candidate.get(key) or {}; before = previous.get(key) or {}
                    if any(download.get(field) != before.get(field) for field in ("status", "route", "attempt", "index")):
                        self.observer(event, operationId=candidate["id"], stage=candidate["stage"], **{field: download.get(field) for field in ("status", "route", "attempt", "received", "total", "index", "count")})
            except Exception:
                pass

    def _observe(self, identifier, label, status="running", detail=""):
        now = datetime.now(timezone.utc).isoformat()
        rows = [dict(row) for row in self.operation.get("observations", [])]
        for row in rows:
            if row["id"] != identifier and row["status"] == "running":
                row.update(status="complete", updated_at=now)
        entry = {"id": identifier, "label": label, "status": status, "detail": detail, "updated_at": now}
        existing = next((index for index, row in enumerate(rows) if row["id"] == identifier), None)
        if existing is None:
            rows.append(entry)
        else:
            rows[existing] = entry
        self._update(observations=rows)

    def _public(self):
        if not self.operation:
            return None
        result = {k: v for k, v in self.operation.items() if k not in ("previous_stamp", "package", "source")}
        if self.operation.get("package"):
            result["package"] = {k: v for k, v in self.operation["package"].items() if k != "hashes"}
        if self.operation.get("backup"):
            archive = self.root / self.operation["id"] / "data-backup.zip"
            result["backup_deleted"] = archive.is_symlink() or not archive.is_file()
        return result

    async def prepare(self, repo: str, tag: str, asset_url: str, existing_name: str | None, target: dict | None = None, mode: str = "install"):
        async with self.lock:
            if mode not in ("install", "reinstall") or mode == "reinstall" and not existing_name:
                raise RuntimeError("卸载并安装必须选择现有插件")
            if self.stopping:
                raise RuntimeError("插件正在卸载")
            if self.cancellation_waiters or self.operation and self.operation["stage"] not in TERMINAL:
                raise RuntimeError("已有安装任务，请先查看其结果")
            await self._clean_backups(True)
            if self.stopping:
                raise RuntimeError("插件正在卸载")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.root.is_symlink():
                raise RuntimeError("准备目录不能是符号链接")
            size = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file() and not p.is_symlink())
            if size > 2 * 1024 * 1024 * 1024 - 1280 * 1024 * 1024:
                raise RuntimeError(f"安装记录与备份超过 2 GiB，请先检查并清理 {self.root}")
            identifier = uuid.uuid4().hex
            directory = self.root / identifier
            directory.mkdir(mode=0o700)
            self.download_cancel = threading.Event()
            transport = self.transport_factory() if self.transport_factory else None
            self.operation = {"id": identifier, "stage": "checking", "repo": repo, "tag": tag, "asset_url": asset_url, "existing_name": existing_name, "target": target,
                              "network_mode": transport.mode if transport else "direct",
                              "download": None,
                              "created_at": datetime.now(timezone.utc).isoformat(), "error": None, "backup": None,
                              "artifact": "file://" + str(directory / "plugin.zip"), "request_id": None, "package": None,
                              "saw_finish": False, "saw_start": False, "binding_saved": False, "mode": mode,
                              "outcome": None, "directory": str(directory)}
            self._update()
            self._observe("download", "主安装包下载")
            self.task = asyncio.create_task(self._prepare(directory, repo, tag, asset_url, existing_name, target, transport))
            return self._public()

    async def clean_backups(self, expired_only: bool = True):
        async with self.lock:
            if self.stopping:
                raise RuntimeError("插件正在卸载")
            return await self._clean_backups(expired_only)

    async def _clean_backups(self, expired_only: bool):
        protected = set()
        op = self.operation
        workers = any(task and not task.done() for task in (self.task, self.validation))
        if op and (op["stage"] not in TERMINAL or workers or self.cancellation_waiters):
            protected.add(op["id"])
        self.cleanup = asyncio.current_task()
        try:
            result = await self._work(clean_backups, self.root, protected, expired_only)
            if self.observer:
                try:
                    self.observer("backup_cleanup", expiredOnly=expired_only, **result)
                except Exception:
                    pass
            return result
        finally:
            self.cleanup = None

    async def continue_prepare(self, identifier: str):
        return await self._reuse_download(identifier, download=False)

    async def retry_download(self, identifier: str):
        return await self._reuse_download(identifier, download=True)

    async def _reuse_download(self, identifier: str, download: bool):
        if self.stopping:
            raise RuntimeError("插件正在卸载")
        async with self.lock:
            op = self.operation
            download_state = (op or {}).get("download") or {}
            if not op or op["id"] != identifier or op["stage"] != "failed" or op.get("cancel_requested") or op.get("request_id") or op.get("saw_start"):
                raise RuntimeError("没有可继续的安装任务")
            if download and download_state.get("status") == "complete":
                raise RuntimeError("下载已完成，请继续安装而不是重新下载")
            if not download and download_state.get("status") != "complete":
                raise RuntimeError("下载尚未完成，不能继续安装")
            if download and not isinstance(op.get("asset_url"), str):
                raise RuntimeError("这次任务没有记录下载地址，请从详情重新安装")
            directory = Path(op["directory"])
            archive = directory / "plugin.zip"
            if directory.is_symlink() or archive.is_symlink() or (not download and not archive.is_file()):
                raise RuntimeError("安装包已不在，请重新下载")
            self.download_cancel = threading.Event()
            self._update(stage="checking", error=None, download=None if download else download_state,
                         dependencies=None if download else op.get("dependencies"),
                         dependency_download=None if download else op.get("dependency_download"))
            self._observe("download", "主安装包下载", "running" if download else "complete")
            self.task = asyncio.create_task(self._resume(directory, identifier, download))
            return self._public()

    async def _finish_downloaded(self, directory: Path, identifier: str, cancel, transport=None):
        installed = await self._prepare_work(identifier, cancel, read_plugins_dir, str(self.home / "plugins"))
        existing_name = self.operation.get("existing_name")
        existing = next((p for p in installed if p["name"] == existing_name), None) if existing_name else None
        if existing_name and not existing:
            raise RuntimeError("原插件已不存在，请刷新列表")
        archive = directory / "plugin.zip"
        self._observe("archive", "安装包校验")
        package = await self._prepare_work(identifier, cancel, inspect_archive, archive, existing, installed, self.self_name, self.self_folder, self.operation.get("target"))
        previous = await self._prepare_work(identifier, cancel, plugin_stamp, self.home, package["folder"])
        if not existing and ((self.home / "plugins" / package["folder"]).exists() or (self.home / "plugins" / package["folder"]).is_symlink()):
            raise RuntimeError("目标目录已存在，请从已安装插件进入管理")
        self._observe("dependencies", "额外依赖准备")
        loop = asyncio.get_running_loop()
        def progress(snapshot):
            loop.call_soon_threadsafe(self._dependency_progress, identifier, cancel, snapshot)
        def bundle():
            return prepare_remote_binaries(archive, package, self.ssl_context(), transport=transport,
                                           progress=progress, cancel=cancel, timeout_seconds=self.timeout_seconds())
        dependencies = await self._prepare_work(identifier, cancel, bundle)
        if dependencies and dependencies["count"]:
            package = await self._prepare_work(identifier, cancel, inspect_archive, archive, existing, installed, self.self_name, self.self_folder, self.operation.get("target"))
        elif dependencies is None and self.operation.get("dependencies") and (directory / "source.zip").is_file():
            dependencies = self.operation["dependencies"]
        self._observe("dependencies", "额外依赖准备", "complete", "、".join(dependencies["names"]) if dependencies and dependencies["count"] else "无额外依赖")
        self._update(package=package, previous_stamp=previous, dependencies=dependencies, stage="backup")
        if existing:
            backup = await self._work(backup_data, self.home, existing["folder"], directory / "data-backup.zip")
            backup["created_at"] = datetime.now(timezone.utc).isoformat()
            self._update(backup=backup)
            self._require_running(identifier, cancel)
        self._update(stage="ready")

    @operation_context
    async def _resume(self, directory: Path, identifier: str, download: bool):
        cancel = self.download_cancel
        worker = None
        try:
            transport = self.transport_factory() if self.transport_factory else None
            if download:
                archive = directory / "plugin.zip"
                if archive.exists():
                    if archive.is_symlink():
                        raise RuntimeError("安装包路径不安全")
                    archive.unlink()
                loop = asyncio.get_running_loop()
                def progress(snapshot):
                    loop.call_soon_threadsafe(self._download_progress, identifier, cancel, snapshot)
                def download_work():
                    try:
                        download_asset(self.operation["asset_url"], archive, self.ssl_context(), transport=transport, progress=progress, cancel=cancel, timeout_seconds=self.timeout_seconds())
                    except Exception as error:
                        return error
                    return None
                worker = asyncio.create_task(asyncio.to_thread(download_work))
                error = await asyncio.shield(worker)
                if error is not None:
                    raise error
                self._require_running(identifier, cancel)
            await self._finish_downloaded(directory, identifier, cancel, transport)
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
                    self._update(stage="failed", error=str(error), diagnostic_error=error_fields(error))

    def _download_progress(self, identifier, cancel, snapshot):
        if not self.stopping and not cancel.is_set() and self.operation and self.operation["id"] == identifier and not self.operation.get("cancel_requested") and self.operation["stage"] == "checking":
            self._update(download=snapshot)

    def _dependency_progress(self, identifier, cancel, snapshot):
        if not self.stopping and not cancel.is_set() and self.operation and self.operation["id"] == identifier and not self.operation.get("cancel_requested") and self.operation["stage"] == "checking":
            self._update(dependency_download=snapshot)

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

    @operation_context
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
            if transport is not None:
                state, _version = await self._prepare_work(identifier, cancel, probe_release_zip, asset_url, transport)
                if state == "absent":
                    raise RuntimeError("安装包没有可读取的版本，已取消下载")
            self._update(repo=release["repo"])
            archive = directory / "plugin.zip"
            loop = asyncio.get_running_loop()
            def progress(snapshot):
                loop.call_soon_threadsafe(self._download_progress, identifier, cancel, snapshot)
            def download_work():
                try:
                    download_asset(asset_url, archive, self.ssl_context(), transport=transport, progress=progress, cancel=cancel, timeout_seconds=self.timeout_seconds())
                except Exception as error:
                    return error
                return None
            worker = asyncio.create_task(asyncio.to_thread(download_work))
            error = await asyncio.shield(worker)
            if error is not None:
                raise error
            self._require_running(identifier, cancel)
            await self._finish_downloaded(directory, identifier, cancel, transport)
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
                    self._update(stage="failed", error=str(error), diagnostic_error=error_fields(error))

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
                    self._update(stage="failed", error=str(error), diagnostic_error=error_fields(error))
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
                    self._update(stage="unknown" if after_uninstall else "failed", error=str(error), diagnostic_error=error_fields(error))
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
                elif kind == "start" and op.get("request_id") and not op.get("saw_start"):
                    self._observe("awaiting", "等待原生确认", "complete")
                    self._update(stage="installing", saw_start=True)
                elif kind == "finish" and op.get("request_id"):
                    if op.get("saw_start"):
                        self._update(saw_finish=True)
                    else:
                        self._update(stage="unknown", error="缺少本次安装开始事件，无法关联完成证据")
                elif kind == "progress" and op.get("saw_start") and not op.get("saw_finish") and not op.get("evidence_conflict"):
                    steps = {"downloading": "读取本地安装包", "uninstalling_previous": "卸载旧版", "installing_plugin": "解压安装包", "download_remote": "检查额外依赖"}
                    step = steps.get(value.rsplit(".", 1)[-1])
                    if step:
                        self._observe("installing", "Loader 安装", detail=step)
                elif kind in ("install_error", "confirm_returned") and sha256 == op.get("request_id") and not op.get("evidence_conflict"):
                    if kind == "install_error" or not op.get("saw_finish"):
                        observed = next((row.get("detail") for row in op.get("observations", []) if row["id"] == "installing"), None)
                        self._update(stage="failed", error=value[:1024] or f"Loader 在{observed or '安装'}阶段结束，未报告安装完成。")
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
                    waited = "submitted_at" in op and time.time() - op["submitted_at"] > 120
                    can_verify = op.get("saw_start") and op.get("saw_finish") and not op.get("evidence_conflict") and not op.get("uninstall_blocked")
                    if can_verify:
                        self._observe("verification", "安装文件核验")
                        matches = await self._work(verify_installed, self.home, op["package"], op["previous_stamp"])
                        if matches:
                            self._update(stage="verified", error=None)
                        else:
                            self._update(stage="failed", error="Loader 流程已结束，但安装文件校验失败（包括额外依赖）。旧版可能已卸载，请检查 Decky。")
                    elif waited:
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

    async def delete_package(self, identifier: str):
        async with self.lock:
            op = self.operation
            if not op or op["id"] != identifier:
                raise RuntimeError("任务已变化")
            if op["stage"] not in TERMINAL:
                raise RuntimeError("安装尚未结束，不能删除安装包")
            if op.get("artifact_deleted"):
                return self._public()
            directory = Path(op["directory"])
            if directory.is_symlink() or not directory.is_dir():
                raise RuntimeError("安装目录无效")
            archives = [directory / name for name in ("plugin.zip", "source.zip")]
            for archive in archives:
                if archive.is_symlink() or archive.exists() and not archive.resolve().is_relative_to(directory.resolve()):
                    raise RuntimeError("安装包路径无效")
                if archive.exists():
                    info = archive.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise RuntimeError("安装包不是普通文件")
            for archive in archives:
                if archive.exists():
                    archive.unlink()
            self._update(artifact_deleted=True)
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
        for task in (self.task, self.validation, self.cleanup):
            if task and not task.done() and task is not asyncio.current_task():
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
