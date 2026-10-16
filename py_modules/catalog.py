"""Validated, bounded store snapshots and official registration-only fallback.

Semantic index:
- owner: docs/upstream-conflict/sideload/plugin-management.md -- persist identity-matched local relationships and store history; fetch catalogs only for explicit refresh.
"""
from __future__ import annotations

import json
import math
import os
import stat
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from github_meta import parse_github_input
from persist import save_settings

REGISTRY_URL = "https://raw.githubusercontent.com/SteamDeckHomebrew/decky-plugin-database/main/.gitmodules"
CACHE_TTL = 300
MAX_CATALOG_BYTES = 4 * 1024 * 1024
MAX_NAMES = 4096


def validate_names(payload):
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_NAMES:
        raise RuntimeError("商店目录为空、格式异常或超过上限")
    names = set()
    for item in payload:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not 1 <= len(name) <= 128 or not name.strip() or any(ord(c) < 32 for c in name):
            raise RuntimeError("商店目录包含损坏条目")
        names.add(name)
    return names


def parse_gitmodules(text):
    sections = re.split(r'(?m)(?=^\[submodule ")', text)
    entries = set()
    count = 0
    for section in sections:
        if not section.strip() or section.lstrip().startswith("#") and "[submodule" not in section:
            continue
        lines = [line.strip() for line in section.splitlines() if line.strip() and not line.lstrip().startswith(("#", ";"))]
        try:
            match = re.fullmatch(r'\[submodule "(plugins/[A-Za-z0-9_.-]+)"\]', lines[0])
            if not match:
                raise ValueError()
            fields = {}
            for line in lines[1:]:
                key, separator, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if separator != "=" or key not in {"path", "url", "branch"} or key in fields or not value:
                    raise ValueError()
                fields[key] = value
            path, url = fields["path"], fields["url"]
            if not re.fullmatch(r"plugins/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", path) or match[1] != path:
                raise ValueError()
        except (IndexError, KeyError, ValueError) as error:
            raise RuntimeError("官方登记数据库格式异常或不完整") from error
        count += 1
        if count > MAX_NAMES:
            raise RuntimeError("官方登记数据库超过上限")
        if url.startswith("git@github.com:"):
            normalized = "https://github.com/" + url.split(":", 1)[1]
        else:
            normalized = url
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in normalized) or not parsed.path.strip("/"):
            raise RuntimeError("官方登记数据库包含损坏仓库地址")
        repo = parse_github_input(normalized)
        if parsed.netloc == "github.com" and (not repo or len(parsed.path.strip("/").split("/")) != 2):
            raise RuntimeError("官方登记数据库包含损坏 GitHub 仓库地址")
        # Non-GitHub registrations are valid records, but not GitHub identity evidence.
        if repo and repo.lower() != "steamdeckhomebrew/decky-plugin-template":
            entries.add((path.split("/", 1)[1], repo.lower()))
    if not count:
        raise RuntimeError("官方登记数据库为空")
    return sorted(entries)


