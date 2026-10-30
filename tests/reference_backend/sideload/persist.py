from __future__ import annotations

from datetime import datetime
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from sideload.github_meta import parse_github_input
from sideload.github_transport import MODES


def load_settings(path: str) -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(value, dict):
            loaded = value
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    minutes = loaded.get("autoCheckMinutes", 360)
    if type(minutes) is not int or minutes < 60 or minutes > 1440:
        minutes = 360
    timeout = loaded.get("downloadTimeoutMinutes", 3)
    if type(timeout) is not int or timeout not in (3, 5, 10, 15, 30):
        timeout = 3
    def report_for(key):
        report = loaded.get(key)
        try:
            if not isinstance(report, dict) or not isinstance(report.get("items"), list) or not datetime.fromisoformat(report["checkedAt"]).tzinfo:
                return None
            if report.get("fullCheckedAt") is not None and not datetime.fromisoformat(report["fullCheckedAt"]).tzinfo:
                return None
            if report.get("source") not in (None, "manual", "scheduled") or report.get("error") is not None and not isinstance(report["error"], str):
                return None
            if any(not isinstance(item, dict) or not isinstance(item.get("name"), str) or item.get("status") not in {"update", "current", "ahead", "uncomparable", "unbound", "no_release", "no_asset", "error"} for item in report["items"]):
                return None
            return report
        except (KeyError, ValueError, TypeError):
            return None
    return {
        "bindings": loaded.get("bindings") if isinstance(loaded.get("bindings"), dict) else {},
        "autoCheck": bool(loaded.get("autoCheck", False)),
        "autoCheckMinutes": minutes,
        "networkMode": loaded.get("networkMode") if isinstance(loaded.get("networkMode"), str) and loaded["networkMode"] in MODES else "direct",
        "checkNotifications": loaded.get("checkNotifications") if type(loaded.get("checkNotifications")) is bool else True,
        "showHomeTaskNotice": loaded.get("showHomeTaskNotice") if type(loaded.get("showHomeTaskNotice")) is bool else True,
        "deletePackageAfterInstall": loaded.get("deletePackageAfterInstall") if type(loaded.get("deletePackageAfterInstall")) is bool else True,
        "downloadTimeoutMinutes": timeout,
        "skippedReleases": _skipped_releases(loaded.get("skippedReleases")),
        "lastCheck": report_for("lastCheck"),
        "lastScheduledCheck": report_for("lastScheduledCheck"),
    }


def skipped_tags(settings: dict[str, Any], name: str, repo: str | None) -> list[str]:
    entry = settings.get("skippedReleases", {}).get(name) if isinstance(name, str) else None
    if not isinstance(entry, dict) or not repo or entry.get("repo") != repo:
        return []
    tags = entry.get("tags")
    return list(tags) if isinstance(tags, list) else []


def _skipped_releases(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for name, entry in list(raw.items())[:64]:
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or not isinstance(entry, dict):
            continue
        repo = parse_github_input(entry.get("repo")) if isinstance(entry.get("repo"), str) else None
        tags = []
        for tag in entry.get("tags") if isinstance(entry.get("tags"), list) else []:
            if isinstance(tag, str) and tag.strip() and len(tag) <= 128 and not any(ord(c) < 32 for c in tag) and tag not in tags:
                tags.append(tag)
            if len(tags) == 20:
                break
        if repo and tags:
            cleaned[name] = {"repo": repo, "tags": tags}
    return cleaned


def save_settings(path: str, data: dict[str, Any]) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{file.name}.", suffix=".tmp", dir=file.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, file)
    finally:
        # Only the exclusively created temporary file is ours to clean up.
        temp.unlink(missing_ok=True)


def apply_bindings(plugins: list[dict[str, Any]], bindings: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for plugin in plugins:
        row = dict(plugin)
        binding = bindings.get(row["name"])
        if isinstance(binding, dict) and "folder" in binding:
            package_repo = parse_github_input(row.get("repo") or "")
            saved_repo = binding.get("package_repo")
            saved_repo_valid = saved_repo is None or isinstance(saved_repo, str) and parse_github_input(saved_repo) == saved_repo
            row["binding_changed"] = binding["folder"] != row["folder"] or binding.get("package_name") != row.get("package_name") or not saved_repo_valid or bool(saved_repo and package_repo and saved_repo.lower() != package_repo.lower())
            row["binding_confirmed"] = not row["binding_changed"]
        if isinstance(binding, dict) and binding.get("disabled"):
            row["repo"], row["repo_source"] = None, "disabled"
        elif isinstance(binding, dict) and isinstance(binding.get("repo"), str) and binding["repo"].strip():
            row["repo"], row["repo_source"] = binding["repo"].strip(), "user"
        else:
            row["repo_source"] = "package.json" if row.get("repo") else None
        rows.append(row)
    return rows
