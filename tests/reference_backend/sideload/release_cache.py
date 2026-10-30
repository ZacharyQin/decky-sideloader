"""Bounded, disposable successful Release snapshots, never installation authority."""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path

from sideload.persist import save_settings

MAX_RELEASE_CACHE_BYTES = 16 * 1024 * 1024


def release_identity(plugin):
    return {
        "name": plugin["name"], "folder": plugin.get("folder"), "repo": plugin.get("repo"),
        "current": plugin.get("version"),
        "ownership_id": ((plugin.get("ownership") or {}).get("intent") or {}).get("id"),
    }


def matches(snapshot, plugin):
    return bool((plugin.get("ownership") or {}).get("eligible")) and all(
        snapshot.get(key) == value for key, value in release_identity(plugin).items())


def valid_snapshot(value):
    try:
        if not isinstance(value, dict) or not all(isinstance(value[k], str) for k in ("name", "repo", "checkedAt")):
            return False
        if not datetime.fromisoformat(value["checkedAt"]).tzinfo:
            return False
        if any(value.get(k) is not None and not isinstance(value[k], str) for k in ("folder", "current", "ownership_id")):
            return False
        rows = value.get("releases")
        if not isinstance(rows, list) or len(rows) > 1000:
            return False
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ("tag", "title", "body", "published_at", "repo")):
                return False
            if row["repo"] != value["repo"] or type(row.get("has_zip")) is not bool or type(row.get("prerelease")) is not bool or row.get("comparison") not in (None, -1, 0, 1):
                return False
            if not isinstance(row.get("zips"), list) or any(not isinstance(a, dict) or not isinstance(a.get("name"), str) or not isinstance(a.get("url"), str) for a in row["zips"]):
                return False
            evidence = row.get("version_evidence")
            if evidence is not None and (not isinstance(evidence, dict) or set(evidence) != {"package_state", "package_version", "git_version"}
                    or evidence["package_state"] not in ("skipped", "unknown", "semver", "raw", "absent")
                    or any(evidence[k] is not None and not isinstance(evidence[k], str) for k in ("package_version", "git_version"))):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


class ReleaseCache:
    def __init__(self, path):
        self.path = Path(path)
        self.entries = {}
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RELEASE_CACHE_BYTES:
                    return
                raw = json.loads(stream.read(MAX_RELEASE_CACHE_BYTES + 1))
            if isinstance(raw, list):
                self.entries = {s["name"]: {key: value for key, value in s.items() if key != "skipped_tags"} for s in raw if valid_snapshot(s)}
        except (OSError, ValueError, TypeError):
            pass  # Disposable cache: a miss must not discard user settings or checks.

    def get(self, plugin):
        snapshot = self.entries.get(plugin["name"])
        return snapshot if snapshot is not None and matches(snapshot, plugin) else None

    def put(self, plugin, releases, checked_at):
        snapshot = {**release_identity(plugin), "checkedAt": checked_at, "releases": releases}
        # Never truncate release bodies or assets to make an entry fit.
        size = len(json.dumps([snapshot], ensure_ascii=False, indent=2).encode("utf-8"))
        if size > MAX_RELEASE_CACHE_BYTES:
            return snapshot
        entries = {name: item for name, item in self.entries.items() if name != plugin["name"]}
        entries[plugin["name"]] = snapshot
        while len(json.dumps(list(entries.values()), ensure_ascii=False, indent=2).encode("utf-8")) > MAX_RELEASE_CACHE_BYTES:
            del entries[next(iter(entries))]
        self.entries = entries
        try:
            save_settings(str(self.path), list(entries.values()))
        except OSError:
            pass  # A disposable disk cache failure must not erase a successful read.
        return snapshot
