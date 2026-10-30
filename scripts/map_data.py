"""Build-time validation for the maintained repository map. Not shipped at runtime."""
from datetime import date
import json
import re


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate mapping key")
        result[key] = value
    return result


def decode_mapping(data):
    if len(data) > 1024 * 1024:
        raise ValueError("Repository map exceeds 1 MiB")
    value = json.loads(data, object_pairs_hook=_unique)
    if not isinstance(value, dict) or set(value) != {"schema_version", "revision", "updated_at", "repositories"}:
        raise ValueError("Invalid repository map fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported repository map schema")
    if type(value["revision"]) is not int or not 1 <= value["revision"] <= 2**53 - 1:
        raise ValueError("Invalid repository map revision")
    if not isinstance(value["updated_at"], str) or date.fromisoformat(value["updated_at"]).isoformat() != value["updated_at"]:
        raise ValueError("Invalid repository map date")
    repositories = value["repositories"]
    if not isinstance(repositories, dict) or len(repositories) > 4096:
        raise ValueError("Invalid repository map entries")
    for name, repo in repositories.items():
        if not 1 <= len(name) <= 128 or name.strip() != name or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in name):
            raise ValueError("Invalid plugin registration name")
        if (not isinstance(repo, str) or len(repo) > 256
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
                or any(p in (".", "..") for p in repo.split("/")) or repo.endswith(".git")
                or repo.lower() == "steamdeckhomebrew/decky-plugin-template"):
            raise ValueError("Invalid canonical GitHub repository")
    return value


def read_mapping(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Repository map is unavailable")
    return decode_mapping(path.read_bytes())
