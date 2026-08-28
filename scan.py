from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_GITHUB_REPO = re.compile(
    r"(?:github\.com[:/]|git@github\.com:)(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


def detect_github_repo(package: dict[str, Any] | None) -> str | None:
    if not package:
        return None
    candidates: list[str] = []
    repository = package.get("repository")
    if isinstance(repository, str):
        candidates.append(repository)
    elif isinstance(repository, dict):
        url = repository.get("url")
        if isinstance(url, str):
            candidates.append(url)
    homepage = package.get("homepage")
    if isinstance(homepage, str):
        candidates.append(homepage)
    for raw in candidates:
        parsed = _parse_github(raw)
        if parsed:
            return parsed
    return None


def _parse_github(raw: str) -> str | None:
    match = _GITHUB_REPO.search(raw.replace("git+", ""))
    if not match:
        parsed = urlparse(raw)
        if parsed.netloc.lower() != "github.com":
            return None
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1]
    else:
        owner, repo = match.group("owner"), match.group("repo")
    repo = repo.removesuffix(".git")
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def read_plugins_dir(plugins_dir: str) -> list[dict[str, Any]]:
    root = Path(plugins_dir)
    if not root.is_dir():
        return []
    plugins: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        plugin_json_path = child / "plugin.json"
        if not child.is_dir() or not plugin_json_path.is_file():
            continue
        try:
            plugin_json = json.loads(plugin_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = plugin_json.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        package: dict[str, Any] | None = None
        package_path = child / "package.json"
        if package_path.is_file():
            try:
                loaded = json.loads(package_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    package = loaded
            except (OSError, json.JSONDecodeError):
                package = None
        version = None
        if package and isinstance(package.get("version"), str):
            version = package["version"]
        plugins.append(
            {
                "name": name,
                "folder": child.name,
                "version": version,
                "author": plugin_json.get("author") if isinstance(plugin_json.get("author"), str) else "",
                "repo": detect_github_repo(package),
            }
        )
    return plugins


def classify_plugins(
    local: list[dict[str, Any]],
    store_names: set[str],
    self_name: str,
    store_ok: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plugin in local:
        name = plugin["name"]
        is_self = name == self_name
        in_store = (name in store_names) if store_ok else None
        row = dict(plugin)
        row["is_self"] = is_self
        row["in_store"] = in_store
        row["sideload"] = bool(store_ok and in_store is False and not is_self)
        rows.append(row)
    return rows
