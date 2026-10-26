import asyncio
from sideload import http_client
from sideload.github_transport import MODES
from sideload.paths import _settings_path
from sideload.persist import load_settings, save_settings
from sideload.rpc import _observed_rpc


class SettingsAPI:
    def _transport(self):
        loop = asyncio.get_running_loop()
        def observe(snapshot):
            loop.call_soon_threadsafe(setattr, self, "_last_route", snapshot)
        return http_client._ObservedTransport(http_client._ssl_context(), load_settings(_settings_path())["networkMode"], observe, diagnostic=self._diagnostic)

    def _query_fetch(self):
        transport = self._transport()
        return lambda url, **kwargs: http_client._fetch_json(url, transport=transport, **kwargs)

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
