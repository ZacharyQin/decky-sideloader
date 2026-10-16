from __future__ import annotations

# Semantic index:
# - participant: docs/upstream-conflict/sideload/plugin-management.md -- normalize exact GitHub repositories and conservatively compare release versions.
import re
from typing import Any
from urllib.parse import urlparse

_REPO_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
_VERSION = re.compile(r"^[vV]?(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?(?:\.(0|[1-9]\d*))?(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$")


def parse_github_input(raw: str | None) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().removeprefix("git+")
    if text.startswith("git@github.com:"):
        text = "https://github.com/" + text[len("git@github.com:"):]
    if text.lower().startswith("github.com/"):
        text = "https://" + text
    if "://" in text:
        parsed = urlparse(text)
        if parsed.scheme not in ("https", "http") or parsed.netloc.lower() != "github.com":
            return None
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            return None
        owner, repo = parts[:2]
    else:
        parts = text.split("/")
        if len(parts) != 2:
            return None
        owner, repo = parts
    repo = repo.removesuffix(".git")
    if any(not _REPO_PART.fullmatch(p) or p in (".", "..") for p in (owner, repo)):
        return None
    return f"{owner}/{repo}"


def pick_zip_assets(assets: list[dict[str, Any]] | None, repo: str) -> list[dict[str, str]]:
    zips = []
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        name, url = asset.get("name"), asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str) and name.lower().endswith(".zip"):
            zips.append({"name": name, "url": url})
    zips.sort(key=lambda a: (0 if repo.lower() in a["name"].lower() or "plugin" in a["name"].lower() else 1, a["name"].lower()))
    return zips


def _version(value: str | None) -> tuple[tuple[int, int, int], list[str] | None] | None:
    match = _VERSION.fullmatch((value or "").strip())
    if not match:
        return None
    pre = match[4].split(".") if match[4] else None
    if pre and any(p.isdigit() and len(p) > 1 and p.startswith("0") for p in pre):
        return None
    return (int(match[1]), int(match[2] or 0), int(match[3] or 0)), pre


def compare_versions(remote: str, local: str | None) -> int | None:
    """Return -1/0/1, or None when tags cannot be compared as semver."""
    a, b = _version(remote), _version(local)
    if a is None or b is None:
        return None
    if a[0] != b[0]:
        return 1 if a[0] > b[0] else -1
    ap, bp = a[1], b[1]
    if ap == bp:
        return 0
    if ap is None:
        return 1
    if bp is None:
        return -1
    for x, y in zip(ap, bp):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return 1 if int(x) > int(y) else -1
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return 1 if x > y else -1
    return 1 if len(ap) > len(bp) else -1


def is_newer(remote: str, local: str | None) -> bool | None:
    result = compare_versions(remote, local)
    return None if result is None else result > 0


def parse_releases(payload: Any, repo: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("GitHub 发布列表格式异常")
    releases = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not tag.strip():
            continue
        title = item.get("name")
        body = item.get("body")
        releases.append({
            "tag": tag,
            "title": title if isinstance(title, str) and title.strip() else tag,
            "prerelease": bool(item.get("prerelease")),
            "body": body[:20000] if isinstance(body, str) else "",
            "published_at": item.get("published_at") if isinstance(item.get("published_at"), str) else "",
            "zips": pick_zip_assets(item.get("assets") if isinstance(item.get("assets"), list) else [], repo),
        })
    return sorted(releases, key=lambda r: r["published_at"], reverse=True)
