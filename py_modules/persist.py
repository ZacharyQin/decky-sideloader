from __future__ import annotations

# Semantic index:
# - participant: docs/upstream-conflict/sideload/plugin-management.md -- validate notification defaults and separate scheduled reports; explicit unlink and atomic owned writes remain intact.
from datetime import datetime
import json
import os
import tempfile
from pathlib import Path
from typing import Any


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
    def report_for(key):
        report = loaded.get(key)
        try:
            if not isinstance(report, dict) or not isinstance(report.get("items"), list) or not datetime.fromisoformat(report["checkedAt"]).tzinfo:
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
        "networkMode": loaded.get("networkMode") if loaded.get("networkMode") in ("direct", "auto", "gh-proxy") else "direct",
        "checkNotifications": loaded.get("checkNotifications") if type(loaded.get("checkNotifications")) is bool else True,
        "lastCheck": report_for("lastCheck"),
        "lastScheduledCheck": report_for("lastScheduledCheck"),
    }


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
        if isinstance(binding, dict) and binding.get("disabled"):
            row["repo"], row["repo_source"] = None, "disabled"
        elif isinstance(binding, dict) and isinstance(binding.get("repo"), str) and binding["repo"].strip():
            row["repo"], row["repo_source"] = binding["repo"].strip(), "user"
        else:
            row["repo_source"] = "package.json" if row.get("repo") else None
        rows.append(row)
    return rows
