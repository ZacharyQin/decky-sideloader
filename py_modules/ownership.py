"""Durable explicit store responsibility, independent of repository bindings.

Semantic index:
- owner: docs/upstream-conflict/sideload/plugin-management.md -- validate the durable ledger and persist correlated freeze intentions before native effects.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import stat
import uuid

from github_meta import parse_github_input
from persist import save_settings

MAX_BYTES = 1024 * 1024
STATES = {"prepared", "writing", "confirming", "active", "released"}


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _read(path: Path, maximum: int):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError()
        return json.loads(data)
    finally:
        os.close(fd)


def read_frozen(home: str) -> list[str]:
    try:
        value = _read(Path(home) / "settings" / "loader.json", 4 * MAX_BYTES)
        names = value.get("frozenPlugins", []) if isinstance(value, dict) else None
        if not isinstance(names, list) or len(names) > 4096 or any(not _text(n) for n in names) or len(set(names)) != len(names):
            raise ValueError()
        return names
    except (OSError, ValueError, TypeError, RecursionError):
        raise RuntimeError("无法核验 Loader 冻结设置，请恢复来源读取后重试") from None


class Ownership:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            value = _read(self.path, MAX_BYTES)
        except FileNotFoundError:
            return {"version": 1, "entries": []}
        except (OSError, ValueError, TypeError, RecursionError):
            raise RuntimeError("接管记录损坏，已阻止管理操作") from None
        return self._validate(value)

    def _validate(self, value):
        try:
            assert isinstance(value, dict) and set(value) == {"version", "entries"} and type(value["version"]) is int and value["version"] == 1
            assert isinstance(value["entries"], list) and len(value["entries"]) <= 512
            identities, ids = set(), set()
            for row in value["entries"]:
                assert isinstance(row, dict) and set(row) == {"id", "name", "folder", "repo", "prior_frozen", "action", "state"}
                assert _text(row["name"]) and _text(row["folder"]) and row["folder"] not in (".", "..") and not any(c in row["folder"] for c in "/\\")
                assert isinstance(row["id"], str) and re.fullmatch(r"[a-f0-9]{32}", row["id"])
                assert isinstance(row["repo"], str) and len(row["repo"]) <= 256 and parse_github_input(row["repo"]) == row["repo"]
                assert type(row["prior_frozen"]) is bool and row["action"] in ("takeover", "return") and row["state"] in STATES
                assert row["state"] != "active" or row["action"] == "takeover"
                assert row["state"] != "released" or row["action"] == "return"
                identity = (row["name"], row["folder"])
                assert identity not in identities and row["id"] not in ids
                identities.add(identity); ids.add(row["id"])
            return value
        except (AssertionError, KeyError, TypeError):
            raise RuntimeError("接管记录损坏，已阻止管理操作") from None

    def save(self, value):
        self._validate(value)
        if len(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")) > MAX_BYTES:
            raise RuntimeError("接管记录已达容量上限")
        save_settings(str(self.path), value)

    def entry(self, plugin, ledger=None):
        return next((r for r in (ledger or self.load())["entries"] if (r["name"], r["folder"]) == (plugin["name"], plugin["folder"])), None)

    def resolve(self, plugins, frozen, ledger=None, error=None):
        ledger = ledger or self.load()
        counts = {p["name"]: sum(q["name"] == p["name"] for q in plugins) for p in plugins}
        rows = []
        for plugin in plugins:
            row = dict(plugin)
            entry = self.entry(row, ledger)
            state, eligible = "unknown", False
            if row.get("is_self") or counts[row["name"]] != 1:
                state = "readonly"
            elif error:
                state = "unknown"
            elif entry and entry["state"] not in ("active", "released"):
                state = "pending"
            elif entry and entry["state"] == "released":
                state = "store"
            elif entry and entry["state"] == "active":
                state = "owned" if frozen is not None and row["name"] in frozen else "pending"
                eligible = state == "owned"
                row["repo"], row["repo_source"] = entry["repo"], "user"
            elif row.get("in_store") is True or row.get("cached_in_store") is True:
                state = "store"
            elif row.get("in_store") is False:
                state, eligible = "nonstore", True
            elif row.get("repo_source") == "user" and parse_github_input(row.get("repo") or ""):
                state, eligible = "repair", True
            row["ownership"] = {"state": state, "eligible": eligible, "intent": copy.deepcopy(entry), "error": error}
            row["sideload"] = eligible
            rows.append(row)
        return rows

    def begin(self, plugin, repo, action, frozen):
        ledger = self.load()
        old = self.entry(plugin, ledger)
        if old and old["state"] not in ("active", "released"):
            raise RuntimeError("已有未确认的接管操作，请先回查或显式继续")
        if action == "return":
            if not old or old["state"] != "active":
                raise RuntimeError("此插件没有可交还的接管记录")
        elif action != "takeover" or plugin.get("in_store") is not True:
            raise RuntimeError("只有当前商店已收录的插件可以显式接管")
        if action == "takeover" and old and old["state"] == "active":
            raise RuntimeError("此插件已经接管")
        parsed = parse_github_input(repo) if isinstance(repo, str) else None
        if not parsed:
            raise RuntimeError("请确认有效的 GitHub 仓库")
        row = {"id": uuid.uuid4().hex, "name": plugin["name"], "folder": plugin["folder"], "repo": old["repo"] if action == "return" else parsed,
               "prior_frozen": old["prior_frozen"] if action == "return" else plugin["name"] in frozen,
               "action": action, "state": "prepared"}
        ledger["entries"] = [r for r in ledger["entries"] if (r["name"], r["folder"]) != (row["name"], row["folder"])] + [row]
        self.save(ledger)
        return copy.deepcopy(row)

    def transition(self, identifier, state, frozen=None):
        ledger = self.load()
        row = next((r for r in ledger["entries"] if r["id"] == identifier), None)
        if not row:
            raise RuntimeError("接管意图已变化，请重新读取")
        if state == "writing":
            if row["state"] not in ("prepared", "writing", "confirming", "active"):
                raise RuntimeError("原生写入已领取，请先回查结果，不会自动重放")
        elif state == "confirming":
            if row["state"] != "writing":
                raise RuntimeError("原生写入状态已变化")
        elif state == "complete":
            if row["state"] == "prepared":
                raise RuntimeError("尚未领取原生冻结写入，请显式继续操作")
            desired = True if row["action"] == "takeover" else row["prior_frozen"]
            if frozen is None or (row["name"] in frozen) != desired:
                raise RuntimeError("原生冻结回查未匹配，接管结果仍未确认")
            state = "active" if row["action"] == "takeover" else "released"
        else:
            raise RuntimeError("接管阶段无效")
        row["state"] = state
        self.save(ledger)
        return copy.deepcopy(row)
