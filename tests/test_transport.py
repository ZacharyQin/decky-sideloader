import errno
import http.client
import io
import socket
import threading
import time
from contextlib import contextmanager
import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import sideload
from sideload import github_transport as network
from sideload.github_transport import GitHubTransport, allowed_url
from test_http import Response

API = "https://api.github.com/repos/acme/plugin/releases"
ZIP = "https://github.com/acme/plugin/releases/download/v1/plugin.zip"


@contextmanager
def trickling_response(prefix, tail=b"x" * 200, interval=0.003, ready=None):
    """Real HTTPResponse parsing over local sockets, with no external requests."""
    client, server = socket.socketpair()
    def send():
        try:
            request = bytearray()
            while b"\r\n\r\n" not in request:
                chunk = server.recv(4096)
                if not chunk:
                    return
                request.extend(chunk)
            server.sendall(prefix)
            if ready is not None:
                ready.set()
            for byte in tail:
                time.sleep(interval)
                server.sendall(bytes([byte]))
        except OSError:
            pass
        finally:
            server.close()
    sender = threading.Thread(target=send)
    sender.start()
    def connect(connection):
        connection.budget.attach(client)
        connection.sock = network._GuardedSocket(client, connection.budget)
    try:
        with patch.object(network._HTTPSConnection, "connect", connect):
            yield connect
    finally:
        try:
            client.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        client.close()
        sender.join(1)
        if sender.is_alive():
            raise AssertionError("fixture sender did not stop")


