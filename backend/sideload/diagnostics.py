"""Private, bounded, allowlisted diagnostics. Free-form payloads never reach disk."""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
import uuid
import zipfile
from pathlib import Path
from sideload.diagnostic_context import CONTEXT, ERROR_CODES, ERROR_TYPES, MODULES, error_fields
from sideload.diagnostic_snapshot import PAGES, sanitize_snapshot, version
from sideload.device_info import environment_snapshot

LINE_BYTES = 8 * 1024
LOG_BYTES = 1024 * 1024
PACKAGE_BYTES = 6 * 1024 * 1024
BATCH_BYTES = 8 * 1024
LOG_NAMES = ("active.jsonl", "history-1.jsonl", "history-2.jsonl", "history-3.jsonl")
LOG_MARKER = b'{"event":"log_header","format":1}\n'
ZIP_MARKER = b"decky-sideloader-diagnostics-v1\n"
EXPORT_NAME = re.compile(r"^diagnostics-[0-9]{19}-[a-f0-9]{32}\.zip$")
TEMP_NAME = re.compile(r"^building-[a-f0-9]{32}\.tmp$")
EVENTS = {"startup", "shutdown", "rpc_start", "rpc_end", "rpc_failed", "network_attempt", "list_summary", "check_summary", "operation_stage", "download_stage", "dependency_stage", "backup_cleanup", "ownership_stage", "loader_read", "frontend_drop", "sdk_log", "frontend_build", "ui_page", "ui_action", "frontend_error", "diagnostic_export"}
RPCS = {"ping", "get_operation", "get_app_settings", "get_repository_map_status", "update_repository_map", "set_network_mode", "set_check_notifications", "set_show_home_task_notice", "set_delete_package_after_install", "set_download_timeout", "set_skipped_release", "set_auto_check", "list_sideload_plugins", "set_binding", "clear_binding", "search_repositories", "probe_repository", "list_releases", "get_plugin_releases", "check_updates", "prepare_install", "continue_prepared", "retry_download", "submit_prepared", "claim_uninstall", "complete_uninstall", "record_install_event", "close_operation", "cancel_operation", "dismiss_operation", "delete_install_package", "delete_backups", "authorize_plugin_mutation", "begin_store_ownership", "ownership_native_step"}
ACTIONS = {"back", "checkUpdates", "newInstall", "settings", "binding", "manageBinding", "saveBinding", "unlink", "replace", "reinstall", "uninstall", "search", "manualRepo", "returnSearch", "readReleases", "refreshReleases", "installPackage", "changePlugin", "changeRepo", "releaseNotes", "licenses", "reloadSettings", "dismissTask", "muteTaskNotice", "viewTask", "refreshTask", "refreshRecognition", "deletePackage", "confirmInstall", "shareDiagnostics", "copyDiagnostics", "stopDiagnostics", "diagnosticDetails", "exportDiagnostics", "takeover", "returnStore", "readOwnership", "cancelRequest", "finishTracking", "skipRelease", "continueInstall", "retryDownload", "maintenance", "network", "deleteBackups", "updateRepositoryMap"}
ENUMS = {
    "rpc": RPCS,
    "stage": {"checking", "backup", "ready", "uninstalling", "validating", "awaiting", "installing", "verified", "failed", "unknown", "cancelled", "closed"},
    "status": {"ok", "error", "running", "connecting", "downloading", "complete", "failed", "success", "cancelled", "unknown"},
    "source": {"manual", "scheduled", "live", "cache", "registry", "unavailable"},
    "route": {"direct", "gh-proxy", "ghfast"}, "mode": {"direct", "auto", "gh-proxy", "ghfast"},
    "errorType": ERROR_TYPES | {"Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "URIError", "EvalError"},
    "errorCode": ERROR_CODES, "module": MODULES, "page": PAGES, "action": ACTIONS | {"takeover", "return"},
    "phase": {"read", "prepared", "writing", "confirming", "complete", "active", "released"},
    "kind": {"api", "raw", "asset", "store", "other"}, "origin": {"render", "action", "global", "unhandled", "toast", "icon"},
}
COUNTS = {"count", "errors", "updates", "durationMs", "attempt", "httpStatus", "dropped", "level", "line", "column", "errno", "index", "received", "total", "deleted", "bytes", "skipped", "failed", "sequence", "timestampMs"}
BOOLS = {"ok", "loaderOk", "catalogOk", "expiredOnly"}
IDS = {"operationId", "requestId", "parentId", "sessionId", "clientId", "pluginId", "repoId", "releaseId", "resourceId", "ownershipId"}
FRONT_FIELDS = {
    "loader_read": ({"event", "ok", "count", "durationMs"}, {"clientId"}),
    "frontend_build": ({"event", "fingerprint", "version", "clientId"}, set()),
    "ui_page": ({"event", "page", "clientId"}, {"pluginId", "releaseId"}),
    "ui_action": ({"event", "page", "action", "clientId"}, set()),
    "frontend_error": ({"event", "page", "origin", "errorType", "clientId"}, {"line", "column"}),
}


def sanitize(event, fields):
    """Select known enums/numbers, never stringify arbitrary input or exceptions."""
    if type(event) is not str or event not in EVENTS:
        return None
    row = {"event": event}
    if type(fields) is not dict:
        return row
    for key, allowed in ENUMS.items():
        value = fields.get(key)
        if type(value) is str and len(value) <= 64 and value in allowed:
            row[key] = value
    for key in COUNTS:
        value = fields.get(key)
        if type(value) is int and 0 <= value <= 2**53 - 1:
            row[key] = value
    for key in IDS:
        value = fields.get(key)
        if type(value) is str and re.fullmatch(r"[a-f0-9]{32}", value): row[key] = value
    for key in BOOLS:
        if type(fields.get(key)) is bool: row[key] = fields[key]
    if type(fields.get("fingerprint")) is str and re.fullmatch(r"[a-f0-9]{64}", fields["fingerprint"]): row["fingerprint"] = fields["fingerprint"]
    if version(fields.get("version")) is not None: row["version"] = fields["version"]
    if type(fields.get("frames")) is list: row["frames"] = sanitize_snapshot({"frames": fields["frames"]})["frames"]
    if type(fields.get("operation")) is dict: row["operation"] = sanitize_snapshot(fields["operation"])
    return row


def encoded(row):
    return (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def validate_batch(events, dropped):
    if type(events) is not list or len(events) > 16 or type(dropped) is not int or not 0 <= dropped <= 65535:
        raise RuntimeError("诊断批次无效")
    clean = []
    for row in events:
        if type(row) is not dict or type(row.get("event")) is not str or row["event"] not in FRONT_FIELDS:
            raise RuntimeError("诊断事件无效")
        required, optional = FRONT_FIELDS[row["event"]]
        if not required <= set(row) or not set(row) <= required | optional:
            raise RuntimeError("诊断事件无效")
        value = sanitize(row["event"], row)
        if value != row:
            raise RuntimeError("诊断字段无效")
        clean.append(value)
    if len(encoded({"events": clean, "dropped": dropped})) > BATCH_BYTES:
        raise RuntimeError("诊断批次过大")
    return clean


def _directory(path, *, create=True):
    """Walk using directory descriptors; reject links in every path component."""
    path = Path(path)
    if not path.is_absolute() or path == Path("/"):
        raise OSError("diagnostics directory unavailable")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in ("", ".", ".."):
                raise OSError("invalid directory")
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if os.fstat(fd).st_uid != os.getuid():
            raise OSError("directory owner mismatch")
        os.fchmod(fd, 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open(fd, name, flags=os.O_RDONLY):
    file = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
    info = os.fstat(file)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        os.close(file)
        raise OSError("unsafe diagnostic file")
    return file


def _owned(fd, name, marker):
    try:
        file = _open(fd, name)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(file)
        if os.read(file, len(marker)) != marker:
            raise OSError("unrecognized diagnostic file")
        os.fchmod(file, 0o600)
        return info
    finally:
        os.close(file)


def _remove(fd, name, marker):
    info = _owned(fd, name, marker)
    if info is not None:
        current = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_nlink) != (info.st_dev, info.st_ino, 1):
            raise OSError("diagnostic file changed")
        os.unlink(name, dir_fd=fd)


def _same_file(file, info):
    current = os.fstat(file)
    if (current.st_dev, current.st_ino, current.st_nlink) != (info.st_dev, info.st_ino, 1):
        raise OSError("diagnostic file changed")


def _remove_created(fd, name, info):
    """Clean our incomplete write, including a disk-full failure before its marker."""
    try:
        current = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino, current.st_nlink) != (info.st_dev, info.st_ino, 1):
        raise OSError("temporary diagnostic file changed")
    os.unlink(name, dir_fd=fd)


def _publish(fd, source, destination):
    """Atomically rename without overwrite or a transient hardlink (Linux/macOS)."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    flag = 1  # Linux RENAME_NOREPLACE.
    if rename is None:
        rename = getattr(libc, "renameatx_np", None)
        flag = 4  # macOS RENAME_EXCL, used by local validation.
    if rename is None:
        raise OSError("exclusive diagnostic publish unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(fd, source.encode("ascii"), fd, destination.encode("ascii"), flag) != 0:
        raise OSError(ctypes.get_errno(), "exclusive diagnostic publish failed")


def _version(value):
    return value if type(value) is str and re.fullmatch(r"v?\d{1,4}\.\d{1,4}\.\d{1,4}(?:-(?:pre|debug|beta|alpha)(?:\.?\d{1,4})?)?", value) else "unavailable"


class _SDKHandler(logging.Handler):
    def __init__(self, diagnostics):
        super().__init__()
        self.diagnostics = diagnostics

    def emit(self, record):
        # Only controlled source locations and classified errors, never formatted text.
        fields = error_fields(record.exc_info[1]) if record.exc_info and isinstance(record.exc_info[1], BaseException) else {}
        self.diagnostics.record("sdk_log", module=record.module, line=record.lineno, level=record.levelno, **fields)


class Diagnostics:
    def __init__(self, log_dir, user_home, versions=None):
        self.log_dir = Path(log_dir) / "diagnostics" if log_dir else None
        self.user_home = Path(user_home) if user_home else None
        self.versions = {key: _version((versions or {}).get(key)) for key in ("sdk", "backend", "frontend", "loader")}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.export_task = None
        self.share_phase = None
        self.state = {"state": "idle"}
        self.health = {"dropped": 0}
        self.session_id = uuid.uuid4().hex
        self.sequence = 0
        self.builds = {}
        self.sdk_handler = None
        self.sdk_logger = None
        self._recover_exports()

    def _export_directory(self):
        if self.user_home is None or not self.user_home.is_absolute() or self.user_home == Path("/root"):
            raise OSError("SDK user home unavailable")
        return self.user_home / "Downloads" / "decky-sideloader-diagnostics"

    def _recover_exports(self):
        # Restart cleanup only inspects the dedicated directory if it already exists.
        try:
            fd = _directory(self._export_directory(), create=False)
            try:
                exports = self._exports(fd)
                while len(exports) > 3:
                    _remove(fd, exports.pop(0), ZIP_MARKER)
            finally:
                os.close(fd)
        except OSError:
            pass

    def attach_sdk(self, logger, exact_file):
        # Only the isolated plugin SDK's exact file handler is removed. Never reconfigure root.
        if not isinstance(logger, logging.Logger) or type(exact_file) is not str or not exact_file:
            return
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler) and handler.baseFilename == os.path.abspath(exact_file):
                logger.removeHandler(handler)
                handler.close()
        self.sdk_logger = logger
        self.sdk_handler = _SDKHandler(self)
        logger.addHandler(self.sdk_handler)

    def record(self, event, **fields):
        try:
            if self.log_dir is None or self.stop.is_set():
                return
            row = sanitize(event, {**CONTEXT.get(), **fields})
            if row is None:
                return
            row["timestamp"] = int(time.time())
            row["timestampMs"] = int(time.time() * 1000)
            row["sessionId"] = self.session_id
            with self.lock:
                self.sequence += 1
                row["sequence"] = self.sequence
            data = encoded(row)
            if len(data) > LINE_BYTES:
                return
            with self.lock:
                if self.stop.is_set():
                    return
                fd = _directory(self.log_dir)
                try:
                    for name in LOG_NAMES:
                        info = _owned(fd, name, LOG_MARKER)
                        if info and info.st_size > LOG_BYTES:
                            _remove(fd, name, LOG_MARKER)
                    info = _owned(fd, LOG_NAMES[0], LOG_MARKER)
                    if info and info.st_size + len(data) > LOG_BYTES:
                        _remove(fd, LOG_NAMES[-1], LOG_MARKER)
                        for i in range(2, -1, -1):
                            if _owned(fd, LOG_NAMES[i], LOG_MARKER):
                                os.rename(LOG_NAMES[i], LOG_NAMES[i + 1], src_dir_fd=fd, dst_dir_fd=fd)
                        info = None
                    file = _open(fd, LOG_NAMES[0], os.O_WRONLY | os.O_APPEND | (os.O_CREAT | os.O_EXCL if info is None else 0))
                    created = os.fstat(file) if info is None else None
                    try:
                        if info is not None:
                            _same_file(file, info)
                        os.fchmod(file, 0o600)
                        with os.fdopen(file, "ab", closefd=False) as stream:
                            if info is None:
                                stream.write(LOG_MARKER)
                            stream.write(data)
                    except Exception:
                        if created is not None:
                            _remove_created(fd, LOG_NAMES[0], created)
                        raise
                    finally:
                        os.close(file)
                finally:
                    os.close(fd)
            if event == "loader_read":
                self.health["loader"] = sanitize(event, fields)
            elif event == "list_summary":
                self.health["listing"] = sanitize(event, fields)
            elif event == "frontend_build":
                self.health["frontend"] = sanitize(event, fields)
                self.versions["frontend"] = _version(fields.get("version"))
            elif event == "backup_cleanup":
                self.health["cleanup"] = sanitize(event, fields)
        except Exception:
            self.health["dropped"] = min(65535, self.health["dropped"] + 1)

    def record_batch(self, events, dropped=0):
        rows = validate_batch(events, dropped)
        for row in rows:
            self.record(row["event"], **{key: value for key, value in row.items() if key != "event"})
        if dropped:
            self.record("frontend_drop", dropped=dropped)
        return {"accepted": len(rows)}

    def status(self):
        return dict(self.state)

    def _snapshot(self):
        result, bad = {}, 0
        if self.log_dir is None:
            raise OSError("diagnostics unavailable")
        with self.lock:
            fd = _directory(self.log_dir)
            try:
                for name in LOG_NAMES:
                    info = _owned(fd, name, LOG_MARKER)
                    if info is None:
                        continue
                    if info.st_size > LOG_BYTES:
                        bad += 1
                        continue
                    file = _open(fd, name)
                    data = bytearray(LOG_MARKER)
                    try:
                        _same_file(file, info)
                        os.fchmod(file, 0o600)
                        with os.fdopen(file, "rb", closefd=False) as stream:
                            stream.readline(LINE_BYTES + 1)
                            remaining = LOG_BYTES - len(LOG_MARKER)
                            while remaining > 0:
                                raw = stream.readline(min(LINE_BYTES + 1, remaining))
                                if not raw:
                                    break
                                remaining -= len(raw)
                                if len(raw) > LINE_BYTES or not raw.endswith(b"\n"):
                                    bad += 1
                                    # Discard the rest of this physical line within the same file budget.
                                    while remaining > 0 and raw and not raw.endswith(b"\n"):
                                        raw = stream.readline(min(LINE_BYTES + 1, remaining))
                                        remaining -= len(raw)
                                    continue
                                try:
                                    row = json.loads(raw)
                                    clean = sanitize(row.get("event"), row) if type(row) is dict else None
                                    if clean is None:
                                        bad += 1
                                        continue
                                    if type(row.get("timestamp")) is int and 0 <= row["timestamp"] <= 2**31 - 1:
                                        clean["timestamp"] = row["timestamp"]
                                    line = encoded(clean)
                                    if len(data) + len(line) <= LOG_BYTES:
                                        data.extend(line)
                                except (ValueError, UnicodeError, TypeError, RecursionError):
                                    bad += 1
                    finally:
                        os.close(file)
                    result["logs/" + name] = bytes(data)
            finally:
                os.close(fd)
        return result, bad

    def _exports(self, fd):
        exports = []
        with os.scandir(fd) as entries:
            for count, item in enumerate(entries):
                if count >= 512:
                    raise OSError("too many export directory entries")
                if EXPORT_NAME.fullmatch(item.name) or TEMP_NAME.fullmatch(item.name):
                    try:
                        info = _owned(fd, item.name, ZIP_MARKER)
                    except OSError:
                        continue  # Foreign/link files remain untouched, even with a matching name.
                    if info:
                        if TEMP_NAME.fullmatch(item.name) or info.st_size > PACKAGE_BYTES:
                            _remove(fd, item.name, ZIP_MARKER)
                        else:
                            exports.append(item.name)
        return sorted(exports)

    def _build_export(self, summary):
        target = self._export_directory()
        fd = _directory(target)
        temporary = "building-" + uuid.uuid4().hex + ".tmp"
        created = None
        try:
            exports = self._exports(fd)
            while len(exports) > 3:
                _remove(fd, exports.pop(0), ZIP_MARKER)
            entries, bad = self._snapshot()
            health = {"dropped": min(65535, self.health["dropped"])}
            for key, event in (("listing", "list_summary"), ("loader", "loader_read"), ("frontend", "frontend_build"), ("cleanup", "backup_cleanup")):
                value = self.health.get(key)
                if value is not None:
                    health[key] = sanitize(event, value)
            safe_summary = {"versions": self.versions, "snapshot": sanitize("operation_stage", summary), "details": sanitize_snapshot(summary.get("details", {})),
                "builds": sanitize_snapshot(self.builds), "health": health, "invalidLines": min(bad, 65535)}
            environment = environment_snapshot(self.user_home)
            front_environment = safe_summary["details"].get("environment")
            steam_version = front_environment.get("steamClientVersion") if type(front_environment) is dict else None
            if type(steam_version) is int and steam_version > 0:
                environment["steamClientVersion"] = steam_version
            safe_summary["details"]["environment"] = environment
            frontend = health.get("frontend")
            if frontend:
                safe_summary["builds"]["frontend"] = sanitize_snapshot(frontend)
                if self.builds.get("installedFrontend"):
                    safe_summary["builds"]["matchesInstalled"] = frontend.get("fingerprint") == self.builds["installedFrontend"]
            entries["summary.json"] = encoded(safe_summary)
            manifest = {"format": 2, "limits": {"lineBytes": LINE_BYTES, "logFileBytes": LOG_BYTES, "logFiles": 4, "packageBytes": PACKAGE_BYTES, "expandedBytes": PACKAGE_BYTES, "retainedPackages": 3},
                "privacy": "Allowlisted codes, source locations, counts, versions, content fingerprints and pseudonymous identifiers. No original messages, URLs, paths, credentials, configuration files or uploads.",
                "identifiers": "sha256(kind + ':' + value), first 32 hex characters; names/repositories/tags are never exported verbatim",
                "files": [{"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in entries.items()]}
            entries["manifest.json"] = encoded(manifest)
            if sum(map(len, entries.values())) > PACKAGE_BYTES - 65536:
                raise OSError("expanded diagnostics limit")
            file = _open(fd, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            created = os.fstat(file)
            try:
                os.fchmod(file, 0o600)
                with os.fdopen(file, "wb", closefd=False) as stream:
                    stream.write(ZIP_MARKER)
                    # Stored data makes both on-disk and expanded limits explicit.
                    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
                        archive.comment = ZIP_MARKER
                        for name, data in entries.items():
                            if self.stop.is_set():
                                raise OSError("diagnostics stopped")
                            archive.writestr(name, data)
                    stream.flush()
                    os.fsync(file)
                size = os.fstat(file).st_size
                if size > PACKAGE_BYTES or self.stop.is_set():
                    raise OSError("diagnostics package limit or stopped")
            finally:
                os.close(file)
            while len(exports) >= 3:
                _remove(fd, exports.pop(0), ZIP_MARKER)
            name = f"diagnostics-{time.time_ns():019d}-{uuid.uuid4().hex}.zip"
            _publish(fd, temporary, name)
            return {"state": "success", "file": name, "path": str(target / name), "bytes": size}
        finally:
            try:
                if created is not None:
                    _remove_created(fd, temporary, created)
            finally:
                os.close(fd)

    async def export(self, summary, *, _for_share=False):
        if self.share_phase is not None and not (_for_share and self.share_phase == "preparing"):
            raise RuntimeError("诊断下载正在进行，请先停止下载服务")
        if self.stop.is_set() or self.export_task is not None and not self.export_task.done():
            raise RuntimeError("诊断导出正在进行或插件正在停止")
        self.state = {"state": "exporting"}
        async def work():
            try:
                self.state = await asyncio.to_thread(self._build_export, summary)
            except Exception as error:
                self.record("diagnostic_export", status="failed", **error_fields(error))
                self.state = {"state": "error", "error": "诊断导出失败，请检查目录权限和可用空间后重试"}
            return self.status()
        self.export_task = asyncio.create_task(work())
        return await asyncio.shield(self.export_task)

    async def close(self):
        self.stop.set()
        if self.export_task:
            await asyncio.shield(self.export_task)
        if self.sdk_logger and self.sdk_handler:
            self.sdk_logger.removeHandler(self.sdk_handler)
            self.sdk_handler.close()
