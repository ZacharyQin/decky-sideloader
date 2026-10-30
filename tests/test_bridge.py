import asyncio
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.modules.setdefault("decky", types.ModuleType("decky"))
spec = importlib.util.spec_from_file_location("sideloader_bridge_under_test", ROOT / "backend/main.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
BINARY = ROOT / "backend-rs/target/debug/sideloader-core"


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue(BINARY.is_file(), "Build the actual Rust debug binary before bridge tests")
        self.temporary = tempfile.TemporaryDirectory(prefix="sideloader-rust-bridge-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.plugin_dir = self.home / "plugins/decky-sideloader"
        for name in ("bin", "dist", "data"):
            (self.plugin_dir / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BINARY, self.plugin_dir / "bin/sideloader-core")
        (self.plugin_dir / "bin/sideloader-core").chmod(0o755)
        shutil.copyfile(ROOT / "backend/main.py", self.plugin_dir / "main.py")
        shutil.copyfile(ROOT / "data/repositories.json", self.plugin_dir / "data/repositories.json")
        identity_process = await asyncio.create_subprocess_exec(str(BINARY), "--identity", stdout=asyncio.subprocess.PIPE)
        identity, _ = await identity_process.communicate()
        self.assertEqual(identity_process.returncode, 0)
        self.identity = json.loads(identity)
        (self.plugin_dir / "dist/backend.json").write_text(json.dumps({**self.identity, "binary": "sideloader-core"}))
        (self.plugin_dir / "plugin.json").write_text(json.dumps({"name": "Sideloader"}))
        (self.plugin_dir / "package.json").write_text(json.dumps({"name": "decky-sideloader", "version": "0.6.0"}))
        bundle = b"export const test = true;"
        (self.plugin_dir / "dist/index.js").write_bytes(bundle)
        (self.plugin_dir / "dist/build.json").write_text(json.dumps({"version": "0.6.0", "fingerprint": "b" * 64,
            "bundle": hashlib.sha256(bundle).hexdigest()}))
        self.settings = self.home / "settings/decky-sideloader"
        self.runtime = self.home / "data/decky-sideloader"
        self.logs = self.home / "logs/decky-sideloader"
        self.user_home = self.home / "user"
        self.user_home.mkdir()
        self.settings.mkdir(parents=True)
        (self.home / "settings/loader.json").write_text('{"frozenPlugins":[]}')
        self.events = []
        self.answer_native = True
        self.plugin = bridge.Plugin()
        self.callbacks = set()

        async def emit(name, *args):
            self.events.append((name, args))
            if name == "native_management_requested" and self.answer_native:
                payload = args[0]
                task = asyncio.create_task(self.plugin.receive_native_management(payload["id"], self.native_payload()))
                self.callbacks.add(task)
                task.add_done_callback(self.callbacks.discard)
        self.sdk = patch.multiple(bridge.decky, create=True, logger=Mock(), emit=emit,
            DECKY_HOME=str(self.home), DECKY_PLUGIN_DIR=str(self.plugin_dir), DECKY_PLUGIN_VERSION="0.6.0",
            DECKY_PLUGIN_NAME="Sideloader", DECKY_PLUGIN_RUNTIME_DIR=str(self.runtime),
            DECKY_PLUGIN_SETTINGS_DIR=str(self.settings), DECKY_PLUGIN_LOG_DIR=str(self.logs),
            DECKY_USER_HOME=str(self.user_home), DECKY_VERSION="v3.3.0")
        self.sdk.start()
        self.addCleanup(self.sdk.stop)

    def native_payload(self):
        return {"snapshot": {"ok": True, "plugins": [
            {"name": "Sideloader", "version": "0.6.0", "load_type": 0, "disabled": False},
            {"name": "Example", "version": "1.0.0", "load_type": 0, "disabled": False},
        ]}, "capable": [], "cache": {"channel": "default", "names": []}, "error": None}

    async def asyncTearDown(self):
        await self.plugin._unload()
        if self.callbacks:
            await asyncio.gather(*self.callbacks, return_exceptions=True)

    async def call(self, method, *args):
        return await asyncio.wait_for(getattr(self.plugin, method)(*args), 15 if self.plugin._process is None else 3)

    async def test_actual_rust_handshake_rpc_and_graceful_exit(self):
        await self.plugin._main()
        process = self.plugin._process
        self.assertEqual(await self.call("ping"), "pong")
        self.assertEqual((await self.call("get_app_settings"))["networkMode"], "direct")
        self.assertIsNone(await self.call("get_operation"))
        await self.plugin._unload()
        self.assertEqual(process.returncode, 0)
        self.assertTrue(self.plugin._reader_task.done())

    async def test_native_event_round_trip_and_library_mapping(self):
        folder = self.home / "plugins/example"
        folder.mkdir()
        (folder / "plugin.json").write_text('{"name":"Example","author":"Test"}')
        (folder / "package.json").write_text('{"name":"example","version":"1.0.0","repository":"a/b"}')
        listing = await self.call("list_sideload_plugins")
        row = next(p for p in listing["plugins"] if p["name"] == "Example")
        self.assertEqual((row["repo"], row["ownership"]["state"], row["ownership"]["eligible"]), ("a/b", "nonstore", True))
        self.assertTrue(any(e[0] == "native_management_requested" for e in self.events))

    async def test_settings_persist_across_real_core_instances(self):
        await self.call("set_network_mode", "ghfast")
        await self.call("set_auto_check", True, 720)
        before = (self.settings / "settings.json").read_bytes()
        old_pid = self.plugin._process.pid
        await self.plugin._unload()
        self.plugin = bridge.Plugin()
        settings = await self.call("get_app_settings")
        self.assertNotEqual(self.plugin._process.pid, old_pid)
        self.assertEqual((settings["networkMode"], settings["autoCheck"], settings["autoCheckMinutes"]), ("ghfast", True, 720))
        self.assertEqual((self.settings / "settings.json").read_bytes(), before)

    async def test_invalid_types_and_unknown_rpc_cannot_mutate_settings(self):
        await self.call("set_network_mode", "direct")
        before = (self.settings / "settings.json").read_bytes()
        for method, args in (("set_download_timeout", (True,)), ("set_check_notifications", (1,)),
                             ("set_auto_check", (True, 7)), ("set_network_mode", ("invalid",))):
            with self.assertRaises(RuntimeError):
                await self.call(method, *args)
        with self.assertRaises(RuntimeError):
            await asyncio.wait_for(self.plugin._rpc("arbitrary_method", []), 3)
        self.assertEqual((self.settings / "settings.json").read_bytes(), before)

    async def test_cancelled_caller_does_not_orphan_or_replay_core_request(self):
        self.answer_native = False
        pending = asyncio.create_task(self.plugin.list_sideload_plugins())
        async with asyncio.timeout(3):
            while not self.events:
                if pending.done():
                    await pending
                await asyncio.sleep(0)
        identifier = self.events[0][1][0]["id"]
        original = next(iter(self.plugin._pending.values()))
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertFalse(original.cancelled())
        await self.call("receive_native_management", identifier, self.native_payload())
        result = await asyncio.wait_for(original, 3)
        self.assertTrue(result["native_snapshot"]["ok"])
        self.assertEqual(len(self.events), 1)

    async def test_core_crash_is_reported_without_automatic_restart(self):
        await self.call("ping")
        process = self.plugin._process
        process.kill()
        await process.wait()
        with self.assertRaises(RuntimeError):
            await self.call("ping")
        self.assertIs(self.plugin._process, process)

    async def test_tampered_binary_is_rejected_before_execution(self):
        path = self.plugin_dir / "bin/sideloader-core"
        path.write_bytes(path.read_bytes() + b"tampered")
        with self.assertRaisesRegex(RuntimeError, "不匹配"):
            await self.plugin._main()
        self.assertIsNone(self.plugin._process)

    async def test_symlink_binary_is_rejected_before_execution(self):
        path = self.plugin_dir / "bin/sideloader-core"
        path.unlink()
        path.symlink_to(BINARY)
        with self.assertRaises(OSError):
            await self.plugin._main()
        self.assertIsNone(self.plugin._process)

    async def test_unknown_installation_is_preserved_without_replay(self):
        path = self.runtime / "installations/operation.json"
        path.parent.mkdir(parents=True)
        record = {"id": "a" * 32, "stage": "awaiting", "request_id": "native-request", "submitted_at": 1,
            "saw_start": False, "saw_finish": False, "package": {"name": "Example", "folder": "example", "version": "2.0.0"}}
        path.write_text(json.dumps(record))
        result = await self.call("get_operation")
        self.assertEqual(result["stage"], "unknown")
        self.assertEqual(result["request_id"], "native-request")
        self.assertEqual(json.loads(path.read_text())["stage"], "unknown")
        self.assertFalse(self.events)

    async def test_missing_frontend_cannot_complete_self_update(self):
        status = await self.call("get_self_update_status", None)
        self.assertFalse(status["frontend_compatible"])
        status = await self.call("get_self_update_status", {"version": "0.6.0", "fingerprint": "b" * 64})
        self.assertTrue(status["frontend_compatible"])
        self.assertIsNone(status["operation"])

    async def test_parent_exit_does_not_leave_a_core_process_running(self):
        source = """
import asyncio, importlib.util, json, logging, os, sys, types
sdk = types.ModuleType('decky')
for key, value in json.loads(sys.argv[2]).items(): setattr(sdk, key, value)
sdk.logger = logging.getLogger('parent-lifecycle-test')
async def emit(*args): pass
sdk.emit = emit
sys.modules['decky'] = sdk
spec = importlib.util.spec_from_file_location('adapter', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
async def main():
    plugin = module.Plugin()
    await plugin._main()
    print(plugin._process.pid, flush=True)
    os._exit(0)
asyncio.run(main())
"""
        context = {key: getattr(bridge.decky, key) for key in (
            "DECKY_HOME", "DECKY_PLUGIN_DIR", "DECKY_PLUGIN_VERSION", "DECKY_PLUGIN_NAME",
            "DECKY_PLUGIN_RUNTIME_DIR", "DECKY_PLUGIN_SETTINGS_DIR", "DECKY_PLUGIN_LOG_DIR",
            "DECKY_USER_HOME", "DECKY_VERSION")}
        parent = await asyncio.create_subprocess_exec(sys.executable, "-B", "-c", source,
            str(ROOT / "backend/main.py"), json.dumps(context), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        output, errors = await asyncio.wait_for(parent.communicate(), 15)
        self.assertEqual(parent.returncode, 0, errors.decode())
        pid = int(output.strip())
        try:
            descriptor = os.pidfd_open(pid)
        except ProcessLookupError:
            return
        loop = asyncio.get_running_loop()
        exited = loop.create_future()
        loop.add_reader(descriptor, lambda: None if exited.done() else exited.set_result(None))
        try:
            await asyncio.wait_for(exited, 4)
        finally:
            loop.remove_reader(descriptor)
            os.close(descriptor)

    async def test_failed_partial_persistence_never_replaces_previous_settings(self):
        import resource
        await self.call("set_show_home_task_notice", False)
        settings = self.settings / "settings.json"
        previous = settings.read_bytes()
        diagnostic_dir = self.logs / "diagnostics"
        diagnostic_dir.chmod(0o500)
        try:
            resource.prlimit(self.plugin._process.pid, resource.RLIMIT_FSIZE, (64, 64))
            with self.assertRaises(RuntimeError):
                await self.call("set_show_home_task_notice", True)
            self.assertEqual(settings.read_bytes(), previous)
            self.assertFalse(json.loads(settings.read_text())["showHomeTaskNotice"])
        finally:
            diagnostic_dir.chmod(0o700)

    async def test_sdk_logging_uses_rust_redaction_without_changing_other_handlers(self):
        logger = logging.Logger("sideloader-sdk-test")
        exact = self.home / "sdk.log"
        unrelated = self.home / "other.log"
        sdk_handler = logging.FileHandler(exact)
        other_handler = logging.FileHandler(unrelated)
        logger.addHandler(sdk_handler)
        logger.addHandler(other_handler)
        self.addCleanup(other_handler.close)
        with patch.object(bridge.decky, "logger", logger), patch.object(bridge.decky, "DECKY_PLUGIN_LOG", str(exact), create=True):
            await self.plugin._main()
            self.assertNotIn(sdk_handler, logger.handlers)
            self.assertIn(other_handler, logger.handlers)
            logger.error("PRIVATE_URL_AND_CREDENTIAL_SENTINEL")
            await asyncio.sleep(0)
            if self.plugin._log_tasks:
                await asyncio.gather(*tuple(self.plugin._log_tasks))
            # The following RPC is ordered after the host log in the same input pipe.
            await self.call("ping")
            data = (self.logs / "diagnostics/active.jsonl").read_text()
            self.assertIn('"event":"sdk_log"', data)
            self.assertNotIn("PRIVATE_URL_AND_CREDENTIAL_SENTINEL", data)
            self.assertNotIn("PRIVATE_URL_AND_CREDENTIAL_SENTINEL", exact.read_text())
            self.assertIn("PRIVATE_URL_AND_CREDENTIAL_SENTINEL", unrelated.read_text())
            await self.plugin._unload()
            self.assertEqual(logger.handlers, [other_handler])

    async def test_diagnostics_export_is_real_bounded_and_redacted(self):
        event = {"event": "ui_page", "page": "settings", "clientId": "a" * 32}
        self.assertEqual(await self.call("record_diagnostic_events", [event], 0), {"accepted": 1})
        result = await self.call("export_diagnostics", 12345)
        self.assertEqual(result["state"], "success")
        self.assertLessEqual(result["bytes"], 6 * 1024 * 1024)
        from zipfile import ZipFile
        with ZipFile(result["path"]) as archive:
            summary = json.loads(archive.read("summary.json"))
            self.assertEqual(summary["details"]["environment"]["steamClientVersion"], 12345)
            manifest = json.loads(archive.read("manifest.json"))
            for entry in manifest["files"]:
                self.assertEqual(hashlib.sha256(archive.read(entry["name"])).hexdigest(), entry["sha256"])
            self.assertNotIn(str(self.home), archive.read("summary.json").decode())

    async def raw_core(self, protocol=1):
        environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ") if key in os.environ}
        environment["HOME"] = str(self.user_home)
        process = await asyncio.create_subprocess_exec(str(BINARY), "--stdio", "--parent-pid", str(os.getpid()),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=environment)
        async def cleanup():
            if process.returncode is None:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 3)
        self.addAsyncCleanup(cleanup)
        context = {"home": str(self.home), "plugin_dir": str(self.plugin_dir), "settings_dir": str(self.settings),
            "runtime_dir": str(self.runtime), "log_dir": str(self.logs), "user_home": str(self.user_home),
            "name": "Sideloader", "version": "0.6.0", "loader_version": "fixture"}
        process.stdin.write(json.dumps({"type": "init", "protocol": protocol, "host": context}).encode() + b"\n")
        await process.stdin.drain()
        if protocol == 1:
            hello = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
            self.assertEqual((hello["type"], hello["protocol"], hello["version"]), ("hello", 1, "0.6.0"))
        return process

    async def raw_send(self, process, identifier, method, args=None):
        process.stdin.write(json.dumps({"type": "request", "id": identifier, "method": method,
            "args": [] if args is None else args}).encode() + b"\n")
        await process.stdin.drain()

    async def test_real_ipc_concurrency_limit_keeps_callback_and_shutdown_live(self):
        process = await self.raw_core()
        originals = {f"{i:032x}" for i in range(1, 17)}
        for identifier in sorted(originals):
            await self.raw_send(process, identifier, "list_sideload_plugins")
        events = []
        async with asyncio.timeout(3):
            while len(events) < 16:
                value = json.loads(await process.stdout.readline())
                self.assertEqual((value["type"], value["event"]), ("event", "native_management_requested"))
                events.append(value)
        busy = "b" * 32
        await self.raw_send(process, busy, "ping")
        response = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
        self.assertEqual((response["id"], response["ok"], response["error"]["code"]), (busy, False, "busy"))
        callback = "c" * 32
        await self.raw_send(process, callback, "receive_native_management", [events[0]["args"][0]["id"], self.native_payload()])
        responses = {busy: response}
        async with asyncio.timeout(3):
            while callback not in responses or not (originals & responses.keys()):
                value = json.loads(await process.stdout.readline())
                self.assertEqual(value["type"], "response")
                self.assertNotIn(value["id"], responses)
                responses[value["id"]] = value
        self.assertTrue(responses[callback]["ok"], "native callbacks must bypass saturated request permits")
        shutdown = "d" * 32
        await self.raw_send(process, shutdown, "shutdown")
        async with asyncio.timeout(3):
            while True:
                value = json.loads(await process.stdout.readline())
                if value["type"] == "closed":
                    break
                self.assertEqual(value["type"], "response")
                self.assertNotIn(value["id"], responses)
                responses[value["id"]] = value
        self.assertEqual(set(responses), originals | {busy, callback, shutdown})
        self.assertTrue(responses[shutdown]["ok"])
        self.assertEqual(await asyncio.wait_for(process.wait(), 3), 0)
        self.assertEqual(await process.stderr.read(), b"")

    async def test_real_ipc_invalid_request_and_eof_settle_pending_without_replay(self):
        process = await self.raw_core()
        bad = "e" * 32
        await self.raw_send(process, bad, "set_network_mode", {"not": "positional arguments"})
        value = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
        self.assertEqual((value["id"], value["error"]["code"]), (bad, "invalid_input"))
        pending = "f" * 32
        await self.raw_send(process, pending, "list_sideload_plugins")
        value = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
        self.assertEqual(value["event"], "native_management_requested")
        process.stdin.close()
        values = []
        async with asyncio.timeout(3):
            while True:
                value = json.loads(await process.stdout.readline())
                values.append(value)
                if value["type"] == "closed":
                    break
        settled = [value for value in values if value["type"] == "response"]
        self.assertEqual([value["id"] for value in settled], [pending])
        self.assertEqual(await asyncio.wait_for(process.wait(), 3), 0)
        self.assertEqual((self.settings / "settings.json").exists(), False)

    async def test_real_ipc_protocol_mismatch_exits_before_state_mutation(self):
        process = await self.raw_core(protocol=2)
        output, errors = await asyncio.wait_for(process.communicate(), 3)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(output, b"")
        self.assertEqual(json.loads(errors), {"level": "error", "code": "invalid_state"})
        self.assertFalse(self.settings.joinpath("settings.json").exists())

    async def test_bridge_and_core_rpc_allowlists_match(self):
        source = (ROOT / "backend-rs/src/rpc.rs").read_text()
        beginning = source.index("pub const METHODS:")
        end = source.index("];", beginning)
        import re
        names = re.findall(r'"([a-z_]+)"', source[beginning:end])
        self.assertEqual(set(names), set(bridge.RPC_METHODS))
