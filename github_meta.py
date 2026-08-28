from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_GITHUB_REPO = re.compile(
    r"(?:github\.com[:/]|git@github\.com:)(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_SHORT_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")


def parse_github_input(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if _SHORT_REPO.match(text) and "://" not in text and "github.com" not in text.lower():
        owner, repo = text.split("/", 1)
        return f"{owner}/{repo.removesuffix('.git')}"
    match = _GITHUB_REPO.search(text.replace("git+", ""))
    if match:
        return f"{match.group('owner')}/{match.group('repo').removesuffix('.git')}"
    parsed = urlparse(text)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1].removesuffix('.git')}"


def pick_zip_assets(assets: list[dict[str, Any]] | None, repo: str) -> list[dict[str, str]]:
    zips: list[dict[str, str]] = []
    for asset in assets or []:
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if not name.lower().endswith(".zip"):
            continue
        zips.append({"name": name, "url": url})
    repo_l = repo.lower()
    zips.sort(
        key=lambda item: (
            0 if repo_l in item["name"].lower() or "plugin" in item["name"].lower() else 1,
            item["name"].lower(),
        )
    )
    return zips


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    text = value.strip()
    if text[:1] in {"v", "V"}:
        text = text[1:]
    parts: list[int] = []
    for piece in text.split("."):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            break
    if not parts:
        return None
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def is_newer(remote: str, local: str | None) -> bool | None:
    remote_v = _version_tuple(remote)
    local_v = _version_tuple(local or "")
    if remote_v is None or local_v is None:
        return None
    return remote_v > local_v


def parse_releases(payload: Any, repo: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("github releases payload is not a list")
    releases: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not tag.strip():
            continue
        zips = pick_zip_assets(item.get("assets") if isinstance(item.get("assets"), list) else [], repo)
        title = item.get("name")
        releases.append(
            {
                "tag": tag,
                "title": title if isinstance(title, str) and title.strip() else tag,
                "prerelease": bool(item.get("prerelease")),
                "zips": zips,
            }
        )
    return releases
