"""Bounded GitHub HTTPS transport; routing never changes canonical source identity.

Semantic index:
- owner: docs/upstream-conflict/sideload/plugin-management.md -- constrain every GitHub hop and retry only transient network failures through the fixed proxy.
"""
from __future__ import annotations

import errno
import http.client
import io
import json
import re
import select
import socket
import threading
import ssl
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlsplit

PROXY_ORIGIN = "https://gh-proxy.org"
MODES = {"direct", "auto", "gh-proxy"}
USER_AGENT = "github-sideload"
_DOWNLOAD_PATH = re.compile(r"^/[^/]+/[^/]+/releases/download/[^/]+/.+$")


def allowed_url(url: str, kind: str, *, initial: bool = False, proxy: bool = False) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url:
            return False
        if proxy and parsed.netloc == "gh-proxy.org":
            return allowed_url(parsed.path[1:] + ("?" + parsed.query if parsed.query else ""), kind, initial=initial)
        if kind == "download":
            if initial:
                return parsed.netloc == "github.com" and bool(_DOWNLOAD_PATH.fullmatch(parsed.path))
            return parsed.netloc in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
        return parsed.netloc in {"api.github.com", "raw.githubusercontent.com"}
    except (ValueError, TypeError):
        return False


def retryable(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 429 or 500 <= error.code <= 599 or error.code == 403 and (error.headers or {}).get("X-RateLimit-Remaining") == "0"
    if isinstance(error, urllib.error.URLError):
        return retryable(error.reason) if isinstance(error.reason, Exception) else False
    if isinstance(error, (TimeoutError, ConnectionError, ssl.SSLError, http.client.IncompleteRead)):
        return True
    return isinstance(error, OSError) and error.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT} or isinstance(error, socket.gaierror)


def network_error(error: Exception) -> RuntimeError:
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 429 or error.code == 403 and (error.headers or {}).get("X-RateLimit-Remaining") == "0":
            retry = "请稍后重试"
            try:
                reset = datetime.fromtimestamp(float(error.headers.get("X-RateLimit-Reset", ""))).astimezone()
                retry = f"请在 {reset.strftime('%H:%M:%S %z')} 后重试"
            except (ValueError, OverflowError, OSError):
                pass
            return RuntimeError(f"GitHub 请求已限流，{retry}")
        return RuntimeError(f"HTTP {error.code} {error.url}")
    return RuntimeError(str(error))


# A stalled system resolver cannot be interrupted by Python. Only these bounded,
# file-free daemon calls may outlive a request; the HTTP/download worker never does.
_DNS_SLOTS = threading.BoundedSemaphore(4)
_HOSTS = {"api.github.com", "raw.githubusercontent.com", "github.com", "gh-proxy.org",
          "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class _Budget:
    def __init__(self, expires, cancel=None, timeout=20, message="GitHub 请求超过总时限"):
        self.expires, self.cancel, self.timeout, self.message = expires, cancel, timeout, message
        self.sockets = set()
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.watcher = None

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise RuntimeError("下载已停止，尚未交给 Loader")
        remaining = self.expires - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(self.message)
        return min(self.timeout, remaining)

    def attach(self, sock):
        with self.lock:
            self.sockets.add(sock)
        self.check()

    def replace(self, old, new):
        with self.lock:
            self.sockets.discard(old)
            self.sockets.add(new)
        self.check()

    def interrupt(self):
        with self.lock:
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _watch(self):
        while not self.done.wait(0.01):
            try:
                self.check()
            except (RuntimeError, TimeoutError):
                self.interrupt()
                return

    def __enter__(self):
        self.check()
        self.watcher = threading.Thread(target=self._watch, daemon=True, name="github-deadline")
        self.watcher.start()
        return self

    def __exit__(self, *_):
        self.done.set()
        self.interrupt()
        with self.lock:
            for sock in self.sockets:
                sock.close()
            self.sockets.clear()
        self.watcher.join()


def _resolve(host, budget):
    if host not in _HOSTS:
        raise RuntimeError("GitHub DNS 主机不受支持")
    budget.check()
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        raise RuntimeError("GitHub DNS 查询繁忙，请稍后重试")
    ready, result = threading.Event(), []

    def resolve():
        try:
            result.append(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception as error:
            result.append(error)
        finally:
            slots.release()
            ready.set()

    try:
        threading.Thread(target=resolve, daemon=True, name="github-dns").start()
    except Exception:
        slots.release()
        raise
    while not ready.wait(min(0.01, budget.check())):
        pass
    budget.check()
    if isinstance(result[0], Exception):
        raise result[0]
    return result[0]


class _GuardedRead(io.RawIOBase):
    def __init__(self, raw, sock, budget):
        self.raw, self.sock, self.budget = raw, sock, budget

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(self.budget.check())
        try:
            count = self.raw.readinto(buffer)
        except Exception:
            self.budget.check()
            raise
        self.budget.check()
        return count

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _GuardedSocket:
    def __init__(self, sock, budget):
        self.sock, self.budget = sock, budget

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def makefile(self, mode, *args, **kwargs):
        # HTTPResponse's status/header/chunk/trailer readline all use this reader.
        return io.BufferedReader(_GuardedRead(self.sock.makefile(mode, buffering=0), self.sock, self.budget))

    def sendall(self, data):
        self.sock.settimeout(self.budget.check())
        self.sock.sendall(data)
        self.budget.check()


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *, budget, **kwargs):
        super().__init__(host, **kwargs)
        self.budget = budget

    def connect(self):
        if self.port != 443 or self._tunnel_host:
            raise RuntimeError("GitHub 连接目标不受支持")
        last_error = None
        for family, kind, proto, _, address in _resolve(self.host, self.budget):
            sock = socket.socket(family, kind, proto)
            try:
                self.budget.attach(sock)
                connect_expires = time.monotonic() + self.budget.check()
                sock.setblocking(False)
                code = sock.connect_ex(address)
                if code not in (0, errno.EISCONN):
                    if code not in (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR):
                        raise OSError(code, "GitHub 连接失败")
                    while True:
                        wait = min(0.01, self.budget.check(), connect_expires - time.monotonic())
                        if wait <= 0:
                            raise TimeoutError("GitHub 连接超时")
                        _, writable, exceptional = select.select([], [sock], [sock], wait)
                        if writable or exceptional:
                            code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                            if code:
                                raise OSError(code, "GitHub 连接失败")
                            break
                self.budget.check()
                sock.settimeout(self.budget.check())
                # Retain the URL hostname for SNI and certificate verification.
                tls = self._context.wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
                self.budget.replace(sock, tls)
                sock = tls
                tls.settimeout(self.budget.check())
                tls.do_handshake()
                self.budget.check()
                self.sock = _GuardedSocket(tls, self.budget)
                return
            except Exception as error:
                sock.close()
                self.budget.check()
                last_error = error
        if last_error is not None:
            raise last_error
        raise OSError("GitHub DNS 未返回地址")


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, context, budget):
        super().__init__(context=context)
        self.budget = budget

    def https_open(self, request):
        def connection(host, **kwargs):
            return _HTTPSConnection(host, budget=self.budget, **kwargs)
        return self.do_open(connection, request, context=self._context)


