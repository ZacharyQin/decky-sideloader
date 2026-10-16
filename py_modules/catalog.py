"""Validated, bounded store snapshots and official registration-only fallback.

Semantic index:
- owner: docs/upstream-conflict/sideload/plugin-management.md -- keep live store membership, dated cache and repository registration distinct.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from github_meta import parse_github_input
from persist import save_settings

REGISTRY_URL = "https://raw.githubusercontent.com/SteamDeckHomebrew/decky-plugin-database/main/.gitmodules"
CACHE_TTL = 300
CACHE_MAX_AGE = 7 * 86400
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
        try:
            if self.path.stat().st_size <= MAX_CATALOG_BYTES:
                value = json.loads(self.path.read_text())
                if not isinstance(value, dict) or not isinstance(value.get("names"), list):
                    raise ValueError("invalid catalog snapshot")
                names = validate_names([{"name": n} for n in value["names"]])
                timestamp = value["timestamp"]
                if type(timestamp) in (int, float) and 0 <= self.clock() - timestamp <= CACHE_MAX_AGE:
                    self.store = (timestamp, names)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            pass

    def resolve(self):
        now = self.clock()
        warning = None
        if self.store and 0 <= now - self.store[0] < CACHE_TTL:
            return self.store[1], self._metadata("cache", self.store[0], False, None), []
        try:
            names, error = self.fetch_store()
            if error:
                raise RuntimeError(error)
            names = validate_names([{"name": n} for n in names])
            self.store = (now, names)
            save_settings(str(self.path), {"timestamp": now, "names": sorted(names)})
            return names, self._metadata("live", now, False, None), []
        except Exception as error:
            warning = str(error)
        if self.store and 0 <= now - self.store[0] <= CACHE_MAX_AGE:
            return self.store[1], self._metadata("cache", self.store[0], True, warning), []
        try:
            if not self.registry or not 0 <= now - self.registry[0] < CACHE_TTL:
                self.registry = (now, parse_gitmodules(self.fetch_registry()))
            return set(), self._metadata("registry", self.registry[0], False, warning), self.registry[1]
        except Exception as error:
            return set(), self._metadata("unavailable", None, False, f"{warning}；官方登记辅助失败：{error}"), []

    @staticmethod
    def _metadata(source, timestamp, stale, warning):
        return {"source": source, "timestamp": timestamp, "stale": stale, "warning": warning}


def registration_for(plugin, entries):
    repo = parse_github_input(plugin.get("repo"))
    return "registered" if repo and (plugin["folder"], repo.lower()) in entries else "unknown"
