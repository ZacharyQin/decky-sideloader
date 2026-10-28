import asyncio
import decky
from sideload.diagnostics_api import DiagnosticsAPI
from sideload.settings_api import SettingsAPI
from sideload.library import PluginLibrary
from sideload.releases_api import ReleasesAPI
from sideload.installation_api import InstallationAPI
from sideload.self_update import SelfUpdateAPI


class Plugin(DiagnosticsAPI, SettingsAPI, PluginLibrary, ReleasesAPI, InstallationAPI, SelfUpdateAPI):
    async def _main(self):
        self._diagnostic_service()
        self._diagnostic("startup")
        self._self_update_init()
        decky.logger.info("decky-sideloader starting")
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._backup_task = asyncio.create_task(self._backup_loop())

    async def _unload(self):
        self._catalog_closing = True
        if hasattr(self, "_self_record"):
            await self._self_update_unload()
        backup_task = getattr(self, "_backup_task", None)
        if backup_task:
            backup_task.cancel()
            try:
                await backup_task
            except asyncio.CancelledError:
                pass
        if hasattr(self, "_native"):
            self._native.close()
        if hasattr(self, "_sharing"):
            await self._sharing.stop(closing=True)
        worker = getattr(self, "_catalog_worker", None)
        if worker is not None:
            await asyncio.shield(worker)
        mapping_update = getattr(self, "_repository_map_update", None)
        if mapping_update is not None:
            await asyncio.gather(asyncio.shield(mapping_update), return_exceptions=True)
        reads = list(getattr(self, "_release_reads", {}).values())
        if reads:
            await asyncio.gather(*(asyncio.shield(task) for task in reads), return_exceptions=True)
        self._diagnostic("shutdown")
        decky.logger.info("decky-sideloader unloading")
        if hasattr(self, "_flow"):
            await self._flow.unload()
        task = getattr(self, "_poll_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if hasattr(self, "_diagnostics"):
            await self._diagnostics.close()

    async def _uninstall(self):
        decky.logger.info("decky-sideloader uninstall cleanup")
