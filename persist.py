from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_settings(path: str) -> dict[str, Any]:
    file = Path(path)
    if not file.is_file():
        return {
            "bindings": {},
            "autoCheck": False,
            "autoCheckMinutes": 360,
        }
    try:
        loaded = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "bindings": {},
            "autoCheck": False,
            "autoCheckMinutes": 360,
        }
    if not isinstance(loaded, dict):
        return {
            "bindings": {},
            "autoCheck": False,
            "autoCheckMinutes": 360,
        }
    bindings = loaded.get("bindings")
    if not isinstance(bindings, dict):
        bindings = {}
    minutes = loaded.get("autoCheckMinutes", 360)
    if not isinstance(minutes, int) or minutes <= 0:
        minutes = 360
    return {
        "bindings": bindings,
        "autoCheck": bool(loaded.get("autoCheck")),
        "autoCheckMinutes": minutes,
    }


def save_settings(path: str, data: dict[str, Any]) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_bindings(plugins: list[dict[str, Any]], bindings: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plugin in plugins:
        row = dict(plugin)
        binding = bindings.get(row["name"])
        repo = None
        source = None
        if isinstance(binding, dict) and isinstance(binding.get("repo"), str) and binding["repo"].strip():
            repo = binding["repo"].strip()
            source = "user"
        elif isinstance(row.get("repo"), str) and row["repo"]:
            repo = row["repo"]
            source = "package.json"
        row["repo"] = repo
        row["repo_source"] = source
        rows.append(row)
    return rows
