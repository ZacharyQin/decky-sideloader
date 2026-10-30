import json
import os
import ssl
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from sideload.catalog import MAX_CATALOG_BYTES, validate_names
from sideload.github_transport import GitHubTransport, QUERY_SECONDS, _Budget, _HTTPSHandler, content_length, read_chunk
from sideload.diagnostic_context import error_fields, identifier
from urllib.parse import urlsplit


STORE_URL = "https://plugins.deckbrew.xyz/plugins"
USER_AGENT = "decky-sideloader"
SYSTEM_CA_FILES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
)


def _ssl_context() -> ssl.SSLContext:
    for ca_file in SYSTEM_CA_FILES:
        if not os.path.isfile(ca_file):
            continue
        try:
            return ssl.create_default_context(cafile=ca_file)
        except (OSError, ssl.SSLError):
            continue
    return ssl.create_default_context()


class _StoreRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl != STORE_URL:
            if fp is not None:
                fp.close()
            raise RuntimeError("商店目录重定向来源不受支持")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_json(url: str, *, accept: str = "application/json", max_bytes: int | None = None, transport=None) -> object:
    if transport is not None or url.startswith(("https://api.github.com/", "https://raw.githubusercontent.com/")):
        return (transport or GitHubTransport(_ssl_context())).fetch_json(url, accept=accept, max_bytes=max_bytes)
    if url != STORE_URL:
        raise RuntimeError("查询地址不受支持")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with _Budget(time.monotonic() + QUERY_SECONDS, timeout=QUERY_SECONDS, message="商店目录等待响应超时") as budget:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _StoreRedirect(), _HTTPSHandler(_ssl_context(), budget))
            with opener.open(request, timeout=budget.check()) as response:
                budget.check()
                if response.geturl() != STORE_URL:
                    raise RuntimeError("商店目录来源不受支持")
                budget.progress("商店目录接收正文等待超时")
                maximum = max_bytes or MAX_CATALOG_BYTES
                length = content_length(response.headers, maximum)
                data = bytearray()
                while True:
                    budget.check()
                    chunk = read_chunk(response, min(65536, maximum + 1 - len(data)))
                    budget.check()
                    if not chunk:
                        break
                    budget.progress()
                    data.extend(chunk)
                    if len(data) > maximum:
                        raise RuntimeError("商店响应过大")
                if length is not None and len(data) != length:
                    raise RuntimeError("商店目录长度不完整")
                return json.loads(data)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        raise RuntimeError(str(error)) from error


def _fetch_store_names(url: str) -> tuple[set[str], str | None]:
    try:
        return validate_names(_fetch_json(url)), None
    except RuntimeError as error:
        return set(), str(error)


class _ObservedTransport(GitHubTransport):
    def __init__(self, *args, diagnostic, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostic = diagnostic

    @contextmanager
    def open(self, url, route, **kwargs):
        attempt = self.routes(url).index(route) + 1
        host = urlsplit(url).hostname
        kind = {"api.github.com": "api", "raw.githubusercontent.com": "raw", "plugins.deckbrew.xyz": "store"}.get(host, "asset" if host in {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"} else "other")
        fields = {"route": route, "attempt": attempt, "kind": kind, "resourceId": identifier("resource", urlsplit(url)._replace(query="", fragment="").geturl())}
        started = time.monotonic()
        self.diagnostic("network_attempt", **fields, status="connecting")
        try:
            with super().open(url, route, **kwargs) as response:
                self.diagnostic("network_attempt", **fields, status="ok", httpStatus=getattr(response, "status", 0), durationMs=max(0, int((time.monotonic() - started) * 1000)))
                yield response
        except Exception as error:
            self.diagnostic("network_attempt", **fields, status="failed", durationMs=max(0, int((time.monotonic() - started) * 1000)), **error_fields(error))
            raise
