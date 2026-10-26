"""Correlated live Loader observations and channel-scoped update capability."""
import asyncio
import json
from pathlib import Path
import uuid

from sideload.ownership import _read, _text
from sideload.persist import save_settings

MAX_BYTES = 4 * 1024 * 1024


def names(value):
    if not isinstance(value, list) or len(value) > 4096 or any(not _text(n) for n in value) or len(set(value)) != len(value):
        raise ValueError("Loader 名称清单无效")
    return value


def cache_record(value):
    if not isinstance(value, dict) or set(value) != {"channel", "names"} or not isinstance(value["channel"], str) or not 1 <= len(value["channel"]) <= 4096:
        raise ValueError("Loader 更新目录无效")
    names(value["names"])
    return value


def validate_snapshot(value):
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        raise ValueError("Loader 管理清单无效")
    if not value["ok"]:
        return {"ok": False, "error": "Loader 管理状态未知"}
    plugins = value.get("plugins")
    if not isinstance(plugins, list) or len(plugins) > 4096:
        raise ValueError("Loader 管理清单无效")
    names([p.get("name") if isinstance(p, dict) else None for p in plugins])
    for p in plugins:
        if "version" not in p or not (p["version"] is None or isinstance(p["version"], str) and len(p["version"]) <= 256) or type(p.get("load_type")) is not int or type(p.get("disabled")) is not bool:
            raise ValueError("Loader 管理条目无效")
    return {"ok": True, "plugins": [{k: p[k] for k in ("name", "version", "load_type", "disabled")} for p in plugins]}


class NativeManagement:
    def __init__(self, path, emit):
        self.path, self.emit = Path(path), emit
        self.pending = {}
        self.closed = False
        try:
            self.cache = cache_record(_read(self.path, MAX_BYTES))
        except (OSError, ValueError, TypeError, RecursionError):
            self.cache = None

    async def read(self, refresh=False):
        unknown = ({"ok": False, "error": "Loader 管理状态未知"}, None)
        if self.closed or not callable(self.emit):
            return unknown
        identifier = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = (future, refresh)
        try:
            await self.emit("native_management_requested", {"id": identifier, "refresh": refresh, "cache": self.cache})
            return await asyncio.wait_for(future, 30 if refresh else 8)
        except Exception:
            if refresh:
                raise RuntimeError("Loader 管理读取超时，未替换原有记录") from None
            return unknown
        finally:
            self.pending.pop(identifier, None)

    def receive(self, identifier, payload):
        pending = self.pending.get(identifier) if isinstance(identifier, str) else None
        if not pending or pending[0].done() or self.closed:
            return
        future, refresh = pending
        try:
            if not isinstance(payload, dict) or len(json.dumps(payload).encode()) > MAX_BYTES:
                raise ValueError("Loader 响应无效")
            snapshot = validate_snapshot(payload.get("snapshot"))
            capable = payload.get("capable")
            if capable is not None:
                names(capable)
                if not snapshot["ok"] or not set(capable).issubset({p["name"] for p in snapshot["plugins"]}):
                    raise ValueError("Loader 更新能力与登记清单不匹配")
            if refresh:
                if payload.get("error") or not snapshot["ok"]:
                    raise ValueError("Loader 更新目录读取失败，未替换原有记录")
                record = cache_record(payload.get("cache"))
                if capable is None or not set(capable).issubset(record["names"]):
                    raise ValueError("Loader 更新能力与频道目录不匹配")
                save_settings(str(self.path), record)
                self.cache = record
            future.set_result((snapshot, capable))
        except (OSError, ValueError, TypeError, RecursionError) as error:
            future.set_exception(RuntimeError(str(error)))

    def close(self):
        self.closed = True
        for future, _ in self.pending.values():
            if not future.done():
                future.set_result(({"ok": False, "error": "插件正在停止"}, None))
