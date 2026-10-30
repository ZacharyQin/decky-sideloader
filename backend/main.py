"""Decky host adapter. Business rules and durable state belong to sideloader-core."""
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import uuid

import decky

PROTOCOL_VERSION = 1
MAX_FRAME = 32 * 1024 * 1024
RPC_METHODS = (
    "ping", "receive_native_management", "get_app_settings", "set_network_mode",
    "set_check_notifications", "set_show_home_task_notice", "set_delete_package_after_install",
    "set_download_timeout", "set_auto_check", "get_repository_map_status", "update_repository_map",
    "get_open_source_notices", "list_sideload_plugins", "set_binding", "clear_binding",
    "authorize_plugin_mutation", "begin_store_ownership", "ownership_native_step",
    "search_repositories", "probe_repository", "list_releases", "get_plugin_releases",
    "set_skipped_release", "check_updates", "prepare_install", "get_operation",
    "continue_prepared", "retry_download", "submit_prepared", "claim_uninstall",
    "complete_uninstall", "record_install_event", "cancel_operation", "dismiss_operation",
    "close_operation", "delete_install_package", "delete_backups", "get_self_update_status",
    "check_self_update", "prepare_self_update", "submit_self_update", "cancel_self_update",
    "record_self_update_event", "get_diagnostics_addresses", "get_diagnostics_share",
    "start_diagnostics_share", "stop_diagnostics_share", "record_diagnostic_events",
    "get_diagnostics_status", "export_diagnostics",
)
_HASH = re.compile(r"[a-f0-9]{64}")
_STOPPED = "Rust 后端已停止；未确认的操作不会自动重试，请重新加载插件后核对结果"


