import asyncio
import os
from pathlib import Path
import decky
from sideload.github_meta import parse_github_input
from sideload.http_client import _ssl_context
from sideload.install_flow import InstallFlow
from sideload.ownership import read_frozen
from sideload.paths import _settings_path
from sideload.persist import load_settings, save_settings
from sideload.releases_api import _releases_for
from sideload.rpc import _observed_rpc
from sideload.scan import read_plugins_dir


class InstallationAPI:
    @_observed_rpc
    async def delete_backups(self) -> dict:
        return await self._install_flow().clean_backups(expired_only=False)

    async def _backup_loop(self):
        while True:
            try:
                result = await self._install_flow().clean_backups()
                if result["failed"]:
                    decky.logger.warning("Could not remove %d expired backups", result["failed"])
            except asyncio.CancelledError:
                raise
            except Exception:
                decky.logger.exception("automatic backup cleanup failed")
            await asyncio.sleep(3600)

    @_observed_rpc
    async def delete_install_package(self, identifier: str) -> dict:
        if not isinstance(identifier, str) or not identifier:
            raise RuntimeError("任务标识无效")
        return await self._install_flow().delete_package(identifier)

    @_observed_rpc
    async def continue_prepared(self, identifier: str) -> dict:
        if not isinstance(identifier, str) or not identifier:
            raise RuntimeError("任务标识无效")
        return await self._install_flow().continue_prepare(identifier)

    @_observed_rpc
    async def retry_download(self, identifier: str) -> dict:
        if not isinstance(identifier, str) or not identifier:
            raise RuntimeError("任务标识无效")
        return await self._install_flow().retry_download(identifier)

    async def _gate_operation(self, identifier: str, absent=False):
        flow = self._install_flow()
        op = flow.operation
        if not op or op["id"] != identifier:
            raise RuntimeError("安装任务已变化")
        name = op.get("existing_name") or (op.get("package") or op.get("target") or {}).get("name")
        if not name:
            raise RuntimeError("尚未确认安装目标")
        local = read_plugins_dir(os.path.join(decky.DECKY_HOME, "plugins"))
        matches = [p for p in local if p["name"] == name]
        if matches:
            plugin = await self._eligible_plugin(name)
            if plugin["ownership"]["state"] == "owned":
                self._require_owned_repo(plugin["ownership"]["intent"], op.get("repo"))
        elif op.get("existing_name"):
            # Explicit native uninstall legitimately removes the directory. The
            # flow still checks absence, ZIP and backup before submitting.
            if not absent or op["stage"] != "uninstalling" or op.get("mode") != "reinstall":
                raise RuntimeError("原插件已不存在")
            ledger = self._ownership().load()
            entry = self._ownership().entry({"name": name, "folder": op["package"]["folder"]}, ledger)
            if entry and (entry["state"] != "active" or name not in read_frozen(decky.DECKY_HOME)):
                raise RuntimeError("接管状态未确认，已阻止继续安装")
            if entry:
                self._require_owned_repo(entry, op.get("repo"))
            if not entry:
                listing = await self.list_sideload_plugins()
                # No new consent can be inferred from an old ready task.
                if name in self._store_names or not listing["native_snapshot"]["ok"]:
                    raise RuntimeError("原插件的商店归属不能确认，已阻止继续安装")
        return flow

    def _install_flow(self) -> InstallFlow:
        if not hasattr(self, "_flow"):
            self._flow = InstallFlow(decky.DECKY_HOME, decky.DECKY_PLUGIN_RUNTIME_DIR,
                                     decky.DECKY_PLUGIN_NAME, Path(decky.DECKY_PLUGIN_DIR).name,
                                     _releases_for, _ssl_context, self._transport, observer=self._diagnostic, timeout_seconds=lambda: load_settings(_settings_path())["downloadTimeoutMinutes"] * 60)
        return self._flow

    @_observed_rpc
    async def prepare_install(self, repo: str, tag: str, asset_url: str, existing_name: str | None = None, plugin_path: str | None = None, mode: str = "install") -> dict:
        async with self._ownership_lock():
            return await self._prepare_install(repo, tag, asset_url, existing_name, plugin_path, mode)

    async def _prepare_install(self, repo, tag, asset_url, existing_name, plugin_path, mode):
        parsed = parse_github_input(repo)
        if not parsed or not isinstance(tag, str) or not isinstance(asset_url, str):
            raise RuntimeError("仓库、版本或安装包无效")
        target = None
        if existing_name:
            plugin = await self._eligible_plugin(existing_name)
            if plugin["ownership"]["state"] == "owned":
                self._require_owned_repo(plugin["ownership"]["intent"], parsed)
        else:
            proof = await self.probe_repository(parsed)
            if not proof["plugins"]:
                raise RuntimeError("仓库中未确认 Decky 插件，已阻止新装")
            if plugin_path is None and len(proof["plugins"]) != 1:
                raise RuntimeError("仓库包含多个插件，请先选择要安装的插件目录")
            candidate = next((p for p in proof["plugins"] if p["path"] == plugin_path), None) if plugin_path is not None else proof["plugins"][0]
            if not candidate:
                raise RuntimeError("所选插件目录不在探查结果中，请重新探查")
            matches = [p for p in read_plugins_dir(os.path.join(decky.DECKY_HOME, "plugins")) if p["name"] == candidate["name"]]
            if matches:
                await self._eligible_plugin(candidate["name"])
                raise RuntimeError("同名插件已安装，请从插件详情进入管理")
            target = {**candidate, "tree_sha": proof["tree_sha"]}
        flow = self._install_flow()
        flow.releases_for = self._release_reader()
        return await flow.prepare(parsed, tag, asset_url, existing_name, target, mode=mode)

    @_observed_rpc
    async def get_operation(self) -> dict | None:
        flow = self._install_flow()
        def finalize(operation):
            settings = load_settings(_settings_path())
            binding = {"repo": operation["repo"], "overridden": True}
            home = getattr(decky, "DECKY_HOME", "")
            rows = read_plugins_dir(os.path.join(home, "plugins")) if home else []
            matches = [p for p in rows if p["name"] == operation["package"]["name"]]
            if len(matches) == 1 and matches[0]["folder"] == operation["package"].get("folder"):
                raw = matches[0]
                binding.update(folder=raw["folder"], package_name=raw.get("package_name"), package_repo=raw.get("repo"))
            settings["bindings"][operation["package"]["name"]] = binding
            save_settings(_settings_path(), settings)
        result = await flow.status(finalize)
        if result and result.get("outcome") == "success" and not result.get("artifact_deleted") and load_settings(_settings_path()).get("deletePackageAfterInstall"):
            try:
                result = await flow.delete_package(result["id"])
            except Exception:
                return result
        return result

    @_observed_rpc
    async def submit_prepared(self, identifier: str) -> dict:
        async with self._ownership_lock():
            flow = await self._gate_operation(identifier)
            return await flow.submit(identifier)

    @_observed_rpc
    async def claim_uninstall(self, identifier: str) -> dict:
        async with self._ownership_lock():
            flow = await self._gate_operation(identifier)
            return await flow.claim_uninstall(identifier)

    @_observed_rpc
    async def complete_uninstall(self, identifier: str) -> dict:
        flow = self._install_flow()
        if flow.operation and flow.operation["id"] == identifier and flow.operation.get("cancel_requested"):
            return await flow.complete_cancelled_uninstall(identifier)
        async with self._ownership_lock():
            flow = await self._gate_operation(identifier, absent=True)
            return await flow.submit(identifier, after_uninstall=True)

    @_observed_rpc
    async def record_install_event(self, identifier: str, kind: str, name: str, value: str = "", sha256: str = "") -> dict | None:
        return await self._install_flow().event(identifier, kind, name, value, sha256)

    @_observed_rpc
    async def cancel_operation(self, identifier: str) -> dict:
        return await self._install_flow().cancel(identifier)

    @_observed_rpc
    async def dismiss_operation(self, identifier: str) -> dict:
        return await self._install_flow().dismiss(identifier)

    @_observed_rpc
    async def close_operation(self, identifier: str) -> dict:
        return await self._install_flow().close(identifier)
