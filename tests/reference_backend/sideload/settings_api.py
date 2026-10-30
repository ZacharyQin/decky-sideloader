import asyncio
from pathlib import Path
import decky
from sideload import http_client
from sideload.github_transport import MODES
from sideload.paths import _settings_path
from sideload.persist import load_settings, save_settings
from sideload.rpc import _observed_rpc
from sideload.repository_map import BUNDLED_MAP, MAP_URL, MAX_MAP_BYTES, RepositoryMap


class SettingsAPI:
    def _transport(self):
        loop = asyncio.get_running_loop()
        def observe(snapshot):
            loop.call_soon_threadsafe(setattr, self, "_last_route", snapshot)
        return http_client._ObservedTransport(http_client._ssl_context(), load_settings(_settings_path())["networkMode"], observe, diagnostic=self._diagnostic)

    def _query_fetch(self):
        transport = self._transport()
        return lambda url, **kwargs: http_client._fetch_json(url, transport=transport, **kwargs)

    def _repository_map_service(self):
        if not hasattr(self, "_repository_map"):
            self._repository_map = RepositoryMap(BUNDLED_MAP, Path(decky.DECKY_PLUGIN_RUNTIME_DIR) / "repository-map.json")
        return self._repository_map

    @_observed_rpc
    async def get_repository_map_status(self) -> dict:
        return self._repository_map_service().status()

    def _require_repository_map_idle(self):
        self._require_binding_idle(decky.DECKY_PLUGIN_NAME)
        operation = getattr(getattr(self, "_flow", None), "operation", None)
        if operation and operation["stage"] not in ("failed", "closed", "cancelled"):
            raise RuntimeError("任务进行中，请结束后更新仓库映射表")
        if getattr(self, "_check_lock", None) and self._check_lock.locked():
            raise RuntimeError("正在检查版本，请稍后更新仓库映射表")

    @_observed_rpc
    async def update_repository_map(self) -> dict:
        task = getattr(self, "_repository_map_update", None)
        if task is None:
            self._require_repository_map_idle()
            task = asyncio.create_task(self._update_repository_map())
            self._repository_map_update = task
            def finished(done):
                if self._repository_map_update is done:
                    self._repository_map_update = None
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _update_repository_map(self) -> dict:
        service = self._repository_map_service()
        transport = self._transport()
        payload = await asyncio.to_thread(transport.fetch_bytes, MAP_URL, accept="application/json", max_bytes=MAX_MAP_BYTES)
        async with self._ownership_lock():
            self._require_repository_map_idle()
            if not hasattr(self, "_check_lock"):
                self._check_lock = asyncio.Lock()
            async with self._check_lock:
                changed = await asyncio.to_thread(service.install, payload)
                return {**service.status(), "changed": changed}


    @_observed_rpc
    async def get_app_settings(self) -> dict:
        settings = await asyncio.to_thread(load_settings, _settings_path())
        settings["lastNetworkRoute"] = getattr(self, "_last_route", None)
        return settings

    @_observed_rpc
    async def set_network_mode(self, mode: str) -> dict:
        if not isinstance(mode, str) or mode not in MODES:
            raise RuntimeError("GitHub 连接模式无效")
        settings = load_settings(_settings_path())
        settings["networkMode"] = mode
        save_settings(_settings_path(), settings)
        return await self.get_app_settings()

    @_observed_rpc
    async def set_check_notifications(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise RuntimeError("通知开关必须为布尔值")
        settings = load_settings(_settings_path())
        settings["checkNotifications"] = enabled
        save_settings(_settings_path(), settings)
        return await self.get_app_settings()

    @_observed_rpc
    async def set_show_home_task_notice(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise RuntimeError("首页任务提示开关必须为布尔值")
        settings = load_settings(_settings_path())
        settings["showHomeTaskNotice"] = enabled
        save_settings(_settings_path(), settings)
        return await self.get_app_settings()

    @_observed_rpc
    async def set_delete_package_after_install(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise RuntimeError("安装包删除开关必须为布尔值")
        settings = load_settings(_settings_path())
        settings["deletePackageAfterInstall"] = enabled
        save_settings(_settings_path(), settings)
        return await self.get_app_settings()

    @_observed_rpc
    async def set_download_timeout(self, minutes: int) -> dict:
        if type(minutes) is not int or minutes not in (3, 5, 10, 15, 30):
            raise RuntimeError("下载超时应为 3、5、10、15 或 30 分钟")
        settings = load_settings(_settings_path())
        settings["downloadTimeoutMinutes"] = minutes
        save_settings(_settings_path(), settings)
        return await self.get_app_settings()

    @_observed_rpc
    async def set_auto_check(self, enabled: bool, minutes: int = 360) -> dict:
        if type(minutes) is not int or minutes not in (60, 360, 720, 1440):
            raise RuntimeError("检查间隔应为 1、6、12 或 24 小时")
        settings = load_settings(_settings_path())
        settings["autoCheck"] = bool(enabled)
        settings["autoCheckMinutes"] = minutes
        save_settings(_settings_path(), settings)
        return settings
