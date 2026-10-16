import asyncio
import hashlib
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))
from diagnostics import Diagnostics, PACKAGE_BYTES, ZIP_MARKER
from diagnostics_sharing import DiagnosticSharing, private_ipv4
import diagnostics_sharing as module


class DiagnosticSharingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.diag = Diagnostics(str(self.root / "logs"), str(self.root / "user"))
        # Only this injected test provider permits loopback. Production uses Linux RFC1918 discovery.
        self.addresses = ["127.0.0.1"]
        self.share = DiagnosticSharing(self.diag, addresses=lambda: self.addresses)
        self.session = "a" * 32

    async def asyncTearDown(self):
        await self.share.stop(closing=True)
        await self.diag.close()
        self.temp.cleanup()

    async def start(self):
        result = await self.share.start("127.0.0.1", self.session, {}, self.share.session)
        self.assertEqual(result["state"], "active")
        self.assertEqual(len(self.share.token), 64)
        self.assertEqual(set(result), {"state", "session", "package", "url", "remaining", "downloads"})
        return result

    async def connect(self):
        return await asyncio.open_connection("127.0.0.1", self.share.port)

    def request(self, method="GET", route=None, headers="", host=None):
        return (f"{method} {route or '/pickup/' + self.share.token} HTTP/1.1\r\nHost: {host or '127.0.0.1:' + str(self.share.port)}\r\n{headers}\r\n").encode()

    async def exchange(self, request):
        reader, writer = await self.connect()
        writer.write(request)
        await writer.drain()
        try:
            return await asyncio.wait_for(reader.read(), 2)
        finally:
            writer.close()
            await writer.wait_closed()

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0.002)

    async def test_exact_bytes_head_limits_hash_modes_and_no_token_artifacts(self):
        result = await self.start()
        package = Path(result["package"]["path"])
        expected = package.read_bytes()
        self.assertEqual(package.stat().st_mode & 0o777, 0o600)
        self.assertEqual(package.parent.stat().st_mode & 0o777, 0o700)
        token = self.share.token.encode()
        head = await self.exchange(self.request("HEAD"))
        self.assertEqual(head.split(b"\r\n\r\n", 1)[1], b"")
        self.assertEqual(self.share.successes, 0)
        self.assertEqual(self.share.reserved, 0)
        for count in range(3):
            reply = await self.exchange(self.request())
            headers, body = reply.split(b"\r\n\r\n", 1)
            self.assertTrue(headers.startswith(b"HTTP/1.1 200"))
            self.assertIn(b"Cache-Control: no-store", headers)
            self.assertIn(b"Content-Disposition: attachment", headers)
            for forbidden in (b"Set-Cookie", b"Access-Control", token):
                self.assertNotIn(forbidden, headers)
            self.assertEqual(body, expected)
            self.assertEqual(hashlib.sha256(body).hexdigest(), self.share.sha256)
        await self.settle()
        self.assertEqual(self.share.successes, 3)
        self.assertEqual(self.share.phase, "exhausted")
        self.assertIsNone(self.share.token)
        self.assertIsNone(self.share.listener)
        self.assertEqual(self.share.clients, {})
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(token, path.read_bytes())
        self.assertTrue(package.exists())

    async def test_prefetch_rejected_before_file_or_slots_and_three_real_downloads_remain(self):
        result = await self.start()
        expected = Path(result["package"]["path"]).read_bytes()
        token = self.share.token.encode()
        with patch.object(self.share, "_open_package", wraps=self.share._open_package) as opened:
            for headers in (
                "Sec-Purpose: prefetch\r\n",
                "Purpose: prefetch\r\n",
                "sEc-PuRpOsE: PrEfEtCh\r\n",
                "Sec-Purpose: prefetch;anonymous-client-ip\r\n",
                "Purpose: navigation, prefetch\r\n",
                "Sec-Purpose: navigation\r\nPurpose: prefetch\r\n",
            ):
                reply = await self.exchange(self.request(headers=headers))
                head, body = reply.split(b"\r\n\r\n", 1)
                self.assertTrue(head.startswith(b"HTTP/1.1 400"))
                self.assertIn(b"Content-Length: 0", head)
                self.assertEqual(body, b"")
                self.assertNotIn(token, reply)
                self.assertEqual((self.share.successes, self.share.reserved), (0, 0))
                self.assertEqual(self.share.phase, "active")
            opened.assert_not_called()
        for count in range(1, 4):
            reply = await self.exchange(self.request())
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"))
            self.assertEqual(reply.split(b"\r\n\r\n", 1)[1], expected)
            self.assertEqual(self.share.successes, count)
        await self.settle()
        self.assertEqual(self.share.phase, "exhausted")
        self.assertEqual(self.share.reserved, 0)
        self.assertIsNone(self.share.listener)
        self.assertIsNone(self.share.token)
        self.assertEqual(self.share.clients, {})

    async def test_malicious_requests_are_bounded_and_do_not_consume_slots(self):
        await self.start()
        route = "/pickup/" + self.share.token
        requests = [
            self.request("POST"), self.request("OPTIONS"), self.request("TRACE"),
            self.request(route=route + "?x=1"), self.request(route="/../" + route),
            self.request(route=route.replace("pickup", "%70ickup")), self.request(route="/"),
            self.request(route="/pickup/" + "0" * 64), self.request(host="evil.example"),
            self.request(host="localhost:" + str(self.share.port)),
            self.request(headers="Range: bytes=0-1\r\n"), self.request(headers="Content-Length: 1\r\n") + b"x",
            self.request(headers="Transfer-Encoding: chunked\r\n"), self.request(headers="Expect: 100-continue\r\n"),
            self.request(headers="Host: duplicate\r\n"), self.request(headers=" Bad: folded\r\n"),
            self.request(headers="X: \x00\r\n"), self.request(headers="X: " + "x" * 17000 + "\r\n"),
            self.request(headers="".join(f"X-{i}: x\r\n" for i in range(33))),
            b"GET /" + b"x" * 2048 + b" HTTP/1.1\r\n\r\n", self.request() + b"body",
        ]
        for request in requests:
            with self.subTest(case=request[:12]):
                reply = await self.exchange(request)
                self.assertFalse(reply.startswith(b"HTTP/1.1 200"))
                self.assertNotIn(self.share.token.encode(), reply)
        self.assertEqual((self.share.successes, self.share.reserved), (0, 0))
        self.assertEqual(self.share.phase, "active")

    async def test_admission_cap_reserves_before_slow_headers_and_stop_drains(self):
        await self.start()
        connections = [await self.connect() for _ in range(4)]
        await self.settle()
        self.assertEqual(len(self.share.clients), 4)
        reader, extra = await self.connect()
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
        self.assertEqual(len(self.share.clients), 4)
        extra.close(); await extra.wait_closed()
        before = time.monotonic()
        await self.share.stop(self.session)
        self.assertLess(time.monotonic() - before, 1)
        self.assertEqual(self.share.clients, {})
        self.assertEqual(self.share.reserved, 0)
        self.assertIsNone(self.share.accept_task)
        self.assertIsNone(self.share.watch_task)
        self.assertIsNone(self.share.token)
        for reader, writer in connections:
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
            writer.close(); await writer.wait_closed()
        self.assertIsNone(self.diag.share_phase)

    async def test_absolute_request_budget_and_slow_reader_transfer_budget(self):
        await self.start()
        with patch.object(module, "REQUEST_SECONDS", 0.05):
            reader, writer = await self.connect()
            writer.write(b"GET /"); await writer.drain()
            self.assertNotIn(b"200 OK", await asyncio.wait_for(reader.read(), 1))
            writer.close(); await writer.wait_closed()
        # Fill a valid owned package to force backpressure without extra package copies.
        await self.share.stop(self.session)
        pack = Path(self.share.package["path"])
        pack.write_bytes(ZIP_MARKER + b"x" * (PACKAGE_BYTES - len(ZIP_MARKER)))
        self.session = "b" * 32
        original = self.diag.export
        async def reuse(*args, **kwargs):
            return {"state": "success", "file": pack.name, "path": str(pack), "bytes": PACKAGE_BYTES}
        self.diag.export = reuse
        await self.start()
        with patch.object(module, "TRANSFER_SECONDS", 0.05):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            client.setblocking(False)
            loop = asyncio.get_running_loop()
            await loop.sock_connect(client, ("127.0.0.1", self.share.port))
            await loop.sock_sendall(client, self.request())
            await asyncio.sleep(0.15)
            self.assertEqual(self.share.reserved, 0)
            self.assertEqual(self.share.successes, 0)
            self.assertEqual(self.share.clients, {})
            client.close()
        self.diag.export = original

    async def test_concurrent_success_slots_and_transfer_cap_reserved_before_send(self):
        await self.start()
        release = asyncio.Event()
        real_send = asyncio.get_running_loop().sock_sendall
        async def blocked(sock, data):
            if data.startswith(b"HTTP/1.1 200"):
                await release.wait()
            return await real_send(sock, data)
        loop = asyncio.get_running_loop()
        with patch.object(loop, "sock_sendall", side_effect=blocked):
            first = asyncio.create_task(self.exchange(self.request()))
            second = asyncio.create_task(self.exchange(self.request()))
            await self.settle()
            self.assertEqual(self.share.reserved, 2)
            self.assertTrue((await self.exchange(self.request())).startswith(b"HTTP/1.1 429"))
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(self.share.successes, 2)
        release.clear()
        with patch.object(loop, "sock_sendall", side_effect=blocked):
            last = asyncio.create_task(self.exchange(self.request()))
            await self.settle()
            self.assertEqual(self.share.successes + self.share.reserved, 3)
            self.assertTrue((await self.exchange(self.request())).startswith(b"HTTP/1.1 429"))
            # HEAD remains usable and never reserves a success slot.
            head = asyncio.create_task(self.exchange(self.request("HEAD")))
            await self.settle()
            self.assertEqual(self.share.reserved, 1)
            release.set()
            await asyncio.gather(last, head)
        await self.settle()
        self.assertEqual(self.share.successes, 3)

    async def test_failed_transfer_releases_success_reservation(self):
        await self.start()
        loop = asyncio.get_running_loop()
        real_send = loop.sock_sendall
        async def failure(sock, data):
            if data.startswith(b"HTTP/1.1 200"):
                raise ConnectionResetError()
            return await real_send(sock, data)
        with patch.object(loop, "sock_sendall", side_effect=failure):
            await self.exchange(self.request())
        self.assertEqual((self.share.successes, self.share.reserved), (0, 0))
        self.assertTrue((await self.exchange(self.request())).startswith(b"HTTP/1.1 200"))

    async def test_export_interlock_preparing_active_draining_cancel_and_retention(self):
        for _ in range(3):
            await self.diag.export({})
        real_build = self.diag._build_export
        import threading
        entered, release = threading.Event(), threading.Event()
        def slow(summary):
            entered.set(); release.wait(2)
            return real_build(summary)
        with patch.object(self.diag, "_build_export", side_effect=slow):
            start = asyncio.create_task(self.share.start("127.0.0.1", self.session, {}))
            await asyncio.to_thread(entered.wait, 1)
            self.assertEqual(self.diag.share_phase, "preparing")
            with self.assertRaises(RuntimeError): await self.diag.export({})
            with self.assertRaises(RuntimeError): await self.share.start("127.0.0.1", "b" * 32, {})
            start.cancel()
            with self.assertRaises(asyncio.CancelledError): await start
            stop = asyncio.create_task(self.share.stop(self.session))
            await self.settle()
            self.assertEqual(self.diag.share_phase, "draining")
            with self.assertRaises(RuntimeError): await self.diag.export({})
            release.set(); await stop
        self.assertIsNone(self.share.listener)
        self.assertIsNone(self.diag.share_phase)
        # Same intent never replays after cancellation or stop.
        self.assertEqual((await self.share.start("127.0.0.1", self.session, {}))["state"], "stopped")
        self.assertEqual(len(list(self.diag._export_directory().glob("*.zip"))), 3)
        self.assertEqual(list(self.diag._export_directory().glob("*.tmp")), [])
        self.session = "c" * 32
        await self.start()
        with self.assertRaises(RuntimeError): await self.diag.export({})
        with self.assertRaises(RuntimeError): await self.share.stop("b" * 32)
        await self.share.stop(self.session)
        self.assertEqual((await self.diag.export({}))["state"], "success")
        self.assertLessEqual(sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()), 22 * 1024 * 1024)

    async def test_expiry_interface_loss_and_unload_never_rebind(self):
        await self.start()
        old_url = self.share.status()["url"]
        self.share.deadline = time.monotonic() - 1
        await asyncio.sleep(1.1)
        self.assertEqual(self.share.phase, "expired")
        self.assertNotIn("url", self.share.status())
        self.assertIsNone(self.diag.share_phase)
        self.session = "b" * 32
        await self.start()
        self.addresses.clear()
        await asyncio.sleep(1.1)
        self.assertEqual(self.share.phase, "error")
        self.assertIsNone(self.share.token)
        self.addresses.append("127.0.0.1")
        self.session = "c" * 32
        await self.start()
        self.assertNotEqual(old_url, self.share.status()["url"])
        await self.share.stop(closing=True)
        with self.assertRaises(RuntimeError): await self.share.start("127.0.0.1", "d" * 32, {})
        fresh = DiagnosticSharing(self.diag, addresses=lambda: self.addresses)
        self.assertEqual(fresh.status(), {"state": "idle"})
        self.assertIsNone(fresh.listener)

    async def test_symlink_hardlink_replacement_delete_mutation_and_mode_rejected(self):
        for mutation in ("symlink", "hardlink", "swap", "delete", "content", "mode"):
            with self.subTest(mutation=mutation):
                self.session = format(len(mutation), "032x")
                await self.start()
                path = Path(self.share.package["path"])
                original = path.read_bytes()
                foreign = self.root / "foreign"
                foreign.write_bytes(original)
                if mutation == "symlink": path.unlink(); path.symlink_to(foreign)
                elif mutation == "hardlink": os.link(path, self.root / "linked")
                elif mutation == "swap": path.unlink(); path.write_bytes(original); path.chmod(0o600)
                elif mutation == "delete": path.unlink()
                elif mutation == "content": path.write_bytes(original[:-1] + b"x")
                else: path.chmod(0o644)
                reply = await self.exchange(self.request())
                self.assertFalse(reply.startswith(b"HTTP/1.1 200"))
                await self.settle()
                self.assertEqual(self.share.phase, "error")
                self.assertEqual(self.share.clients, {})
                self.assertIsNone(self.share.token)
                self.assertEqual(foreign.read_bytes(), original)
                # Preserve unknown/link files; cleanup only this temporary fixture.
                if path.is_symlink(): path.unlink()
                if (self.root / "linked").exists(): (self.root / "linked").unlink()

    async def test_start_failure_strict_requests_and_live_address_validation(self):
        for address, session in (("0.0.0.0", self.session), ("8.8.8.8", self.session), ("127.0.0.1", "invalid"), ([], self.session)):
            with self.assertRaises(RuntimeError): await self.share.start(address, session, {})
        with patch.object(self.diag, "_build_export", side_effect=OSError("synthetic private error")):
            reply = await self.share.start("127.0.0.1", self.session, {})
        self.assertEqual(reply["state"], "error")
        self.assertNotIn("synthetic", str(reply))
        self.assertIsNone(self.diag.share_phase)
        self.assertIsNone(self.share.token)
        with patch.object(self.share, "addresses", side_effect=OSError()):
            self.assertEqual(self.share.available(), {"addresses": []})

    async def test_stale_intent_cannot_replay_after_newer_completed_session(self):
        await self.start()
        first = self.session
        await self.share.stop(first)
        self.session = "b" * 32
        await self.start()
        await self.share.stop(self.session)
        with self.assertRaises(RuntimeError):
            await self.share.start("127.0.0.1", first, {}, None)
        with self.assertRaises(RuntimeError):
            await self.share.start("127.0.0.1", "c" * 32, {}, first)
        self.assertEqual(self.share.phase, "stopped")
        self.assertIsNone(self.share.listener)

    async def test_deleted_idle_file_stops_and_repeated_sessions_do_not_leak_tasks_or_fds(self):
        fd_dir = Path("/proc/self/fd") if Path("/proc/self/fd").exists() else Path("/dev/fd")
        before = len(list(fd_dir.iterdir()))
        for n in range(8):
            self.session = format(n, "032x")
            await self.start()
            await self.exchange(self.request("HEAD"))
            await self.share.stop(self.session)
        await self.settle()
        self.assertLessEqual(len(list(fd_dir.iterdir())), before)
        for task in asyncio.all_tasks():
            self.assertFalse(task.get_coro().__qualname__.startswith("DiagnosticSharing."))
        self.session = "f" * 32
        await self.start()
        Path(self.share.package["path"]).unlink()
        await asyncio.sleep(1.1)
        self.assertEqual(self.share.phase, "error")
        self.assertIsNone(self.share.token)

    async def test_main_unload_revokes_sharing_before_unrelated_work(self):
        import types
        from unittest.mock import AsyncMock, Mock
        sys.modules.setdefault("decky", types.ModuleType("decky"))
        if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
        import main
        await self.start()
        plugin = main.Plugin()
        plugin._sharing, plugin._diagnostics = self.share, self.diag
        async def unrelated():
            self.assertIsNone(self.share.listener)
            self.assertIsNone(self.share.token)
            self.assertEqual(self.share.clients, {})
        plugin._flow = types.SimpleNamespace(unload=AsyncMock(side_effect=unrelated))
        with patch.object(main.decky, "logger", Mock(), create=True):
            await plugin._unload()
        plugin._flow.unload.assert_awaited_once()

    def test_discovery_uses_live_linux_flags_and_filters_rfc1918_without_listener(self):
        import fcntl
        import struct
        names = [(i, name) for i, name in enumerate(("lo", "wlan0", "public", "down", "link", "duplicate"))]
        values = {"lo": (0x49, "127.0.0.1"), "wlan0": (0x41, "192.168.2.5"), "public": (0x41, "8.8.8.8"), "down": (0x01, "10.1.2.3"), "link": (0x41, "169.254.1.2"), "duplicate": (0x41, "192.168.2.5")}
        def ioctl(fd, command, request):
            flags, address = values[request.split(b"\0", 1)[0].decode()]
            if command == 0x8913: return b"x" * 16 + struct.pack("H", flags) + b"x" * 238
            return b"x" * 20 + socket.inet_aton(address) + b"x" * 232
        with patch.object(socket, "if_nameindex", return_value=names), patch.object(fcntl, "ioctl", side_effect=ioctl):
            self.assertEqual(module.lan_addresses(), ["192.168.2.5"])

    def test_rfc1918_only_exact_canonical_ipv4(self):
        for value in ("10.1.2.3", "172.16.0.1", "172.31.255.254", "192.168.2.5"):
            self.assertTrue(private_ipv4(value))
        for value in ("127.0.0.1", "0.0.0.0", "172.32.0.1", "169.254.1.2", "224.0.0.1", "8.8.8.8", "100.64.0.1", "192.168.01.1", "::1", None, []):
            self.assertFalse(private_ipv4(value))
