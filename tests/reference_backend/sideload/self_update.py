"""Self-update authorization is separate from ordinary plugin installation.

RPC contract: check -> cached release/status; prepare(tag, sha256) -> downloading;
submit(id, frontend identity) -> one awaiting native handoff; event -> evidence;
status(frontend identity) -> reconcile the new instance, never replay installation.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid
from zipfile import ZipFile

import decky
from sideload.diagnostic_snapshot import build_snapshot, read_json
from sideload.github_meta import compare_versions
from sideload.http_client import _ssl_context
from sideload.install_ops import download_asset, file_sha256, inspect_archive, plugin_stamp, verify_installed
from sideload.paths import _settings_path
from sideload.persist import load_settings, save_settings
from sideload.rpc import _observed_rpc
from sideload.scan import read_plugins_dir

REPOSITORY = "ZacharyQin/decky-sideloader"
NAME, FOLDER = "Sideloader", "decky-sideloader"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ACTIVE = {"downloading", "ready", "awaiting", "installing", "verifying", "unknown"}
HASH = re.compile(r"[a-f0-9]{64}")
VERSION = re.compile(r"v?((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))")


def require_no_self_update(plugin):
    record = getattr(plugin, "_self_record", {})
    if getattr(plugin, "_self_preparing", False) or (record.get("operation") or {}).get("stage") in ACTIVE:
        raise RuntimeError("请先完成或取消 Sideloader 自更新")


def select_release(payload, current):
    if not isinstance(payload, dict) or payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise RuntimeError("自更新只接受正式发布")
    match = VERSION.fullmatch(payload.get("tag_name", ""))
    if not match or compare_versions(match[1], current) is None:
        raise RuntimeError("自更新版本无法比较")
    tag, version = match[0], match[1]
    filename = f"{FOLDER}-{version}.zip"
    assets = [asset for asset in payload.get("assets", []) if isinstance(asset, dict) and asset.get("name") == filename]
    if len(assets) != 1:
        raise RuntimeError("正式发布缺少唯一的 Sideloader 安装包")
    asset = assets[0]
    url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}"
    digest = asset.get("digest", "")
    if (asset.get("browser_download_url") != url or not isinstance(digest, str) or not digest.startswith("sha256:") or
            not HASH.fullmatch(digest[7:]) or type(asset.get("id")) is not int or asset["id"] <= 0 or
            type(asset.get("size")) is not int or not 0 < asset["size"] <= 128 * 1024 * 1024):
        raise RuntimeError("自更新安装包来源或校验信息无效")
    return {"repo": REPOSITORY, "tag": tag, "version": version, "title": str(payload.get("name") or tag)[:256],
            "body": str(payload.get("body") or "")[:20000], "published_at": str(payload.get("published_at") or "")[:64],
            "prerelease": False, "has_zip": True, "comparison": compare_versions(version, current),
            "zips": [{"name": filename, "url": url}], "sha256": digest[7:], "asset_id": asset["id"], "size": asset["size"]}


def inspect_self_package(archive, home, release):
    installed = read_plugins_dir(str(home / "plugins"))
    matches = [row for row in installed if row["name"] == NAME or row["folder"] == FOLDER]
    if len(matches) != 1 or (matches[0]["name"], matches[0]["folder"], matches[0].get("package_name")) != (NAME, FOLDER, FOLDER):
        raise RuntimeError("Sideloader 运行身份不一致")
    package = inspect_archive(archive, matches[0], installed, NAME, FOLDER,
                              {"name": NAME, "package_name": FOLDER}, updating_self=True)
    if package["sha256"] != release["sha256"] or archive.stat().st_size != release["size"] or package["version"] != release["version"]:
        raise RuntimeError("自更新安装包与发布信息不一致")
    with ZipFile(archive) as source:
        build = json.loads(source.read(f"{FOLDER}/dist/build.json"))
        if (build.get("version") != package["version"] or build.get("bundle") != package["hashes"]["dist/index.js"] or
                not isinstance(build.get("fingerprint"), str) or not HASH.fullmatch(build["fingerprint"])):
            raise RuntimeError("自更新前端构建身份无效")
        digest = hashlib.sha256()
        files = [("main.py", f"{FOLDER}/main.py")]
        prefix = f"{FOLDER}/py_modules/sideload/"
        files.extend(("sideload/" + name[len(prefix):], name) for name in sorted(source.namelist())
                     if name.startswith(prefix) and name.endswith(".py") and "/" not in name[len(prefix):])
        for relative, name in files:
            digest.update(relative.encode() + b"\0" + source.read(name) + b"\0")
    return {**package, "frontend": build["fingerprint"], "backend": digest.hexdigest()}


class SelfUpdateAPI:
    def _self_update_init(self):
        if hasattr(self, "_self_record"):
            return
        self._self_root = Path(decky.DECKY_PLUGIN_RUNTIME_DIR) / "self-update"
        self._self_file = self._self_root / "state.json"
        self._self_boot = uuid.uuid4().hex
        self._self_current = decky.DECKY_PLUGIN_VERSION
        self._self_builds = build_snapshot(decky.DECKY_PLUGIN_DIR)
        self._self_stopping = False
        self._self_preparing = False
        self._self_check_task = self._self_download_task = None
        self._self_cancel = threading.Event()
        self._self_lock = asyncio.Lock()
        self._self_record = {"schema": 1, "check": None, "operation": None}
        self._self_storage_error = None
        try:
            if self._self_root.is_symlink():
                raise RuntimeError("自更新记录不可用")
            record = read_json(self._self_file, 2 * 1024 * 1024)
            op = record.get("operation")
            if (record.get("schema") != 1 or record.get("check") is not None and not isinstance(record["check"], dict) or
                    op is not None and (not isinstance(op, dict) or not re.fullmatch(r"[a-f0-9]{32}", op.get("id", "")) or
                                        op.get("stage") not in ACTIVE | {"completed", "cancelled", "failed"})):
                raise RuntimeError("自更新记录不可用")
            self._self_record = record
            if op and op["stage"] == "downloading":
                self._self_change(stage="failed", error="自更新准备被重载中断，请重新下载")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, RuntimeError):
            self._self_storage_error = "自更新记录不可用"

    def _self_save(self):
        save_settings(str(self._self_file), self._self_record)

    def _self_change(self, **changes):
        previous = self._self_record
        self._self_record = {**previous, "operation": {**previous["operation"], **changes}}
        try:
            self._self_save()
        except BaseException:
            self._self_record = previous
            raise

    def _self_require_idle(self):
        if self._self_stopping or self._self_storage_error:
            raise RuntimeError(self._self_storage_error or "插件正在停止，请重新打开后重试")
        if Path(decky.DECKY_PLUGIN_DIR).name != FOLDER or decky.DECKY_PLUGIN_NAME != NAME:
            raise RuntimeError("Sideloader 运行身份不一致")
        if (self._self_record.get("operation") or {}).get("stage") in ACTIVE:
            raise RuntimeError("请先完成或取消 Sideloader 自更新")
        if not hasattr(self, "_flow") and (Path(decky.DECKY_PLUGIN_RUNTIME_DIR) / "installations/operation.json").is_file():
            self._install_flow()
        operation = getattr(getattr(self, "_flow", None), "operation", None)
        if operation and operation["stage"] not in {"failed", "closed", "cancelled"}:
            raise RuntimeError("请先结束当前插件安装任务")
        if (getattr(self, "_check_lock", None) and self._check_lock.locked() or
                getattr(self, "_repository_map_update", None) is not None or
                getattr(self, "_catalog_worker", None) is not None):
            raise RuntimeError("正在检查版本，请稍后更新 Sideloader")

    def _self_frontend_matches(self, frontend):
        return (isinstance(frontend, dict) and frontend.get("version") == self._self_current and
                frontend.get("fingerprint") == self._self_builds.get("installedFrontend") and
                isinstance(frontend.get("fingerprint"), str) and HASH.fullmatch(frontend["fingerprint"]) is not None)

    def _self_public(self, frontend=None):
        op = self._self_record.get("operation")
        public = {key: value for key, value in op.items() if key in
                  {"id", "stage", "error", "release", "request_id", "saw_start", "progress", "cancel_requested", "artifact", "package"}} if op else None
        if public and public.get("package"):
            public["package"] = {key: value for key, value in public["package"].items() if key in {"name", "folder", "version", "sha256", "warnings"}}
        return {"current": self._self_current, "check": self._self_record["check"], "operation": public,
                "checking": self._self_check_task is not None and not self._self_check_task.done(),
                "preparing": self._self_preparing, "frontend_compatible": self._self_frontend_matches(frontend),
                "error": self._self_storage_error}

    @_observed_rpc
    async def get_self_update_status(self, frontend: dict | None = None):
        self._self_update_init()
        async with self._self_lock:
            op = self._self_record.get("operation")
            if op and op["stage"] in {"awaiting", "installing", "verifying", "unknown"} and not self._self_storage_error:
                package = op.get("package")
                if (package and op.get("from_boot") != self._self_boot and self._self_current == package.get("version") and
                        self._self_builds.get("backend") == package.get("backend")):
                    matched = await asyncio.to_thread(verify_installed, Path(decky.DECKY_HOME), package, op.get("previous_stamp"))
                    if matched and self._self_frontend_matches(frontend) and frontend["fingerprint"] == package.get("frontend"):
                        self._self_change(stage="completed", error=None)
                        if load_settings(_settings_path())["deletePackageAfterInstall"]:
                            self._self_remove_package()
                    elif matched and op["stage"] != "verifying":
                        self._self_change(stage="verifying", error=None)
                elif op["stage"] in {"awaiting", "installing"} and time.time() - op.get("submitted_at", 0) > 120:
                    self._self_change(stage="unknown", error="自更新结果尚未确认，不会重复安装")
            return self._self_public(frontend)

    @_observed_rpc
    async def check_self_update(self):
        self._self_update_init()
        if self._self_stopping or self._self_storage_error:
            raise RuntimeError(self._self_storage_error or "插件正在停止，请重新打开后重试")
        if (self._self_record.get("operation") or {}).get("stage") in ACTIVE:
            raise RuntimeError("请先完成或取消 Sideloader 自更新")
        if self._self_check_task is None:
            task = asyncio.create_task(self._self_check())
            self._self_check_task = task
            def finished(done):
                if self._self_check_task is done:
                    self._self_check_task = None
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)
        await asyncio.shield(self._self_check_task)
        return self._self_public()

    async def _self_check(self):
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            fetch = self._query_fetch()
            payload = await asyncio.to_thread(fetch, LATEST_URL, accept="application/vnd.github+json", max_bytes=256 * 1024)
            release = select_release(payload, self._self_current)
            check = {"status": {1: "update", 0: "current", -1: "ahead"}[release["comparison"]],
                     "release": release, "checked_at": checked_at, "error": None}
        except Exception as error:
            check = {"status": "error", "release": (self._self_record.get("check") or {}).get("release"),
                     "checked_at": checked_at, "error": str(error)[:1024]}
        if not self._self_stopping:
            previous = self._self_record
            self._self_record = {**previous, "check": check}
            try:
                self._self_save()
            except BaseException:
                self._self_record = previous
                raise

    @_observed_rpc
    async def prepare_self_update(self, tag: str, sha256: str):
        self._self_update_init()
        async with self._ownership_lock():
            self._self_require_idle()
            if self._self_preparing:
                raise RuntimeError("请先完成或取消 Sideloader 自更新")
            self._self_preparing = True
        try:
            await self.check_self_update()
            check = self._self_record["check"]
            release = check.get("release")
            if check["status"] != "update" or not release or (release["tag"], release["sha256"]) != (tag, sha256):
                raise RuntimeError("更新版本已变化，请重新检查")
            async with self._ownership_lock():
                self._self_require_idle()
                old = self._self_record.get("operation")
                if old:
                    self._self_remove_package()
                identifier = uuid.uuid4().hex
                directory = self._self_root / identifier
                directory.mkdir(parents=True, mode=0o700)
                self._self_cancel = threading.Event()
                op = {"id": identifier, "stage": "downloading", "release": release, "package": None,
                      "artifact": f"file://{directory / 'plugin.zip'}", "request_id": None, "saw_start": False,
                      "error": None, "cancel_requested": False, "from_boot": self._self_boot,
                      "from_version": self._self_current, "from_backend": self._self_builds.get("backend"),
                      "previous_stamp": plugin_stamp(Path(decky.DECKY_HOME), FOLDER)}
                previous = self._self_record
                self._self_record = {**previous, "operation": op}
                try:
                    self._self_save()
                except BaseException:
                    self._self_record = previous
                    directory.rmdir()
                    raise
                self._self_download_task = asyncio.create_task(self._self_download(identifier))
                self._self_download_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
                return self._self_public()
        finally:
            self._self_preparing = False

    async def _self_download(self, identifier):
        op = self._self_record["operation"]
        archive = self._self_root / identifier / "plugin.zip"
        loop = asyncio.get_running_loop()
        def progress(value):
            def apply():
                if self._self_record["operation"]["id"] == identifier and self._self_record["operation"]["stage"] == "downloading":
                    self._self_change(progress=value)
            loop.call_soon_threadsafe(apply)
        try:
            settings = load_settings(_settings_path())
            await asyncio.to_thread(download_asset, op["release"]["zips"][0]["url"], archive, _ssl_context(),
                                    transport=self._transport(), progress=progress, cancel=self._self_cancel,
                                    timeout_seconds=settings["downloadTimeoutMinutes"] * 60)
            package = await asyncio.to_thread(inspect_self_package, archive, Path(decky.DECKY_HOME), op["release"])
            if self._self_cancel.is_set() or self._self_stopping:
                self._self_change(stage="cancelled", error=None)
                self._self_remove_package()
            else:
                self._self_change(stage="ready", package=package, error=None)
        except Exception as error:
            cancelled = self._self_cancel.is_set() or self._self_stopping
            self._self_change(stage="cancelled" if cancelled else "failed", error=None if cancelled else str(error)[:1024])
            self._self_remove_package()

    def _self_remove_package(self):
        op = self._self_record.get("operation")
        if not op:
            return
        directory = self._self_root / op["id"]
        if directory.is_symlink():
            raise RuntimeError("自更新记录不可用")
        archive = directory / "plugin.zip"
        if archive.is_symlink():
            raise RuntimeError("自更新记录不可用")
        archive.unlink(missing_ok=True)
        if directory.exists():
            directory.rmdir()

    @_observed_rpc
    async def submit_self_update(self, identifier: str, frontend: dict):
        self._self_update_init()
        async with self._ownership_lock(), self._self_lock:
            op = self._self_record.get("operation")
            if self._self_stopping or not op or op["id"] != identifier or op["stage"] != "ready" or op.get("cancel_requested"):
                raise RuntimeError("自更新任务尚未准备好或已提交")
            if (not self._self_frontend_matches(frontend) or self._self_current != op["from_version"] or
                    self._self_builds.get("backend") != op.get("from_backend")):
                raise RuntimeError("Sideloader 运行身份不一致")
            archive = self._self_root / identifier / "plugin.zip"
            if archive.is_symlink() or not archive.is_file() or plugin_stamp(Path(decky.DECKY_HOME), FOLDER) != op["previous_stamp"]:
                raise RuntimeError("自更新安装包或已安装程序已变化")
            if await asyncio.to_thread(file_sha256, archive) != op["package"]["sha256"]:
                raise RuntimeError("自更新安装包或已安装程序已变化")
            if self._self_stopping:
                raise RuntimeError("插件正在停止，请重新打开后重试")
            self._self_change(stage="awaiting", submitted_at=time.time(), from_boot=self._self_boot, artifact=f"file://{archive}")
            return self._self_public(frontend)

    @_observed_rpc
    async def cancel_self_update(self, identifier: str):
        self._self_update_init()
        async with self._self_lock:
            op = self._self_record.get("operation")
            if not op or op["id"] != identifier:
                raise RuntimeError("自更新任务已变化")
            if op["stage"] in {"downloading", "ready"}:
                self._self_change(cancel_requested=True)
                self._self_cancel.set()
                task = self._self_download_task
                if task and not task.done():
                    await asyncio.shield(task)
                else:
                    self._self_change(stage="cancelled", error=None)
                    self._self_remove_package()
            elif op["stage"] in {"awaiting", "unknown"} and not op.get("saw_start") and op.get("request_id"):
                self._self_change(cancel_requested=True)
            else:
                raise RuntimeError("已交给 Loader，不能取消正在替换的程序")
            return self._self_public()

    @_observed_rpc
    async def record_self_update_event(self, identifier: str, kind: str, value: str = "", evidence: str = ""):
        self._self_update_init()
        async with self._self_lock:
            op = self._self_record.get("operation")
            if not op or op["id"] != identifier or op["stage"] not in {"awaiting", "installing", "unknown"}:
                return self._self_public()
            if kind == "prompt" and value:
                if evidence != op["package"]["sha256"] or op.get("request_id") not in (None, value):
                    self._self_change(stage="unknown", error="自更新确认请求不匹配，不会重复安装", evidence_conflict=True)
                elif not op.get("evidence_conflict"):
                    self._self_change(request_id=value)
            elif kind == "start" and op.get("request_id") and not op.get("evidence_conflict"):
                self._self_change(stage="installing", saw_start=True)
            elif kind == "cancelled" and evidence == op.get("request_id") and not op.get("saw_start"):
                self._self_change(stage="cancelled", error=None)
                self._self_remove_package()
            elif kind == "not_dispatched" and not op.get("request_id") and not op.get("saw_start"):
                self._self_change(stage="cancelled", error=None)
                self._self_remove_package()
            elif kind in {"install_error", "confirm_returned"} and evidence == op.get("request_id") and not op.get("evidence_conflict"):
                self._self_change(stage="failed", error=value[:1024] or "原生安装已结束，但新版尚未启动")
            elif kind in {"dispatch_error", "cancel_unknown"}:
                self._self_change(stage="unknown", error="自更新结果尚未确认，不会重复安装")
            return self._self_public()

    async def _self_update_unload(self):
        self._self_stopping = True
        self._self_cancel.set()
        tasks = [task for task in (self._self_check_task, self._self_download_task) if task is not None]
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        async with self._self_lock:
            pass
