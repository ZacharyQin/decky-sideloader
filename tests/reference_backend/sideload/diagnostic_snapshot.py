"""Bounded, pseudonymous export snapshots. No reads can advance application state."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from datetime import datetime

from sideload.diagnostic_context import ERROR_CODES, MODULES, identifier

STAGES = {"checking", "backup", "ready", "uninstalling", "validating", "awaiting", "installing", "verified", "failed", "unknown", "cancelled", "closed"}
PAGES = {"home", "details", "install", "settings", "binding", "operation", "checks", "network", "diagnostics", "maintenance", "versions", "source", "releaseNotes", "licenses"}
ENUM_FIELDS = {
    "stage": STAGES, "status": {"running", "connecting", "downloading", "complete", "failed", "success", "cancelled", "unknown", "error", "update", "current", "ahead", "uncomparable", "unbound", "no_release", "no_asset"},
    "mode": {"direct", "auto", "gh-proxy", "ghfast"}, "route": {"direct", "gh-proxy", "ghfast"},
    "installMode": {"install", "reinstall"}, "outcome": {"success"}, "closeReason": {"verified", "manual"},
    "ownership": {"unknown", "readonly", "pending", "store", "owned", "nonstore"},
    "ownershipState": {"prepared", "writing", "confirming", "active", "released"}, "action": {"takeover", "return"},
    "source": {"manual", "scheduled", "live", "cache", "registry", "unavailable"},
    "registration": {"registered", "unknown"}, "page": PAGES, "module": MODULES,
    "step": {"download", "archive", "dependencies", "backup", "ready", "awaiting", "installing", "verified", "verification", "uninstalling"},
    "errorCode": ERROR_CODES, "system": {"linux", "darwin", "win32"}, "machine": {"x86_64", "aarch64", "arm64"},
    "steamDeckModel": {"lcd", "oled"},
}
NUM_FIELDS = {"received", "total", "attempt", "index", "count", "files", "bytes", "deleted", "skipped", "failed", "line", "column", "errno", "httpStatus", "autoCheckMinutes", "downloadTimeoutMinutes", "collectedAt", "createdAt", "updatedAt", "completedAt", "truncated", "freeBytes", "steamClientVersion"}
BOOL_FIELDS = {"available", "ok", "eligible", "loaderManaged", "cachedInStore", "memoryChanged", "frozen", "priorFrozen", "sawStart", "sawFinish", "bindingSaved", "cancelRequested", "evidenceConflict", "uninstallStarted", "uninstallBlocked", "uninstallResultReceived", "artifactDeleted", "backupDeleted", "noticeDismissed", "hasError", "autoCheck", "checkNotifications", "showHomeTaskNotice", "deletePackageAfterInstall", "stale", "loaderOk", "catalogOk", "matchesInstalled", "isSteamDeck"}
ID_FIELDS = {"pluginId", "repoId", "releaseId", "pathId", "operationId", "requestId", "ownershipId"}
HASH_FIELDS = {"fingerprint", "backend", "installedBundle", "installedFrontend", "packageHash", "archiveHash"}
VERSION_FIELDS = {"version", "currentVersion", "targetVersion", "python"}
METADATA_FIELDS = {
    "osId": r"[a-z0-9][a-z0-9._-]{0,63}", "osVariant": r"[a-z0-9][a-z0-9._-]{0,63}",
    **{key: r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,95}" for key in ("osVersion", "osBuild", "kernelVersion")},
    **{key: r"[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,95}" for key in ("osName", "deviceVendor", "deviceModel", "boardName", "productVersion")},
}
CHILD_FIELDS = {"operation", "download", "dependency", "backup", "error", "frames", "observations", "settings", "library", "plugins", "checks", "items", "cleanup", "builds", "frontend", "environment"}


def version(value):
    return value if type(value) is str and re.fullmatch(r"v?\d{1,4}(?:\.\d{1,4}){0,3}(?:-(?:pre|debug|beta|alpha|rc)(?:\.?\d{1,4})?)?", value) else None


def sanitize_snapshot(value, depth=0):
    if type(value) is not dict or depth > 6:
        return {}
    clean = {}
    for key, item in value.items():
        if key in ENUM_FIELDS and type(item) is str and item in ENUM_FIELDS[key]: clean[key] = item
        elif key in NUM_FIELDS and (item is None or type(item) is int and 0 <= item <= 2**53 - 1): clean[key] = item
        elif key in BOOL_FIELDS and (item is None or type(item) is bool): clean[key] = item
        elif key in ID_FIELDS and type(item) is str and re.fullmatch(r"[a-f0-9]{32}", item): clean[key] = item
        elif key in HASH_FIELDS and type(item) is str and re.fullmatch(r"[a-f0-9]{64}", item): clean[key] = item
        elif key in VERSION_FIELDS and version(item) is not None: clean[key] = item
        elif key in METADATA_FIELDS and type(item) is str and re.fullmatch(METADATA_FIELDS[key], item): clean[key] = item
        elif key in CHILD_FIELDS:
            if type(item) is dict: clean[key] = sanitize_snapshot(item, depth + 1)
            elif type(item) is list:
                limit = 8 if key == "frames" else 16 if key == "observations" else 512
                clean[key] = [sanitize_snapshot(row, depth + 1) for row in item[:limit] if type(row) is dict]
                if len(item) > limit: clean["truncated"] = len(item) - limit
    return clean


def read_json(path, maximum=256 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            raise OSError("snapshot file unavailable")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = json.loads(stream.read(maximum + 1))
        if type(value) is not dict: raise ValueError("snapshot object unavailable")
        return value
    finally:
        os.close(fd)


def timestamp(value):
    if type(value) is not str or len(value) > 64: return None
    try:
        parsed = datetime.fromisoformat(value)
        return int(parsed.timestamp() * 1000) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def operation_snapshot(operation):
    if type(operation) is not dict or not operation:
        return {"available": False}
    package = operation.get("package") if type(operation.get("package")) is dict else {}
    target = operation.get("target") if type(operation.get("target")) is dict else {}
    result = {"available": True, "operationId": operation.get("id"), "stage": operation.get("stage"), "mode": operation.get("network_mode"),
        "installMode": operation.get("mode"), "outcome": operation.get("outcome"), "closeReason": operation.get("close_reason"),
        "pluginId": identifier("plugin", package.get("name") or target.get("name") or operation.get("existing_name")),
        "repoId": identifier("repo", operation.get("repo")), "releaseId": identifier("release", operation.get("tag")),
        "pathId": identifier("path", target.get("path")), "targetVersion": package.get("version") or target.get("version"),
        "packageHash": package.get("sha256"), "requestId": identifier("loader-request", operation.get("request_id")),
        "hasError": bool(operation.get("error")), "error": operation.get("diagnostic_error", {})}
    result.update(createdAt=timestamp(operation.get("created_at")), completedAt=timestamp(operation.get("completed_at")))
    for key in ("saw_start", "saw_finish", "binding_saved", "cancel_requested", "evidence_conflict", "uninstall_started", "uninstall_blocked", "uninstall_result_received", "artifact_deleted", "backup_deleted", "notice_dismissed"):
        camel = re.sub(r"_([a-z])", lambda match: match[1].upper(), key)
        result[camel] = operation.get(key, False)
    for raw, key in (("download", "download"), ("dependency_download", "dependency")):
        progress = operation.get(raw)
        if type(progress) is dict: result[key] = {k: progress.get(k) for k in ("status", "route", "attempt", "received", "total", "index", "count")}
    backup = operation.get("backup")
    if type(backup) is dict: result["backup"] = {"available": not operation["backup_deleted"] if type(operation.get("backup_deleted")) is bool else None, "files": backup.get("files"), "bytes": backup.get("bytes"), "archiveHash": backup.get("sha256")}
    observations = operation.get("observations")
    result["observations"] = [{"step": row.get("id"), "status": row.get("status")} for row in observations[:16] if type(row) is dict] if type(observations) is list else []
    return sanitize_snapshot(result)


def library_snapshot(plugins, native, catalog, frozen):
    rows = []
    for plugin in plugins[:512]:
        owner = plugin.get("ownership") or {}; intent = owner.get("intent") or {}
        rows.append({"pluginId": identifier("plugin", plugin.get("name")), "repoId": identifier("repo", plugin.get("repo")),
            "currentVersion": plugin.get("version"), "ownership": owner.get("state"), "eligible": owner.get("eligible"),
            "ownershipId": intent.get("id"), "ownershipState": intent.get("state"), "priorFrozen": intent.get("prior_frozen"),
            "loaderManaged": plugin.get("loader_managed"), "registration": plugin.get("registration"),
            "cachedInStore": plugin.get("cached_in_store"), "memoryChanged": plugin.get("memory_changed"),
            "frozen": plugin.get("name") in frozen if frozen is not None else None, "hasError": bool(owner.get("error") or plugin.get("memory_error"))})
    return sanitize_snapshot({"available": True, "collectedAt": int(time.time() * 1000), "count": len(plugins), "truncated": max(0, len(plugins) - 512), "loaderOk": native.get("ok"),
        "source": catalog.get("source"), "catalogOk": not bool(catalog.get("warning")), "stale": catalog.get("stale"), "plugins": rows})


def settings_snapshot(settings):
    result = {key: settings.get(key) for key in ("autoCheck", "autoCheckMinutes", "networkMode", "checkNotifications", "showHomeTaskNotice", "deletePackageAfterInstall", "downloadTimeoutMinutes")}
    result["mode"] = result.pop("networkMode", None)
    return sanitize_snapshot(result)


def checks_snapshot(report):
    if type(report) is not dict: return {"available": False}
    items = report.get("items") if type(report.get("items")) is list else []
    return sanitize_snapshot({"available": True, "source": report.get("source"), "hasError": bool(report.get("error")), "count": len(items),
        "items": [{"pluginId": identifier("plugin", row.get("name")), "repoId": identifier("repo", row.get("repo")), "status": row.get("status"),
            "currentVersion": row.get("current"), "targetVersion": row.get("latest"), "hasError": bool(row.get("error"))} for row in items[:512] if type(row) is dict]})


def build_snapshot(plugin_dir):
    root = Path(plugin_dir) if plugin_dir else None
    if root is None: return {}
    result = {}
    try:
        package = root / "py_modules" / "sideload"
        if not package.is_dir(): package = root / "sideload"
        sources = [("main.py", root / "main.py"), *(("sideload/" + p.name, p) for p in sorted(package.glob("*.py")))]
        digest = hashlib.sha256()
        for name, path in sources:
            digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
        result["backend"] = digest.hexdigest()
        bundle = (root / "dist/index.js").read_bytes()
        result["installedBundle"] = hashlib.sha256(bundle).hexdigest()
        metadata = read_json(root / "dist/build.json", 8192)
        if metadata.get("bundle") == result["installedBundle"]:
            result["installedFrontend"] = metadata.get("fingerprint")
    except (OSError, ValueError, TypeError):
        pass
    return sanitize_snapshot(result)

