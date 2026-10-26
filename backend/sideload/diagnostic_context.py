"""Diagnostic-only correlation and error classification; never serialize exception text."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import errno
from functools import wraps
import hashlib
import json
from pathlib import Path
import socket
import ssl
import urllib.error
import zipfile

CONTEXT = ContextVar("sideload_diagnostic_request", default={})
MODULES = frozenset({"main", *(path.stem for path in Path(__file__).parent.glob("*.py"))})
ERROR_CODES = frozenset({"unknown", "invalid_state", "invalid_input", "identity_changed", "ownership_blocked",
    "dns_failed", "tls_failed", "network_timeout", "connection_failed", "rate_limited", "http_failed",
    "permission_denied", "disk_full", "file_missing", "io_failed", "package_invalid", "invalid_response",
    "cancelled", "loader_failed", "evidence_conflict", "interrupted", "frontend_error"})
ERROR_TYPES = frozenset({"OSError", "PermissionError", "FileNotFoundError", "RuntimeError", "TimeoutError", "ValueError",
    "CancelledError", "HTTPError", "URLError", "SSLError", "gaierror", "BadZipFile", "DiagnosticError", "Exception"})


class DiagnosticError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.diagnostic_code = code if code in ERROR_CODES else "unknown"


def identifier(kind, value):
    if type(value) is not str or not value or len(value) > 4096:
        return None
    return hashlib.sha256((kind + ":" + value).encode("utf-8")).hexdigest()[:32]


def operation_context(method):
    @wraps(method)
    async def observed(self, *args, **kwargs):
        token = CONTEXT.set({**CONTEXT.get(), "operationId": self.operation["id"]})
        try:
            return await method(self, *args, **kwargs)
        finally:
            CONTEXT.reset(token)
    return observed


def frames(traceback):
    result = []
    while traceback is not None:
        path = Path(traceback.tb_frame.f_code.co_filename)
        if path.stem in MODULES and (path.parent == Path(__file__).parent or path.name == "main.py" and path.parent in (Path(__file__).parents[1], Path(__file__).parents[2])):
            result.append({"module": path.stem, "line": traceback.tb_lineno})
        traceback = traceback.tb_next
    return result[-8:]


def error_fields(error):
    chain, seen = [], set()
    current = error
    while isinstance(current, BaseException) and id(current) not in seen and len(chain) < 8:
        seen.add(id(current)); chain.append(current)
        reason = current.reason if isinstance(current, urllib.error.URLError) else None
        current = reason if isinstance(reason, BaseException) else current.__cause__ or current.__context__
    result = {"errorType": type(error).__name__ if type(error).__name__ in ERROR_TYPES else "Exception", "errorCode": "unknown"}
    for item in chain:
        code = getattr(item, "diagnostic_code", None)
        if code in ERROR_CODES: result["errorCode"] = code; break
        if isinstance(item, asyncio.CancelledError): code = "cancelled"
        elif isinstance(item, socket.gaierror): code = "dns_failed"
        elif isinstance(item, ssl.SSLError): code = "tls_failed"
        elif isinstance(item, TimeoutError): code = "network_timeout"
        elif isinstance(item, urllib.error.HTTPError):
            code = "rate_limited" if item.code == 429 or item.code == 403 and (item.headers or {}).get("X-RateLimit-Remaining") == "0" else "http_failed"
            result["httpStatus"] = item.code
        elif isinstance(item, OSError) and not isinstance(item, urllib.error.URLError):
            if type(item.errno) is int and 0 <= item.errno <= 65535: result["errno"] = item.errno
            code = {errno.EACCES: "permission_denied", errno.EPERM: "permission_denied", errno.ENOSPC: "disk_full", errno.ENOENT: "file_missing"}.get(item.errno, "connection_failed" if isinstance(item, ConnectionError) else "io_failed")
        elif isinstance(item, zipfile.BadZipFile): code = "package_invalid"
        elif isinstance(item, (json.JSONDecodeError, UnicodeDecodeError)): code = "invalid_response"
        elif isinstance(item, ValueError): code = "invalid_input"
        elif isinstance(item, RuntimeError): code = "invalid_state"
        else: continue
        if result["errorCode"] in ("unknown", "invalid_state", "invalid_input", "io_failed"):
            result["errorCode"] = code
    result["frames"] = [frame for item in chain for frame in frames(item.__traceback__)][-8:]
    if result["errorCode"] == "invalid_state":
        for item in chain:
            trace = item.__traceback__
            while trace is not None:
                name = trace.tb_frame.f_code.co_name
                if Path(trace.tb_frame.f_code.co_filename).parent == Path(__file__).parent:
                    if name in ("inspect_archive", "prepare_remote_binaries"): result["errorCode"] = "package_invalid"
                    elif name == "_check_prepared": result["errorCode"] = "identity_changed"
                trace = trace.tb_next
    return result