class TransportTests(unittest.TestCase):
    def test_all_query_surfaces_and_zip_use_fixed_prefix_without_credentials(self):
        urls = [API, "https://api.github.com/search/repositories?q=test", "https://api.github.com/repos/a/b/git/trees/HEAD?recursive=1", "https://api.github.com/repos/a/b/git/blobs/abc", "https://raw.githubusercontent.com/a/b/main/plugin.json", ZIP]
        for url in urls:
            with self.subTest(url=url):
                address = network.PROXY_ORIGINS["gh-proxy"] + "/" + url
                opener = Mock(open=Mock(return_value=Response(b'{}', address)))
                observed = Mock()
                with patch.object(network.urllib.request, "build_opener", return_value=opener):
                    transport = GitHubTransport(None, "gh-proxy", observed)
                    with transport.open(url, "gh-proxy", kind="download" if url == ZIP else "query"):
                        pass
                request = opener.open.call_args.args[0]
                self.assertEqual(request.full_url, address)
                self.assertEqual(set(k.lower() for k in request.headers), {"user-agent", "accept"})
                if url == ZIP:
                    observed.assert_not_called()
                else:
                    self.assertEqual(observed.call_args.args[0]["route"], "gh-proxy")

    def test_initial_proxy_cannot_expand_input_allowlist(self):
        for url in [network.PROXY_ORIGINS["gh-proxy"] + "/" + ZIP, network.PROXY_ORIGINS["ghfast"] + "/" + ZIP, "https://release-assets.githubusercontent.com/x.zip", "https://api.github.com/x", "https://github.com/a/b/archive/v1.zip"]:
            with self.subTest(url=url), patch.object(network.urllib.request, "build_opener") as build:
                with self.assertRaises(RuntimeError):
                    with GitHubTransport(None, "gh-proxy").open(url, "gh-proxy", kind="download"):
                        pass
                build.assert_not_called()

    def test_all_hops_reject_http_credentials_ports_lookalikes_and_nested_external_urls(self):
        bad = ["http://api.github.com/repos/a/b", "https://user:pass@api.github.com/a", "https://api.github.com.evil.test/a", "https://api.github.com:443/a", "https://evil.test/a", "https://gh-proxy.org.evil.test/" + API, "https://gh-proxy.org/https://evil.test/a", "https://gh-proxy.org/https://gh-proxy.org/" + API, "https://gh-proxy.org/http://api.github.com/a", "https://gh-proxy.org:443/" + API, "https://api.github.com/a#fragment", "https://api.github.com/\\evil", "https://api.github.com/a\n"]
        for url in bad:
            for kind in ("query", "download"):
                with self.subTest(url=url, kind=kind):
                    self.assertFalse(allowed_url(url, kind, proxy="gh-proxy"))
                    with self.assertRaises(RuntimeError):
                        network._Redirect(kind, "gh-proxy").redirect_request(Request(API), None, 302, "", {}, url)
        self.assertTrue(allowed_url(network.PROXY_ORIGINS["gh-proxy"] + "/" + API, "query", proxy="gh-proxy"))
        self.assertTrue(allowed_url("https://release-assets.githubusercontent.com/x.zip", "download"))
        self.assertFalse(allowed_url("https://release-assets.githubusercontent.com/x.zip", "query"))

    def test_final_url_is_checked_even_without_redirect_handler(self):
        for mode in ("direct", "gh-proxy", "ghfast"):
            response = Response(b'{}', "https://evil.test/a")
            with patch.object(network.urllib.request, "build_opener", return_value=Mock(open=Mock(return_value=response))):
                with self.assertRaises(RuntimeError):
                    GitHubTransport(None, mode).fetch_json(API)
            self.assertTrue(response.closed)

    def test_duplicate_length_and_transfer_encoding_conflict_are_rejected(self):
        from email.message import Message
        headers = Message()
        headers.add_header("Content-Length", "2")
        headers.add_header("Content-Length", "2")
        for candidate in (headers, {"Content-Length": "2", "Transfer-Encoding": "chunked"}):
            with self.subTest(headers=candidate), self.assertRaises(RuntimeError):
                network.content_length(candidate, 100)

    def test_redirect_count_is_bounded(self):
        redirect = network._Redirect("query", "gh-proxy")
        for i in range(5):
            self.assertIsNotNone(redirect.redirect_request(Request(API), None, 302, "", {}, API + f"?n={i}"))
        with self.assertRaises(RuntimeError):
            redirect.redirect_request(Request(API), None, 302, "", {}, API)

    def test_auto_falls_back_once_only_for_retryable_failures(self):
        errors = [URLError(TimeoutError("timed out")), URLError(ssl.SSLError("TLS EOF")), ConnectionResetError(), OSError(errno.ENETUNREACH, "unreachable"), HTTPError(API, 502, "Bad Gateway", {}, None), HTTPError(API, 429, "rate", {}, None), HTTPError(API, 403, "rate", {"X-RateLimit-Remaining": "0"}, None)]
        for error in errors:
            with self.subTest(error=error):
                transport = GitHubTransport(None, "auto")
                with patch.object(transport, "open", side_effect=[error, Response(b'{}')]) as opened:
                    self.assertEqual(transport.fetch_json(API), {})
                    self.assertEqual([c.args[1] for c in opened.call_args_list], ["direct", "gh-proxy"])

    def test_nonretryable_status_invalid_json_short_read_and_limits_do_not_fall_back(self):
        cases = [HTTPError(API, code, "bad", {}, None) for code in (401, 403, 404)]
        cases += [RuntimeError("redirect rejected"), PermissionError("disk"), Response(b'not json'), Response(b'{}', headers={"Content-Length": "10"}), Response(b'{}', headers={"Content-Length": "-1"}), Response(b'{}', headers={"Content-Length": "999999999"})]
        for value in cases:
            with self.subTest(value=value):
                transport = GitHubTransport(None, "auto")
                with patch.object(transport, "open", side_effect=[value]) as opened:
                    with self.assertRaises(RuntimeError):
                        transport.fetch_json(API)
                    self.assertEqual(opened.call_count, 1)

    def test_api_auto_and_ghfast_exhaust_only_direct_and_ghproxy(self):
        for mode, calls in (("direct", 1), ("gh-proxy", 1), ("auto", 2), ("ghfast", 2)):
            transport = GitHubTransport(None, mode)
            with patch.object(transport, "open", side_effect=TimeoutError("offline")) as opened:
                with self.assertRaises(RuntimeError):
                    transport.fetch_json(API)
            self.assertEqual(opened.call_count, calls)

    def test_ghfast_redirects_cannot_cross_proxy_or_expand_sources(self):
        raw = "https://raw.githubusercontent.com/a/b/main/file"
        self.assertTrue(allowed_url("https://ghfast.top/" + raw, "query", proxy="ghfast"))
        self.assertTrue(allowed_url("https://ghfast.top/" + ZIP, "download", proxy="ghfast"))
        for url in ("http://ghfast.top/" + ZIP, "https://ghfast.top:443/" + ZIP,
                    "https://user@ghfast.top/" + ZIP, "https://ghfast.top.evil.test/" + ZIP,
                    "https://ghfast.top/https://evil.test/a.zip", "https://ghfast.top/https://gh-proxy.org/" + ZIP,
                    "https://ghfast.top/https://ghfast.top/" + ZIP, "https://gh-proxy.org/" + ZIP):
            with self.subTest(url=url):
                self.assertFalse(allowed_url(url, "download", proxy="ghfast"))
                with self.assertRaises(RuntimeError):
                    network._Redirect("download", "ghfast").redirect_request(Request(ZIP), None, 302, "", {}, url)

    def test_raw_auto_reaches_ghfast_but_stops_on_permission_error(self):
        url = "https://raw.githubusercontent.com/a/b/main/file"
        transport = GitHubTransport(None, "auto")
        with patch.object(transport, "open", side_effect=[TimeoutError(), TimeoutError(), Response(b"raw")]) as opened:
            self.assertEqual(transport.fetch_bytes(url), b"raw")
            self.assertEqual([call.args[1] for call in opened.call_args_list], ["direct", "gh-proxy", "ghfast"])
        with patch.object(transport, "open", side_effect=[TimeoutError(), HTTPError(url, 403, "denied", {}, None)]) as opened:
            with self.assertRaises(RuntimeError):
                transport.fetch_bytes(url)
            self.assertEqual(opened.call_count, 2)

    def test_ghfast_range_keeps_partial_response_semantics(self):
        with trickling_response(b"HTTP/1.1 206 Partial Content\r\nContent-Length: 3\r\nContent-Range: bytes 7-9/10\r\n\r\n", tail=b"zip"):
            self.assertEqual(GitHubTransport(None, "ghfast").fetch_range(ZIP, None, 3), (b"zip", 7, 10))

    def test_query_timeout_interrupts_headers_and_framing_without_body_progress(self):
        prefixes = {
            "status": b"HTTP/1.1 200 ",
            "headers": b"HTTP/1.1 200 OK\r\nX-Slow: ",
            "chunk-size": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;extension=",
            "trailer": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\na\r\n0\r\nX-Slow: ",
        }
        for stage, prefix in prefixes.items():
            with self.subTest(stage=stage), trickling_response(prefix):
                start = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, "等待.*超时"):
                    GitHubTransport(None).fetch_bytes(API, timeout=0.07)
                self.assertLess(time.monotonic() - start, 0.3)

    def test_query_deadline_exhaustion_still_gives_proxy_a_fresh_attempt(self):
        transport = GitHubTransport(None, "auto")
        opened, attempts = transport.open, []
        @contextmanager
        def route_response(url, route, **kwargs):
            attempts.append(route)
            prefix = b"HTTP/1.1 200 OK\r\nX-Slow: " if route == "direct" else b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n"
            body = b"x" * 200 if route == "direct" else b"{}"
            with trickling_response(prefix, tail=body):
                with opened(url, route, **kwargs) as response:
                    yield response
        with patch.object(transport, "open", side_effect=route_response):
            self.assertEqual(transport.fetch_bytes(API, timeout=0.07), b"{}")
        self.assertEqual(attempts, ["direct", "gh-proxy"])

    def test_range_query_deadline_falls_back_instead_of_waiting_like_a_download(self):
        transport = GitHubTransport(None, "auto")
        opened, attempts = transport.open, []
        @contextmanager
        def route_response(url, route, **kwargs):
            attempts.append(route)
            prefix = b"HTTP/1.1 206 Partial Content\r\nX-Slow: " if route == "direct" else b"HTTP/1.1 206 Partial Content\r\nContent-Length: 3\r\nContent-Range: bytes 7-9/10\r\n\r\n"
            body = b"x" * 200 if route == "direct" else b"zip"
            with trickling_response(prefix, tail=body):
                with opened(url, route, **kwargs) as response:
                    yield response
        with patch.object(transport, "open", side_effect=route_response):
            self.assertEqual(transport.fetch_range(ZIP, None, 3, timeout=0.07), (b"zip", 7, 10))
        self.assertEqual(attempts, ["direct", "gh-proxy"])

    def test_store_headers_are_bounded_by_query_deadline(self):
        import main
        with patch.object(sideload.http_client, "QUERY_SECONDS", 0.07), trickling_response(b"HTTP/1.1 200 OK\r\nX-Slow: "):
            start = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "商店目录等待响应"):
                sideload.http_client._fetch_json(sideload.http_client.STORE_URL)
        self.assertLess(time.monotonic() - start, 0.3)

    def test_query_bodies_can_stream_longer_than_the_response_wait(self):
        import main
        body = b'{"value":"0123456789"}'
        for kind in ("json", "range", "store", "chunked"):
            with self.subTest(kind=kind):
                if kind == "range":
                    prefix = f"HTTP/1.1 206 Partial Content\r\nContent-Length: {len(body)}\r\nContent-Range: bytes 0-{len(body)-1}/{len(body)}\r\n\r\n".encode()
                elif kind == "chunked":
                    prefix = f"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n{len(body):x}\r\n".encode()
                else:
                    prefix = f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                wire = body + b"\r\n0\r\n\r\n" if kind == "chunked" else body
                with patch.object(sideload.http_client, "QUERY_SECONDS", 0.07), trickling_response(prefix, tail=wire, interval=0.005):
                    if kind == "range":
                        self.assertEqual(GitHubTransport(None).fetch_range(ZIP, 0, len(body)-1, timeout=0.07), (body, 0, len(body)))
                    elif kind == "store":
                        self.assertEqual(sideload.http_client._fetch_json(sideload.http_client.STORE_URL), {"value": "0123456789"})
                    else:
                        self.assertEqual(GitHubTransport(None).fetch_bytes(API, timeout=0.07), body)

    def test_stalled_body_falls_back_without_mixing_partial_response(self):
        for kind in ("json", "range"):
            with self.subTest(kind=kind):
                transport = GitHubTransport(None, "auto")
                opened, attempts = transport.open, []
                @contextmanager
                def route_response(url, route, **kwargs):
                    attempts.append(route)
                    prefix = b"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 0-1/2\r\n" if kind == "range" else b"HTTP/1.1 200 OK\r\n"
                    prefix += b"Content-Length: 2\r\n\r\n"
                    with trickling_response(prefix + (b"x" if route == "direct" else b""), tail=b"x" if route == "direct" else b"{}", interval=0.15 if route == "direct" else 0.003):
                        with opened(url, route, **kwargs) as response:
                            yield response
                with patch.object(transport, "open", side_effect=route_response):
                    if kind == "range":
                        self.assertEqual(transport.fetch_range(ZIP, 0, 1, timeout=0.07), (b"{}", 0, 2))
                    else:
                        self.assertEqual(transport.fetch_bytes(API, timeout=0.07), b"{}")
                self.assertEqual(attempts, ["direct", "gh-proxy"])

    def test_dns_deadline_cancel_capacity_and_late_completion_are_bounded(self):
        release = threading.Event()
        entered = threading.Event()
        finished = threading.Event()
        calls = []
        def blocked(host, port, **kwargs):
            calls.append((host, port))
            entered.set()
            release.wait(2)
            if len(calls) == 4:
                finished.set()
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        slots = threading.BoundedSemaphore(4)
        try:
            with patch.object(network, "_DNS_SLOTS", slots), patch.object(network.socket, "getaddrinfo", side_effect=blocked):
                for _ in range(4):
                    start = time.monotonic()
                    with self.assertRaisesRegex(TimeoutError, "总时限"):
                        network._resolve("api.github.com", network._Budget(start + 0.025))
                    self.assertLess(time.monotonic() - start, 0.2)
                with self.assertRaisesRegex(RuntimeError, "繁忙"):
                    network._resolve("api.github.com", network._Budget(time.monotonic() + 1))
                self.assertEqual(calls, [("api.github.com", 443)] * 4)
                self.assertFalse(slots.acquire(blocking=False))
                release.set()
                self.assertTrue(finished.wait(1))
                # Slots return only after the real resolver calls have completed.
                for _ in range(100):
                    if slots.acquire(blocking=False):
                        slots.release()
                        break
                    time.sleep(0.005)
                else:
                    self.fail("DNS slot was not released")
        finally:
            release.set()
        cancel = threading.Event()
        release.clear()
        entered.clear()
        timer = threading.Timer(0.025, cancel.set)
        try:
            with patch.object(network.socket, "getaddrinfo", side_effect=blocked):
                timer.start()
                start = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, "停止"):
                    network._resolve("api.github.com", network._Budget(start + 1, cancel))
                self.assertTrue(entered.is_set())
                self.assertLess(time.monotonic() - start, 0.2)
        finally:
            release.set()
            timer.join()

    def test_pre_resolved_connection_keeps_tls_hostname_and_bounds_handshake(self):
        client, server = socket.socketpair()
        class TLSSocket:
            def __getattr__(self, name):
                return getattr(client, name)
            def do_handshake(self):
                # Deliberately ignore the socket inactivity timeout here. The
                # watchdog must terminate a handshake stuck below HTTP parsing.
                client.settimeout(None)
                client.recv(1)
        tls = TLSSocket()
        raw = Mock(connect_ex=Mock(return_value=0))
        context = Mock(wrap_socket=Mock(return_value=tls))
        try:
            start = time.monotonic()
            with network._Budget(start + 0.05) as budget:
                connection = network._HTTPSConnection("api.github.com", budget=budget, context=context)
                with patch.object(network, "_resolve", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]), patch.object(network.socket, "socket", return_value=raw):
                    with self.assertRaisesRegex(TimeoutError, "总时限"):
                        connection.connect()
            self.assertLess(time.monotonic() - start, 0.3)
            context.wrap_socket.assert_called_once_with(raw, server_hostname="api.github.com", do_handshake_on_connect=False)
        finally:
            client.close()
            server.close()

    def test_each_real_redirect_connection_uses_the_original_deadline(self):
        first = b"HTTP/1.1 302 Found\r\nLocation: https://api.github.com/next\r\nContent-Length: 0\r\n\r\n"
        second = b"HTTP/1.1 200 OK\r\nX-Slow: "
        budgets = []
        with trickling_response(first, tail=b"") as connect_first, trickling_response(second) as connect_second:
            connects = iter([connect_first, connect_second])
            def connect(connection):
                budgets.append(connection.budget)
                if len(budgets) == 1:
                    time.sleep(0.025)
                next(connects)(connection)
            with patch.object(network._HTTPSConnection, "connect", connect):
                start = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, "等待响应超时"):
                    GitHubTransport(None).fetch_bytes(API, timeout=0.07)
                self.assertLess(time.monotonic() - start, 0.3)
        self.assertEqual(len(budgets), 2)
        self.assertIs(budgets[0], budgets[1])

    def test_nonblocking_connect_is_cancelled_and_has_an_inactivity_limit(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                cancel = threading.Event()
                raw = Mock(connect_ex=Mock(return_value=errno.EINPROGRESS))
                context = Mock()
                timer = threading.Timer(0.025, cancel.set)
                def waiting(*args):
                    time.sleep(args[-1])
                    return [], [], []
                try:
                    if cancelled:
                        timer.start()
                    with network._Budget(time.monotonic() + 1, cancel, timeout=0.04) as budget:
                        connection = network._HTTPSConnection("api.github.com", budget=budget, context=context)
                        with patch.object(network, "_resolve", return_value=addresses), patch.object(network.socket, "socket", return_value=raw), patch.object(network.select, "select", side_effect=waiting):
                            with self.assertRaisesRegex(RuntimeError if cancelled else TimeoutError, "停止" if cancelled else "连接超时"):
                                connection.connect()
                        raw.close.assert_called()
                        context.wrap_socket.assert_not_called()
                finally:
                    if cancelled:
                        timer.join()

    def test_range_read_uses_partial_content_and_does_not_accept_a_larger_full_body(self):
        body = b"hello"
        response = Response(body, ZIP, {"Content-Range": "bytes 0-4/10"})
        response.status = 206
        opener = Mock(open=Mock(return_value=response))
        with patch.object(network.urllib.request, "build_opener", return_value=opener):
            data, start, total = GitHubTransport(None).fetch_range(ZIP, 0, 4)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Range"), "bytes=0-4")
        self.assertEqual((data, start, total), (body, 0, 10))
        full = Response(b"x" * 20, ZIP, {"Content-Length": "20"})
        full.status = 200
        opener.open.return_value = full
        with patch.object(network.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "范围|过大"):
                GitHubTransport(None).fetch_range(ZIP, 0, 4)