def _open_regular(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("Rust 后端文件不可用")
        return descriptor, info
    except BaseException:
        os.close(descriptor)
        raise


def _program(plugin_dir, version):
    for path in (plugin_dir, plugin_dir / "bin", plugin_dir / "dist"):
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise RuntimeError("Rust 后端目录不可用")
    descriptor, info = _open_regular(plugin_dir / "dist/backend.json")
    try:
        if info.st_size > 8192:
            raise RuntimeError("Rust 后端构建信息无效")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            metadata = json.load(stream)
    finally:
        os.close(descriptor)
    if (not isinstance(metadata, dict) or metadata.get("protocol") != PROTOCOL_VERSION
            or metadata.get("version") != version or metadata.get("binary") != "sideloader-core"
            or not isinstance(metadata.get("fingerprint"), str) or not _HASH.fullmatch(metadata["fingerprint"])
            or not isinstance(metadata.get("sha256"), str) or not _HASH.fullmatch(metadata["sha256"])):
        raise RuntimeError("Rust 后端构建信息无效")
    descriptor, info = _open_regular(plugin_dir / "bin/sideloader-core")
    try:
        if info.st_mode & 0o111 == 0 or not 0 < info.st_size <= 128 * 1024 * 1024:
            raise RuntimeError("Rust 后端程序缺少执行权限或大小无效")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != metadata["sha256"]:
            raise RuntimeError("Rust 后端程序与构建信息不匹配")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, metadata
    except BaseException:
        os.close(descriptor)
        raise


class _SDKLogBridge(logging.Handler):
    def __init__(self, plugin):
        super().__init__()
        self.plugin = plugin
        self.loop = asyncio.get_running_loop()
        self.pending = 0

    def emit(self, record):
        if self._closed or self.plugin._closing or self.pending >= 16:
            return
        self.pending += 1
        # The core applies its diagnostic allowlist; formatted text never crosses IPC.
        fields = {"module": record.module, "line": record.lineno, "level": record.levelno}
        try:
            self.loop.call_soon_threadsafe(self._deliver, fields)
        except RuntimeError:
            self.pending -= 1

    def _deliver(self, fields):
        async def send():
            try:
                if not self._closed:
                    await self.plugin._send({"type": "host_log", "fields": fields})
            except Exception:
                pass
            finally:
                with self.lock:
                    self.pending -= 1
        task = asyncio.create_task(send())
        self.plugin._log_tasks.add(task)
        task.add_done_callback(self.plugin._log_tasks.discard)


class Plugin:
    def __init__(self):
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._process = None
        self._reader_task = None
        self._stderr_task = None
        self._hello = None
        self._pending = {}
        self._closing = False
        self._dead = False
        self._metadata = None
        self._sdk_handler = None
        self._log_tasks = set()

    def _attach_sdk_logger(self):
        logger = getattr(decky, "logger", None)
        exact = getattr(decky, "DECKY_PLUGIN_LOG", "")
        if not isinstance(logger, logging.Logger) or not isinstance(exact, str) or not exact:
            return
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler) and handler.baseFilename == os.path.abspath(exact):
                logger.removeHandler(handler)
                handler.close()
        self._sdk_handler = _SDKLogBridge(self)
        logger.addHandler(self._sdk_handler)

    async def _main(self):
        await self._start()

    async def _start(self):
        async with self._start_lock:
            if self._closing or self._dead:
                raise RuntimeError(_STOPPED)
            if self._process is not None:
                if self._process.returncode is not None:
                    self._dead = True
                    raise RuntimeError(_STOPPED)
                return
            plugin_dir = Path(decky.DECKY_PLUGIN_DIR)
            version = decky.DECKY_PLUGIN_VERSION
            worker = asyncio.create_task(asyncio.to_thread(_program, plugin_dir, version))
            try:
                descriptor, metadata = await asyncio.shield(worker)
            except asyncio.CancelledError:
                try:
                    descriptor, _ = await worker
                    os.close(descriptor)
                except Exception:
                    pass
                raise
            self._metadata = metadata
            self._hello = asyncio.get_running_loop().create_future()
            context = {
                "home": str(decky.DECKY_HOME), "plugin_dir": str(plugin_dir),
                "settings_dir": str(decky.DECKY_PLUGIN_SETTINGS_DIR),
                "runtime_dir": str(decky.DECKY_PLUGIN_RUNTIME_DIR),
                "log_dir": str(decky.DECKY_PLUGIN_LOG_DIR), "user_home": str(decky.DECKY_USER_HOME),
                "name": decky.DECKY_PLUGIN_NAME, "version": version,
                "loader_version": getattr(decky, "DECKY_VERSION", ""),
            }
            environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ") if key in os.environ}
            environment["HOME"] = context["user_home"]
            try:
                self._process = await asyncio.create_subprocess_exec(
                    f"/proc/self/fd/{descriptor}", "--stdio", "--parent-pid", str(os.getpid()),
                    pass_fds=(descriptor,), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    limit=MAX_FRAME + 1, env=environment,
                )
            finally:
                os.close(descriptor)
            self._reader_task = asyncio.create_task(self._read_messages())
            self._stderr_task = asyncio.create_task(self._read_stderr())
            try:
                await self._send({"type": "init", "protocol": PROTOCOL_VERSION, "host": context})
                await asyncio.wait_for(self._hello, 10)
                self._attach_sdk_logger()
            except BaseException:
                self._dead = True
                await self._stop_process()
                raise

    async def _send(self, message):
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        if len(data) > MAX_FRAME:
            raise RuntimeError("后端请求超过上限")
        async with self._write_lock:
            process = self._process
            if process is None or process.returncode is not None or process.stdin is None:
                raise RuntimeError(_STOPPED)
            process.stdin.write(data)
            await process.stdin.drain()

    async def _read_messages(self):
        try:
            while True:
                data = await self._process.stdout.readline()
                if not data:
                    break
                if len(data) > MAX_FRAME or not data.endswith(b"\n"):
                    raise RuntimeError("IPC 响应超过上限或不完整")
                message = json.loads(data)
                if not isinstance(message, dict):
                    raise RuntimeError("IPC 响应无效")
                kind = message.get("type")
                if kind == "hello":
                    if self._hello.done() or any(message.get(key) != self._metadata[key]
                            for key in ("protocol", "version", "fingerprint", "sha256", "target")):
                        raise RuntimeError("Rust 后端运行身份不匹配")
                    if not re.fullmatch(r"[a-f0-9]{32}", message.get("boot_id", "")):
                        raise RuntimeError("Rust 后端运行身份不匹配")
                    self._hello.set_result(message)
                elif kind == "response":
                    if type(message.get("ok")) is not bool:
                        raise RuntimeError("IPC 响应无效")
                    future = self._pending.pop(message.get("id"), None)
                    if future is not None and not future.done():
                        if message["ok"]:
                            future.set_result(message.get("result"))
                        else:
                            error = message.get("error")
                            text = error.get("message") if isinstance(error, dict) else None
                            future.set_exception(RuntimeError(text[:4096] if isinstance(text, str) else _STOPPED))
                elif kind == "event":
                    if message.get("event") not in ("native_management_requested", "scheduled_check_completed") or not isinstance(message.get("args"), list):
                        raise RuntimeError("IPC 事件无效")
                    if not self._closing:
                        try:
                            await decky.emit(message["event"], *message["args"])
                        except Exception:
                            decky.logger.error("Sideloader event delivery failed")
                elif kind == "closed":
                    break
                else:
                    raise RuntimeError("IPC 消息类型无效")
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            self._dead = True
            if self._hello is not None and not self._hello.done():
                self._hello.set_exception(RuntimeError(_STOPPED))
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(_STOPPED))
            self._pending.clear()
            if self._process is not None and self._process.stdin is not None:
                self._process.stdin.close()

    async def _read_stderr(self):
        try:
            while True:
                data = await self._process.stderr.readline()
                if not data:
                    return
                if len(data) <= 8192:
                    try:
                        item = json.loads(data)
                    except (ValueError, UnicodeError):
                        continue
                    code = item.get("code") if isinstance(item, dict) else None
                    if code in {"panic", "ipc", "invalid_state", "invalid_response", "filesystem", "io_failed", "permission_denied", "file_missing", "disk_full"}:
                        decky.logger.error("Sideloader core stopped: %s", code)
        except (Exception, asyncio.CancelledError):
            pass

    async def _call(self, method, args):
        identifier = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        try:
            await self._send({"type": "request", "id": identifier, "method": method, "args": args})
        except BaseException:
            self._pending.pop(identifier, None)
            future.cancel()
            raise
        # Caller cancellation never replays or abandons an already-dispatched mutation.
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(future)

    async def _rpc(self, method, args):
        if self._closing or self._dead:
            raise RuntimeError(_STOPPED)
        await self._start()
        return await self._call(method, args)

    async def _stop_process(self):
        process = self._process
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        current = asyncio.current_task()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and task is not current:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _unload(self):
        self._closing = True
        async with self._start_lock:
            try:
                if self._sdk_handler is not None:
                    decky.logger.removeHandler(self._sdk_handler)
                    self._sdk_handler.close()
                    self._sdk_handler = None
                if self._log_tasks:
                    await asyncio.wait_for(asyncio.gather(*tuple(self._log_tasks), return_exceptions=True), 0.5)
                if self._process is not None and self._process.returncode is None and not self._dead:
                    async def stop():
                        await self._call("shutdown", [])
                        await self._process.wait()
                    await asyncio.wait_for(stop(), 4)
            except (Exception, asyncio.CancelledError):
                pass
            finally:
                await self._stop_process()

    async def _uninstall(self):
        await self._unload()


def _forward(method):
    async def invoke(self, *args):
        return await self._rpc(method, list(args))
    invoke.__name__ = method
    return invoke


for _method in RPC_METHODS:
    setattr(Plugin, _method, _forward(_method))
