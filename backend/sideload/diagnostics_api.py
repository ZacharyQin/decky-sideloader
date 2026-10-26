import asyncio
import json
from pathlib import Path
import decky
from sideload.diagnostics import Diagnostics
from sideload.diagnostics_sharing import DiagnosticSharing
from sideload.paths import _settings_path
import time
import re
import stat
from sideload.rpc import _observed_rpc
from sideload.diagnostic_context import error_fields
from sideload.diagnostic_snapshot import build_snapshot, checks_snapshot, operation_snapshot, read_json, sanitize_snapshot, settings_snapshot


class DiagnosticsAPI:
    def _diagnostic(self, event, **fields):
        try:
            service = getattr(self, "_diagnostics", None)
            if service is not None:
                service.record(event, **fields)
        except Exception:
            pass

    def _diagnostic_service(self):
        if not hasattr(self, "_diagnostics"):
            self._diagnostics = Diagnostics(getattr(decky, "DECKY_PLUGIN_LOG_DIR", ""), getattr(decky, "DECKY_USER_HOME", ""),
                {"sdk": getattr(decky, "__version__", ""), "backend": getattr(decky, "DECKY_PLUGIN_VERSION", ""),
                 "loader": getattr(decky, "DECKY_VERSION", "")})
            self._diagnostics.builds = build_snapshot(getattr(decky, "DECKY_PLUGIN_DIR", ""))
            try:
                self._diagnostics.attach_sdk(decky.logger, getattr(decky, "DECKY_PLUGIN_LOG", ""))
            except Exception:
                pass
        return self._diagnostics

    def _sharing_service(self):
        if not hasattr(self, "_sharing"):
            self._sharing = DiagnosticSharing(self._diagnostic_service())
        return self._sharing

    async def _diagnostic_summary(self, steam_version=None):
        # Read existing state only. Never initialize InstallFlow, refresh a listing or recover a task.
        operation = getattr(getattr(self, "_flow", None), "operation", None)
        def collect():
            details = {"collectedAt": int(time.time() * 1000), "library": getattr(self, "_diagnostic_library", {"available": False})}
            if type(steam_version) is int and 0 < steam_version <= 2**53 - 1:
                details["environment"] = {"steamClientVersion": steam_version}
            plugin_dir = getattr(decky, "DECKY_PLUGIN_DIR", "")
            if plugin_dir and Path(plugin_dir).is_dir():
                installed = build_snapshot(plugin_dir)
                service = self._diagnostic_service()
                running_backend = service.builds.get("backend")
                service.builds = {key: value for key, value in installed.items() if key != "backend"}
                if running_backend: service.builds["backend"] = running_backend
            current = operation
            if current is None and getattr(decky, "DECKY_PLUGIN_RUNTIME_DIR", None):
                try:
                    current = read_json(Path(decky.DECKY_PLUGIN_RUNTIME_DIR) / "installations/operation.json")
                except FileNotFoundError:
                    pass
                except (OSError, ValueError) as error:
                    details["operation"] = {"available": False, "error": error_fields(error)}
            if type(current) is dict and current.get("backup") and type(current.get("id")) is str and re.fullmatch(r"[a-f0-9]{32}", current["id"]):
                current = dict(current)
                try:
                    archive = Path(decky.DECKY_PLUGIN_RUNTIME_DIR) / "installations" / current["id"] / "data-backup.zip"
                    current["backup_deleted"] = not stat.S_ISREG(archive.lstat().st_mode)
                except FileNotFoundError:
                    current["backup_deleted"] = True
                except (OSError, AttributeError):
                    current["backup_deleted"] = None
            details.setdefault("operation", operation_snapshot(current))
            try:
                settings = read_json(_settings_path(), 4 * 1024 * 1024)
                details["settings"] = {"available": True, **settings_snapshot(settings)}
                details["checks"] = checks_snapshot(settings.get("lastCheck"))
                mode = settings.get("networkMode")
            except (OSError, ValueError, AttributeError) as error:
                details["settings"] = {"available": False, "error": error_fields(error)}
                mode = None
            return {"operationId": (current or {}).get("id"), "stage": (current or {}).get("stage"), "mode": mode, "details": sanitize_snapshot(details)}
        return await asyncio.to_thread(collect)

    async def get_diagnostics_addresses(self) -> dict:
        return self._sharing_service().available()

    async def get_diagnostics_share(self) -> dict:
        return self._sharing_service().status()

    async def start_diagnostics_share(self, address: str, session: str, previous: str | None = None, steam_version: int | None = None) -> dict:
        return await self._sharing_service().start(address, session, await self._diagnostic_summary(steam_version), previous)

    async def stop_diagnostics_share(self, session: str) -> dict:
        return await self._sharing_service().stop(session)

    async def record_diagnostic_events(self, events: list, dropped: int = 0) -> dict:
        return self._diagnostic_service().record_batch(events, dropped)

    async def get_diagnostics_status(self) -> dict:
        return self._diagnostic_service().status()

    async def export_diagnostics(self, steam_version: int | None = None) -> dict:
        return await self._diagnostic_service().export(await self._diagnostic_summary(steam_version))

    @_observed_rpc
    async def ping(self) -> str:
        return "pong"

    async def get_open_source_notices(self) -> dict:
        path = Path(decky.DECKY_PLUGIN_DIR) / "dist" / "notices.json"
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return json.loads(text)