class _Redirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, kind, proxy):
        self.kind, self.proxy = kind, proxy
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > 5 or not allowed_url(newurl, self.kind, proxy=self.proxy):
            if fp is not None:
                fp.close()
            raise RuntimeError("GitHub 重定向到了不受支持的地址，或跳转过多")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubTransport:
    def __init__(self, context, mode="direct", observed=None):
        if mode not in MODES:
            raise RuntimeError("GitHub 连接模式无效")
        self.context, self.mode, self.observed = context, mode, observed

    def routes(self):
        return ("direct", "gh-proxy") if self.mode == "auto" else (self.mode,)

    @contextmanager
    def open(self, url, route, *, kind="query", accept="application/vnd.github+json", timeout=20, budget=None):
        if not allowed_url(url, kind, initial=True):
            raise RuntimeError("GitHub 原始 HTTPS 地址不受支持")
        if budget is None:
            with _Budget(time.monotonic() + 60, timeout=timeout) as owned:
                with self.open(url, route, kind=kind, accept=accept, timeout=timeout, budget=owned) as response:
                    yield response
            return
        proxy = route == "gh-proxy"
        address = PROXY_ORIGIN + "/" + url if proxy else url
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirect(kind, proxy), _HTTPSHandler(self.context, budget))
        # Only these public headers are sent. No caller-supplied credentials or cookies.
        request = urllib.request.Request(address, headers={"User-Agent": USER_AGENT, "Accept": accept})
        response = None
        try:
            budget.check()
            response = opener.open(request, timeout=min(timeout, budget.check()))
            budget.check()
            if not allowed_url(response.geturl(), kind, proxy=proxy):
                raise RuntimeError("GitHub 响应来源不受支持")
            if self.observed and kind == "query":
                self.observed({"route": route, "timestamp": time.time()})
            yield response
        except Exception:
            budget.check()
            raise
        finally:
            if response is not None:
                response.close()

    def fetch_bytes(self, url, *, accept="application/vnd.github+json", max_bytes=8 * 1024 * 1024, timeout=20, deadline=60):
        with _Budget(time.monotonic() + deadline, timeout=timeout) as budget:
            for attempt, route in enumerate(self.routes()):
                try:
                    with self.open(url, route, accept=accept, timeout=timeout, budget=budget) as response:
                        length = content_length(response.headers, max_bytes)
                        data = bytearray()
                        while True:
                            budget.check()
                            chunk = read_chunk(response, min(65536, max_bytes + 1 - len(data)))
                            budget.check()
                            if not chunk:
                                break
                            data.extend(chunk)
                            if len(data) > max_bytes:
                                raise RuntimeError("GitHub 响应过大")
                        if length is not None and len(data) != length:
                            raise RuntimeError("GitHub 响应长度不完整")
                        return bytes(data)
                except Exception as error:
                    if isinstance(error, urllib.error.HTTPError):
                        error.close()
                    if attempt + 1 < len(self.routes()) and retryable(error) and time.monotonic() < budget.expires:
                        continue
                    raise network_error(error) from error

    def fetch_json(self, url, *, accept="application/vnd.github+json", max_bytes=None):
        # Parsing is outside retry: a proxy must not turn invalid data into a new attempt.
        try:
            return json.loads(self.fetch_bytes(url, accept=accept, max_bytes=max_bytes if max_bytes is not None else 8 * 1024 * 1024))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError("GitHub JSON 响应格式异常") from error


def read_chunk(response, size):
    # Body reads remain small; the guarded raw reader also bounds HTTP framing.
    return response.read1(size) if hasattr(response, "read1") else response.read(size)


def content_length(headers, maximum):
    value = headers.get("Content-Length")
    if hasattr(headers, "get_all") and len(headers.get_all("Content-Length", [])) > 1 or value is not None and headers.get("Transfer-Encoding"):
        raise RuntimeError("Content-Length 重复或与传输编码冲突")
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value) or len(value) > 20:
        raise RuntimeError("Content-Length 不合法")
    total = int(value)
    if total > maximum:
        raise RuntimeError("响应过大，超过大小上限")
    return total
