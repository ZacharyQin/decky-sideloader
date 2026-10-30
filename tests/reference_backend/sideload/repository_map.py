"""Versioned name-to-repository defaults, separate from user bindings and ownership."""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import stat

from sideload.diagnostic_context import DiagnosticError
from sideload.github_meta import parse_github_input
from sideload.persist import save_settings

MAP_URL = "https://raw.githubusercontent.com/ZacharyQin/decky-sideloader/main/data/repositories.json"
BUNDLED_MAP = Path(__file__).resolve().parents[2] / "data" / "repositories.json"
MAX_MAP_BYTES = 1024 * 1024
MAX_REPOSITORIES = 4096


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def decode_mapping(payload: bytes) -> dict:
    try:
        if len(payload) > MAX_MAP_BYTES:
            raise ValueError()
        value = json.loads(payload, object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or set(value) != {"schema_version", "revision", "updated_at", "repositories"}:
            raise ValueError()
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError()
        if type(value["revision"]) is not int or not 1 <= value["revision"] <= 2**53 - 1:
            raise ValueError()
        if not isinstance(value["updated_at"], str) or date.fromisoformat(value["updated_at"]).isoformat() != value["updated_at"]:
            raise ValueError()
        repositories = value["repositories"]
        if not isinstance(repositories, dict) or len(repositories) > MAX_REPOSITORIES:
            raise ValueError()
        for name, repo in repositories.items():
            if not 1 <= len(name) <= 128 or name.strip() != name or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in name):
                raise ValueError()
            if not isinstance(repo, str) or len(repo) > 256 or parse_github_input(repo) != repo or repo.lower() == "steamdeckhomebrew/decky-plugin-template":
                raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise DiagnosticError("invalid_response", "仓库映射表格式无效或不受支持") from error


def read_mapping(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MAP_BYTES:
            raise DiagnosticError("invalid_response", "仓库映射表格式无效或不受支持")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return decode_mapping(stream.read(MAX_MAP_BYTES + 1))
    finally:
        os.close(descriptor)


class RepositoryMap:
    def __init__(self, bundled: Path, downloaded: Path):
        self.path = Path(downloaded)
        self.bundled = read_mapping(Path(bundled))
        self.value, self.source, self.warning = self.bundled, "bundled", None
        try:
            saved = read_mapping(self.path)
            if saved["revision"] > self.value["revision"]:
                self.value, self.source = saved, "downloaded"
            elif saved["revision"] == self.value["revision"] and saved != self.value:
                raise ValueError()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, RuntimeError):
            self.warning = "已下载的仓库映射表不可用，正在使用随包版本"

    def status(self) -> dict:
        return {"revision": self.value["revision"], "updated_at": self.value["updated_at"],
                "count": len(self.value["repositories"]), "source": self.source, "warning": self.warning}

    def install(self, payload: bytes) -> bool:
        candidate = decode_mapping(payload)
        if candidate["revision"] < self.value["revision"]:
            raise DiagnosticError("invalid_response", "远端仓库映射表版本较旧，已保留当前版本")
        if candidate["revision"] == self.value["revision"]:
            if candidate != self.value:
                raise DiagnosticError("invalid_response", "仓库映射表修订号未变化但内容不同，已保留当前版本")
            if not self.warning:
                return False
        changed = candidate != self.value
        save_settings(str(self.path), candidate)
        self.value = candidate
        self.source = "downloaded" if candidate["revision"] > self.bundled["revision"] else "bundled"
        self.warning = None
        return changed


def resolve_repository(plugin: dict, memory: dict, repositories: dict) -> tuple[str | None, str | None]:
    repo, source = plugin.get("repo"), plugin.get("repo_source")
    if source in ("user", "disabled") or plugin.get("is_self"):
        return repo, source
    if memory.get("repo_source") == "user" and memory.get("repo") and not plugin.get("memory_changed"):
        return memory["repo"], "remembered"
    if plugin["name"] in repositories:
        return repositories[plugin["name"]], "mapping"
    if source is None and memory.get("repo"):
        return memory["repo"], "remembered"
    return repo, source