class Catalog:
    def __init__(self, path, fetch_store, fetch_registry, clock=time.time):
        self.path = Path(path)
        self.fetch_store, self.fetch_registry, self.clock = fetch_store, fetch_registry, clock
        self.store = self.registry = None
        self.records = []
        self.store_history = set()
        self.memory_error = None
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_size > MAX_CATALOG_BYTES:
                    raise ValueError()
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = stream.read(MAX_CATALOG_BYTES + 1)
                if len(data) > MAX_CATALOG_BYTES:
                    raise ValueError()
                value = json.loads(data)
            finally:
                os.close(fd)
            self._validate(value)
            if value["timestamp"] is not None:
                self.store = (value["timestamp"], set(value["names"]))
            self.records = value.get("plugins", [])
            self.store_history = set(value.get("store_history", value["names"]))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError):
            self.memory_error = "本地插件记录无法读取，请核对后重试"

    @staticmethod
    def _identity(plugin):
        return (plugin["name"], plugin["folder"], plugin.get("package_name"))

    @staticmethod
    def _text(value):
        return isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)

    def _validate(self, value):
        if not isinstance(value, dict) or set(value) not in ({"timestamp", "names"}, {"version", "timestamp", "names", "plugins", "store_history"}):
            raise ValueError()
        modern = "version" in value
        if modern and (type(value["version"]) is not int or value["version"] != 1):
            raise ValueError()
        timestamp = value["timestamp"]
        if timestamp is None and modern and value["names"] == []:
            pass
        elif type(timestamp) not in (int, float) or not math.isfinite(timestamp) or not 0 <= timestamp <= self.clock():
            raise ValueError()
        else:
            validate_names([{"name": n} for n in value["names"]])
        history = value.get("store_history", value["names"])
        if not isinstance(history, list) or len(history) > MAX_NAMES or len(set(history)) != len(history):
            raise ValueError()
        if history:
            validate_names([{"name": n} for n in history])
        records = value.get("plugins", [])
        if not isinstance(records, list) or len(records) > 512:
            raise ValueError()
        keys = set()
        for row in records:
            if not isinstance(row, dict) or set(row) != {"name", "folder", "package_name", "package_repo", "repo", "repo_source", "in_store", "timestamp"}:
                raise ValueError()
            if not self._text(row["name"]) or not self._text(row["folder"]) or row["folder"] in (".", "..") or any(c in row["folder"] for c in "/\\"):
                raise ValueError()
            if row["package_name"] is not None and not self._text(row["package_name"]):
                raise ValueError()
            for field in ("package_repo", "repo"):
                if row[field] is not None and (not self._text(row[field]) or parse_github_input(row[field]) != row[field]):
                    raise ValueError()
            if row["repo_source"] not in (None, "user", "package.json") or row["in_store"] is not None and type(row["in_store"]) is not bool:
                raise ValueError()
            if type(row["timestamp"]) not in (int, float) or not math.isfinite(row["timestamp"]) or not 0 <= row["timestamp"] <= self.clock():
                raise ValueError()
            key = row["name"], row["folder"]
            if key in keys:
                raise ValueError()
            keys.add(key)

    def _save(self, store, records):
        if self.memory_error:
            raise RuntimeError(self.memory_error)
        history = self.store_history | (store[1] if store else set())
        value = {"version": 1, "timestamp": store[0] if store else None, "names": sorted(store[1]) if store else [], "plugins": records, "store_history": sorted(history)}
        self._validate(value)
        if len(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")) > MAX_CATALOG_BYTES:
            raise RuntimeError("本地插件记录已达容量上限")
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise RuntimeError("本地插件记录文件不安全")
        except FileNotFoundError:
            pass
        save_settings(str(self.path), value)
        self.store, self.records, self.store_history = store, records, history

    def memory_for(self, plugin):
        related = [row for row in self.records if row["name"] == plugin["name"]]
        row = next((row for row in related if self._identity(row) == self._identity(plugin)), None)
        repo = parse_github_input(plugin.get("repo") or "")
        changed = bool(related and row is None) or bool(row and row["package_repo"] and repo and row["package_repo"].lower() != repo.lower())
        known_store = plugin["name"] in self.store_history
        return {"in_store": None if changed else True if known_store else row["in_store"] if row else None,
                "repo": row["repo"] if row and not changed else None,
                "repo_source": row["repo_source"] if row and not changed else None, "changed": changed}

    def remember(self, plugin, repo, source):
        parsed = parse_github_input(repo or "")
        if not parsed or source not in ("user", "package.json"):
            raise RuntimeError("仓库记录无效")
        old = self.memory_for(plugin)
        row = {"name": plugin["name"], "folder": plugin["folder"], "package_name": plugin.get("package_name"),
               "package_repo": parse_github_input(plugin.get("repo") or ""), "repo": parsed, "repo_source": source,
               "in_store": True if plugin["name"] in self.store_history else old["in_store"], "timestamp": self.clock()}
        records = [r for r in self.records if r["name"] != plugin["name"]] + [row]
        self._save(self.store, records)

    def resolve(self, refresh=False, plugins=()):
        if type(refresh) is not bool:
            raise RuntimeError("刷新参数无效")
        if not refresh or self.memory_error:
            if self.store:
                return self.store[1], self._metadata("cache", self.store[0], self.clock() - self.store[0] >= CACHE_TTL, self.memory_error), []
            if self.registry and not self.memory_error:
                return set(), self._metadata("registry", self.registry[0], False, None), self.registry[1]
            return set(), self._metadata("unavailable", None, False, self.memory_error), []
        now = self.clock()
        try:
            names, error = self.fetch_store()
            if error:
                raise RuntimeError(error)
            names = validate_names([{"name": n} for n in names])
            records = [dict(row) for row in self.records]
            counts = {p["name"]: sum(q["name"] == p["name"] for q in plugins) for p in plugins}
            for plugin in plugins:
                if counts[plugin["name"]] != 1 or self.memory_for(plugin)["changed"]:
                    continue
                repo = parse_github_input(plugin.get("repo") or "")
                previous = next((r for r in records if self._identity(r) == self._identity(plugin)), None)
                row = {"name": plugin["name"], "folder": plugin["folder"], "package_name": plugin.get("package_name"),
                       "package_repo": repo or (previous["package_repo"] if previous else None),
                       "repo": previous["repo"] if previous and previous["repo_source"] == "user" else repo or (previous["repo"] if previous else None),
                       "repo_source": "user" if previous and previous["repo_source"] == "user" else "package.json" if repo else previous["repo_source"] if previous else None,
                       "in_store": plugin["name"] in names or plugin["name"] in self.store_history, "timestamp": now}
                records = [r for r in records if (r["name"], r["folder"]) != (row["name"], row["folder"])] + [row]
            self._save((now, names), records)
            return names, self._metadata("live", now, False, None), []
        except (OSError, ValueError, RuntimeError, TypeError) as error:
            warning = str(error)
        if self.store:
            return self.store[1], self._metadata("cache", self.store[0], True, warning), []
        try:
            self.registry = (now, parse_gitmodules(self.fetch_registry()))
            return set(), self._metadata("registry", now, False, warning), self.registry[1]
        except Exception as error:
            return set(), self._metadata("unavailable", None, False, f"{warning}；官方登记辅助失败：{error}"), []

    @staticmethod
    def _metadata(source, timestamp, stale, warning):
        return {"source": source, "timestamp": timestamp, "stale": stale, "warning": warning}


def registration_for(plugin, entries):
    repo = parse_github_input(plugin.get("repo"))
    return "registered" if repo and (plugin["folder"], repo.lower()) in entries else "unknown"
