from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.parse import quote, urlparse

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


def embedded_version(text: str | None) -> str | None:
    """Return the only semver embedded in a tag or filename, without a leading v."""
    if not isinstance(text, str) or not text:
        return None
    found = []
    index = 0
    while index < len(text):
        char = text[index]
        starts = char.isdigit() or char in "vV" and index + 1 < len(text) and text[index + 1].isdigit()
        if starts and not (index and text[index - 1].isalnum()):
            end, best = index + 1, None
            while end <= len(text) and (text[end - 1].isalnum() or text[end - 1] in ".+-"):
                if _version(text[index:end]):
                    best = end
                end += 1
            if best:
                token = text[index:best]
                canonical = token[1:] if token[:1] in "vV" else token
                if canonical not in found:
                    found.append(canonical)
                index = best
                continue
        index += 1
    return found[0] if len(found) == 1 else None


def label_version(tag: str | None, names) -> str | None:
    tag_version = embedded_version(tag)
    file_versions = []
    for name in names or []:
        version = embedded_version(name if isinstance(name, str) else None)
        if version and version not in file_versions:
            file_versions.append(version)
    if tag_version and any(version != tag_version for version in file_versions):
        return None
    if tag_version:
        return tag_version
    return file_versions[0] if len(file_versions) == 1 else None


def assess_checked_release(releases, current, *, package_state="skipped", package_version=None, git_version=None) -> dict[str, str | None]:
    """Choose the comparable release. Package semver outranks a tag, filename, or git tree."""
    if not releases:
        return {"status": "no_release", "latest": None, "notice": None}
    packaged = next((item for item in releases if isinstance(item, dict) and item.get("has_zip")), None)
    if packaged is None:
        return {"status": "no_asset", "latest": releases[0].get("tag") if isinstance(releases[0], dict) else None, "notice": None}
    notice = f"最新发布 {releases[0].get('tag')} 没有安装包" if packaged is not releases[0] else None
    tag = packaged.get("tag") if isinstance(packaged.get("tag"), str) else None
    names = [item.get("name") for item in packaged.get("zips") or [] if isinstance(item, dict)]
    label = packaged.get("version") if isinstance(packaged.get("version"), str) else label_version(tag, names)

    def finish(status, latest, extra=None):
        text = notice
        if extra:
            text = f"{text}，{extra}" if text else extra
        return {"status": status, "latest": latest, "notice": text}

    if package_state == "semver" and isinstance(package_version, str) and _version(package_version):
        return _compared(package_version.strip(), current, tag, finish)
    if package_state == "raw":
        return finish("uncomparable", package_version.strip() if isinstance(package_version, str) and package_version.strip() else tag)
    if package_state == "absent":
        return finish("uncomparable", tag, "安装包没有可读取的版本")
    if isinstance(label, str) and _version(label):
        return _compared(label, current, tag, finish)
    if isinstance(git_version, str) and _version(git_version):
        return _compared(git_version.strip(), current, tag, finish, "版本来自仓库源文件，安装时会再核对安装包")
    return finish("uncomparable", tag)


def _compared(version, current, tag, finish, extra=None):
    comparison = compare_versions(version, current)
    if comparison is None:
        return finish("uncomparable", tag, extra)
    return finish({1: "update", 0: "current", -1: "ahead"}[comparison], version, extra)


def git_tree_version(repo: str, tag: str, plugin_name: str, fetch_json) -> str | None:
    """Read the tag's root package version only when plugin.json names the same plugin."""
    try:
        plugin = _git_json(repo, tag, "plugin.json", fetch_json)
        package = _git_json(repo, tag, "package.json", fetch_json)
    except Exception:
        return None
    if not isinstance(plugin, dict) or plugin.get("name") != plugin_name or not isinstance(package, dict):
        return None
    version = package.get("version")
    if not isinstance(version, str) or _version(version.strip()) is None:
        return None
    return version.strip()


def _git_json(repo: str, tag: str, path: str, fetch_json):
    payload = fetch_json(
        f"https://api.github.com/repos/{repo}/contents/{path}?ref={quote(tag, safe='')}",
        accept="application/vnd.github+json",
        max_bytes=65536,
    )
    if not isinstance(payload, dict) or payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        return None
    raw = base64.b64decode("".join(payload["content"].split()), validate=False)
    if len(raw) > 65536:
        return None
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else None



def is_newer(remote: str, local: str | None) -> bool | None:
    result = compare_versions(remote, local)
    return None if result is None else result > 0


def _release_download_count(assets: Any) -> int | None:
    if not isinstance(assets, list):
        return None
    total = 0
    for asset in assets:
        count = asset.get("download_count") if isinstance(asset, dict) else None
        if type(count) is not int or count < 0:
            return None
        total += count
    return total


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
            "download_count": _release_download_count(item.get("assets")),
            "zips": pick_zip_assets(item.get("assets") if isinstance(item.get("assets"), list) else [], repo),
        })
    return sorted(releases, key=lambda r: r["published_at"], reverse=True)
