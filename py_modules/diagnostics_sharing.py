"""On-demand, bounded IPv4 diagnostic pickup. No request data reaches logging.

Semantic index:
- owner: docs/upstream-conflict/sideload/diagnostics-sharing.md -- serve only the current owned export with in-memory credentials and bounded admission and lifetime.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import math
import os
import re
import secrets
import socket
import struct
import time

from diagnostics import EXPORT_NAME, PACKAGE_BYTES, ZIP_MARKER, _directory, _open

TTL = 300
REQUEST_SECONDS = 5
TRANSFER_SECONDS = 30
CHUNK = 64 * 1024
MAX_CLIENTS = 4
MAX_TRANSFERS = 2
MAX_DOWNLOADS = 3
PRIVATE = tuple(ipaddress.IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_ipv4(value):
    if type(value) is not str:
        return False
    try:
        address = ipaddress.IPv4Address(value)
        return str(address) == value and any(address in network for network in PRIVATE)
    except ValueError:
        return False


def lan_addresses():
    """Linux live interface flags and primary IPv4; fail closed on unsupported hosts."""
    import fcntl
    result = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        for _, name in socket.if_nameindex()[:128]:
            if len(name.encode()) > 15:
                continue
            request = struct.pack("256s", name.encode())
            try:
                flags = struct.unpack_from("H", fcntl.ioctl(probe, 0x8913, request), 16)[0]
                # IFF_UP, IFF_RUNNING, not IFF_LOOPBACK.
                if flags & 0x41 != 0x41 or flags & 0x8:
                    continue
                value = socket.inet_ntoa(fcntl.ioctl(probe, 0x8915, request)[20:24])
                if private_ipv4(value):
                    result.add(value)
            except OSError:
                continue
    return sorted(result)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink, info.st_uid)


class DiagnosticSharing:
    def __init__(self, diagnostics, *, addresses=lan_addresses):
        self.diagnostics = diagnostics
        self.addresses = addresses
        self.phase = "idle"
        self.session = None
        self.token = None
        self.address = None
        self.port = None
        self.package = None
        self.expected = None
        self.sha256 = None
        self.deadline = 0
        self.successes = 0
        self.reserved = 0
        self.clients = {}
        self.listener = None
        self.accept_task = None
        self.watch_task = None
        self.prepare_task = None
        self.drain_task = None
        self.closed = False

    def available(self):
        try:
            return {"addresses": self.addresses()}
        except Exception:
            return {"addresses": []}

    def status(self):
        result = {"state": self.phase}
        if self.session is not None:
            result["session"] = self.session
        if self.package is not None:
            result["package"] = dict(self.package)
        if self.phase == "active":
            result.update(url=f"http://{self.address}:{self.port}/pickup/{self.token}",
                          remaining=max(0, math.ceil(self.deadline - time.monotonic())),
                          downloads=self.successes)
        if self.phase == "error":
            result["error"] = "领取未启动或已失效，请检查所选局域网地址和诊断文件后重试"
        return result

    def _current(self):
        return self.address in self.available()["addresses"]

    def _open_package(self, *, initial=False):
        fd = _directory(self.diagnostics._export_directory(), create=False)
        file = None
        try:
            name = self.package["file"]
            if not EXPORT_NAME.fullmatch(name):
                raise OSError("invalid package")
            file = _open(fd, name)
            info = os.fstat(file)
            if not 0 < info.st_size <= PACKAGE_BYTES or info.st_size != self.package["bytes"] or info.st_mode & 0o777 != 0o600:
                raise OSError("unsafe package")
            current = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if identity(current) != identity(info) or not initial and identity(info) != self.expected:
                raise OSError("changed package")
            digest = hashlib.sha256()
            marker = os.read(file, len(ZIP_MARKER))
            if marker != ZIP_MARKER:
                raise OSError("invalid package")
            digest.update(marker)
            remaining = info.st_size - len(marker)
            while remaining:
                data = os.read(file, min(CHUNK, remaining))
                if not data:
                    raise OSError("truncated package")
                digest.update(data)
                remaining -= len(data)
            if identity(os.fstat(file)) != identity(info):
                raise OSError("changed package")
            value = digest.hexdigest()
            if initial:
                self.expected, self.sha256 = identity(info), value
            elif value != self.sha256:
                raise OSError("changed package")
            os.lseek(file, 0, os.SEEK_SET)
            return file
        except BaseException:
            if file is not None:
                os.close(file)
            raise
        finally:
            os.close(fd)

    def _unchanged(self, file):
        fd = _directory(self.diagnostics._export_directory(), create=False)
        try:
            return (identity(os.fstat(file)) == self.expected and
                    identity(os.stat(self.package["file"], dir_fd=fd, follow_symlinks=False)) == self.expected)
        finally:
            os.close(fd)

    def _path_current(self):
        fd = file = None
        try:
            fd = _directory(self.diagnostics._export_directory(), create=False)
            file = _open(fd, self.package["file"])
            return identity(os.fstat(file)) == self.expected
        except OSError:
            return False
        finally:
            if file is not None:
                os.close(file)
            if fd is not None:
                os.close(fd)

    async def start(self, address, session, summary, previous=None):
        if type(session) is not str or re.fullmatch(r"[a-f0-9]{32}", session) is None or previous is not None and (type(previous) is not str or re.fullmatch(r"[a-f0-9]{32}", previous) is None):
            raise RuntimeError("领取请求无效")
        if type(address) is not str:
            raise RuntimeError("所选地址无效")
        if self.closed:
            raise RuntimeError("插件正在停止")
        if session == self.session:
            if address != self.address:
                raise RuntimeError("领取意图地址不匹配")
            if self.prepare_task and not self.prepare_task.done():
                await asyncio.shield(self.prepare_task)
            return self.status()  # Idempotent, including stopped/failed intents. Never replay.
        if previous != self.session:
            raise RuntimeError("领取意图已变化，请刷新状态后重试")
        if self.phase in ("preparing", "active", "draining") or self.diagnostics.export_task and not self.diagnostics.export_task.done():
            raise RuntimeError("诊断导出或领取正在进行，请先停止领取")
        if type(address) is not str or address not in self.available()["addresses"]:
            raise RuntimeError("所选地址不在可用局域网中，请刷新后重试")
        self.session, self.address = session, address
        self.phase = self.diagnostics.share_phase = "preparing"
        self.package = self.expected = self.sha256 = None
        self.successes = self.reserved = 0
        self.prepare_task = asyncio.create_task(self._prepare(summary))
        await asyncio.shield(self.prepare_task)
        return self.status()

    async def _prepare(self, summary):
        listener = None
        try:
            package = await self.diagnostics.export(summary, _for_share=True)
            if self.phase != "preparing":
                return
            if package["state"] != "success" or not self._current():
                raise OSError("pickup unavailable")
            self.package = package
            file = self._open_package(initial=True)
            os.close(file)  # No retained inode while idle or between requests.
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setblocking(False)
            listener.bind((self.address, 0))
            listener.listen(MAX_CLIENTS)
            self.listener = listener
            self.port = listener.getsockname()[1]
            self.token = secrets.token_hex(32)
            self.deadline = time.monotonic() + TTL
            self.phase = self.diagnostics.share_phase = "active"
            self.accept_task = asyncio.create_task(self._accept())
            self.watch_task = asyncio.create_task(self._watch())
        except Exception:
            if listener is not None:
                listener.close()
            self.listener = None
            self.token = None
            if self.phase == "preparing":
                self.phase = "error"
                self.diagnostics.share_phase = None
        finally:
            self.prepare_task = None

    async def _accept(self):
        loop = asyncio.get_running_loop()
        try:
            while self.phase == "active":
                client, _ = await loop.sock_accept(self.listener)
                client.setblocking(False)
                try:
                    client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, CHUNK)
                    client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16384)
                except OSError:
                    client.close()
                    raise
                if len(self.clients) >= MAX_CLIENTS or self.phase != "active":
                    client.close()
                    await asyncio.sleep(0)  # A saturated accept backlog must not starve TTL or drains.
                    continue
                # Reserve admission before task creation, including eager task factories.
                self.clients[client] = None
                try:
                    task = asyncio.create_task(self._client(client))
                    if client in self.clients:
                        self.clients[client] = task
                except BaseException:
                    self.clients.pop(client, None)
                    client.close()
                    raise
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        except Exception:
            self._begin_stop("error")

    async def _watch(self):
        try:
            while self.phase == "active":
                await asyncio.sleep(min(1, max(0, self.deadline - time.monotonic())))
                if time.monotonic() >= self.deadline:
                    self._begin_stop("expired")
                elif not self._current() or not self._path_current():
                    self._begin_stop("error")
        except asyncio.CancelledError:
            pass
        except Exception:
            self._begin_stop("error")

    async def _headers(self, client):
        loop = asyncio.get_running_loop()
        data = bytearray()
        async with asyncio.timeout(REQUEST_SECONDS):
            while b"\r\n\r\n" not in data:
                block = await loop.sock_recv(client, 2048)
                if not block:
                    raise ValueError("incomplete request")
                data.extend(block)
                line_end = data.find(b"\r\n")
                if (line_end < 0 and len(data) > 2048) or line_end > 2048 or len(data) > 2048 + 16384:
                    raise ValueError("request limit")
            head, body = bytes(data).split(b"\r\n\r\n", 1)
            lines = head.split(b"\r\n")
            if len(lines) - 1 > 32 or len(head) - len(lines[0]) > 16384 or body:
                raise ValueError("header limit or body")
            method, route, version = lines[0].split(b" ")
            if method not in (b"GET", b"HEAD") or version != b"HTTP/1.1":
                raise ValueError("unsupported request")
            expected = ("/pickup/" + (self.token or "")).encode("ascii")
            if not hmac.compare_digest(route, expected) or not self.token:
                raise ValueError("invalid route")
            headers = {}
            for line in lines[1:]:
                name, value = line.split(b":", 1)
                if not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(c < 32 and c != 9 or c > 126 for c in value):
                    raise ValueError("invalid header")
                name, value = name.lower(), value.strip()
                if name in headers:
                    raise ValueError("duplicate header")
                headers[name] = value
            if headers.get(b"host") != f"{self.address}:{self.port}".encode("ascii"):
                raise ValueError("invalid host")
            if any(name in headers for name in (b"range", b"transfer-encoding", b"expect", b"upgrade")) or headers.get(b"content-length", b"0") != b"0":
                raise ValueError("unsupported body or range")
            if any(b"prefetch" in re.split(rb"[,;\t ]+", headers.get(name, b"").lower())
                   for name in (b"purpose", b"sec-purpose")):
                # Reject before opening the ZIP or reserving a download slot.
                raise ValueError("prefetch is not a download")
            return method

    async def _client(self, client):
        file = None
        reserved = False
        exhausted = False
        loop = asyncio.get_running_loop()
        try:
            method = await self._headers(client)
            if self.phase != "active" or time.monotonic() >= self.deadline or not self._current():
                self._begin_stop("expired" if time.monotonic() >= self.deadline else "error")
                return
            if method == b"GET":
                if self.reserved >= MAX_TRANSFERS or self.successes + self.reserved >= MAX_DOWNLOADS:
                    await self._reject(client, b"429 Too Many Requests")
                    return
                self.reserved += 1
                reserved = True
            transfer_deadline = time.monotonic() + TRANSFER_SECONDS
            async with asyncio.timeout(TRANSFER_SECONDS):
                try:
                    file = self._open_package()
                except OSError:
                    self._begin_stop("error")
                    return
                if time.monotonic() >= min(transfer_deadline, self.deadline):
                    return
                headers = (f"HTTP/1.1 200 OK\r\nContent-Type: application/zip\r\nContent-Length: {self.package['bytes']}\r\n"
                           f"Content-Disposition: attachment; filename=\"{self.package['file']}\"\r\n"
                           "Cache-Control: no-store\r\nPragma: no-cache\r\nX-Content-Type-Options: nosniff\r\n"
                           "Referrer-Policy: no-referrer\r\nConnection: close\r\n\r\n").encode("ascii")
                await loop.sock_sendall(client, headers)
                if method == b"GET":
                    remaining = self.package["bytes"]
                    while remaining:
                        if time.monotonic() >= transfer_deadline:
                            return
                        if self.phase != "active" or time.monotonic() >= self.deadline:
                            self._begin_stop("expired")
                            return
                        try:
                            unchanged = self._unchanged(file)
                        except OSError:
                            unchanged = False
                        if not unchanged:
                            self._begin_stop("error")
                            return
                        data = os.read(file, min(CHUNK, remaining))
                        if not data:
                            raise OSError("truncated package")
                        await loop.sock_sendall(client, data)
                        remaining -= len(data)
                    try:
                        unchanged = self._unchanged(file)
                    except OSError:
                        unchanged = False
                    if not unchanged:
                        self._begin_stop("error")
                        return
                    if time.monotonic() >= min(transfer_deadline, self.deadline):
                        return
                    self.reserved -= 1
                    reserved = False
                    self.successes += 1
                    exhausted = self.successes == MAX_DOWNLOADS
        except (ValueError, asyncio.TimeoutError):
            if file is None:
                await self._reject(client, b"400 Bad Request")
        except asyncio.CancelledError:
            pass
        except Exception:
            # No exception/request/peer stringification, including asyncio task reports.
            pass
        finally:
            if file is not None:
                os.close(file)
            client.close()
            self.clients.pop(client, None)
            if reserved:
                self.reserved -= 1
            if exhausted:
                self._begin_stop("exhausted")

    async def _reject(self, client, status):
        try:
            await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(client, b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"), 0.2)
        except (Exception, asyncio.CancelledError):
            pass

    def _begin_stop(self, reason):
        if self.phase == "draining":
            return self.drain_task
        if self.phase not in ("preparing", "active"):
            return None
        self.phase = self.diagnostics.share_phase = "draining"
        self.token = None  # Revoke before awaiting anything.
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        tasks = [task for task in (self.accept_task, self.watch_task) if task is not None]
        tasks += [task for task in self.clients.values() if task is not None]
        for client in self.clients:
            client.close()
        for task in tasks:
            task.cancel()
        self.drain_task = asyncio.create_task(self._drain(tasks, reason))
        return self.drain_task

    async def _drain(self, tasks, reason):
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.prepare_task and not self.prepare_task.done():
            await asyncio.shield(self.prepare_task)  # Existing builder owns its bounded temporary cleanup.
        self.clients.clear()
        self.accept_task = self.watch_task = None
        self.phase = reason
        self.diagnostics.share_phase = None
        self.drain_task = None

    async def stop(self, session=None, *, closing=False):
        if closing:
            self.closed = True
        elif type(session) is not str or session != self.session:
            raise RuntimeError("领取状态已变化，请刷新后重试")
        task = self._begin_stop("stopped")
        if task:
            await asyncio.shield(task)
        return self.status()
