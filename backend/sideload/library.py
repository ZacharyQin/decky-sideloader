import asyncio
import os
from pathlib import Path
import decky
from sideload import http_client
from sideload.catalog import Catalog, REGISTRY_URL, registration_for
from sideload.github_meta import parse_github_input
from sideload.native_management import NativeManagement
from sideload.ownership import Ownership, read_frozen
from sideload.paths import _settings_path
from sideload.persist import apply_bindings, load_settings, save_settings, skipped_tags
from sideload.rpc import _observed_rpc
from sideload.diagnostic_context import DiagnosticError, identifier as diagnostic_id
from sideload.diagnostic_snapshot import library_snapshot
from sideload.scan import classify_plugins, read_plugins_dir


class PluginLibrary:
    def _catalog_service(self):
        if not hasattr(self, "_catalog"):
            self._catalog = Catalog(Path(_settings_path()).with_name("catalog.json"),
                                    lambda: http_client._fetch_store_names(http_client.STORE_URL), lambda: "")
        return self._catalog

    def _native_service(self):
        if not hasattr(self, "_native"):
            self._native = NativeManagement(Path(decky.DECKY_PLUGIN_RUNTIME_DIR) / "cache" / "native-updates.json", getattr(decky, "emit", None))
        return self._native

    async def _read_native_management(self, refresh=False):
        return await self._native_service().read(refresh)

    async def receive_native_management(self, identifier: str, payload: dict) -> None:
        self._native_service().receive(identifier, payload)

    @_observed_rpc
    async def list_sideload_plugins(self, refresh: bool = False) -> dict:
        if type(refresh) is not bool:
            raise RuntimeError("刷新参数无效")
        if refresh:
            async with self._ownership_lock():
                self._require_binding_idle(decky.DECKY_PLUGIN_NAME)
                operation = getattr(getattr(self, "_flow", None), "operation", None)
                if operation and operation["stage"] not in ("failed", "closed", "cancelled"):
                    raise RuntimeError("任务进行中，请结束后刷新")
                return await self._list_plugins(True)
        return await self._list_plugins(False)

    async def _list_plugins(self, refresh: bool) -> dict:
        native, capable = await self._read_native_management(refresh)
        native_names = {p["name"] for p in native["plugins"]} if native["ok"] else None
        capable_names = set(capable) if capable is not None else None
        plugins_dir = os.path.join(decky.DECKY_HOME, "plugins")
        local = await asyncio.to_thread(read_plugins_dir, plugins_dir)
        if not hasattr(self, "_catalog_lock"):
            self._catalog_lock = asyncio.Lock()
        async with self._catalog_lock:
            if refresh and getattr(self, "_catalog_closing", False):
                raise RuntimeError("插件正在停止，请重新打开后重试")
            service = self._catalog_service()
            if refresh:
                transport = self._transport()
                service.fetch_registry = lambda: transport.fetch_bytes(REGISTRY_URL, max_bytes=1024 * 1024).decode("utf-8")
                observed = [p for p in local if p["name"] != decky.DECKY_PLUGIN_NAME]
                self._catalog_worker = asyncio.create_task(asyncio.to_thread(service.resolve, refresh=True, plugins=observed))
                try:
                    store_names, catalog, entries = await asyncio.shield(self._catalog_worker)
                except asyncio.CancelledError:
                    await self._catalog_worker
                    raise
                finally:
                    self._catalog_worker = None
                if catalog["warning"]:
                    raise RuntimeError("刷新失败，请稍后重试")
            else:
                store_names, catalog, entries = await asyncio.to_thread(service.resolve)
            memories = {}
            for plugin in local:
                memory = service.memory_for(plugin)
                memories[plugin["folder"]] = memory if isinstance(memory, dict) else {}
            history = getattr(service, "store_history", set())
            history = history if isinstance(history, set) else store_names if catalog["source"] in ("live", "cache") else set()
            memory_error = getattr(service, "memory_error", None)
            memory_error = memory_error if isinstance(memory_error, str) else None
        store_ok = catalog["source"] == "live"
        store_error = catalog["warning"]
        settings = load_settings(_settings_path())
        plugins = apply_bindings(classify_plugins(local, store_names, decky.DECKY_PLUGIN_NAME, store_ok=store_ok), settings.get("bindings", {}))
        for plugin in plugins:
            plugin["loader_managed"] = None if native_names is None else False if plugin["name"] not in native_names else None if capable_names is None else plugin["name"] in capable_names
            memory = memories[plugin["folder"]]
            plugin["registration"] = registration_for(plugin, entries)
            plugin["cached_in_store"] = plugin["name"] in history
            plugin["memory_in_store"] = memory.get("in_store")
            plugin["memory_changed"] = bool(plugin.get("binding_changed") or memory.get("changed", False) and not plugin.get("binding_confirmed"))
            plugin["memory_error"] = memory_error
            if plugin["repo_source"] is None and memory.get("repo"):
                plugin["repo"], plugin["repo_source"] = memory["repo"], "remembered"
            plugin["skipped_tags"] = skipped_tags(settings, plugin["name"], plugin.get("repo"))
        ownership = self._ownership()
        try:
            ledger, ownership_error = ownership.load(), None
        except RuntimeError as error:
            ledger, ownership_error = {"version": 1, "entries": []}, str(error)
        try:
            frozen = read_frozen(decky.DECKY_HOME)
        except RuntimeError:
            frozen = None
        plugins = ownership.resolve(plugins, frozen, ledger, ownership_error)
        self._store_names, self._store_ok = {p["name"] for p in plugins if p["loader_managed"] is True}, native["ok"]
        self._diagnostic_library = library_snapshot(plugins, native, catalog, frozen)
        self._diagnostic("list_summary", source=catalog["source"], count=len(plugins), ok=native["ok"] and not bool(catalog["warning"]), loaderOk=native["ok"], catalogOk=not bool(catalog["warning"]))
        return {
            "native_snapshot": native,
            "catalog": catalog,
            "plugins": plugins,
            "store_ok": store_ok,
            "store_error": store_error,
            "last_check": settings.get("lastCheck"),
            "release_snapshots": [snapshot for plugin in plugins if (snapshot := self._release_cache().get(plugin)) is not None],
        }

    def _ownership(self):
        return Ownership(Path(_settings_path()).with_name("ownership.json"))

    def _ownership_lock(self):
        if not hasattr(self, "_owner_lock"):
            self._owner_lock = asyncio.Lock()
        return self._owner_lock

    async def _eligible_plugin(self, name: str) -> dict:
        self._require_plugin(name)
        listing = await self.list_sideload_plugins()
        matches = [p for p in listing["plugins"] if p["name"] == name]
        if len(matches) != 1 or not matches[0]["ownership"]["eligible"]:
            raise DiagnosticError("ownership_blocked", "此插件由商店负责或归属未确认，请先显式接管并完成冻结回查")
        return matches[0]

    async def _binding_plugin(self, name: str) -> dict:
        self._require_plugin(name)
        listing = await self.list_sideload_plugins()
        plugin = next(p for p in listing["plugins"] if p["name"] == name)
        owner = plugin["ownership"]
        if not owner["eligible"] and not (owner["state"] == "unknown" and not owner["error"] and not owner["intent"] and plugin.get("cached_in_store") is not True):
            raise RuntimeError("此插件由商店负责或接管未确认，请先使用显式接管")
        return plugin

    @_observed_rpc
    async def authorize_plugin_mutation(self, name: str) -> None:
        async with self._ownership_lock():
            self._require_binding_idle(name)
            await self._eligible_plugin(name)

    @_observed_rpc
    async def begin_store_ownership(self, name: str, folder: str, repo: str, action: str) -> dict:
        async with self._ownership_lock():
            self._require_binding_idle(name)
            disk = self._require_plugin(name)
            if disk["folder"] != folder:
                raise DiagnosticError("identity_changed", "插件目录已变化")
            listing = await self.list_sideload_plugins()
            plugin = next(p for p in listing["plugins"] if p["name"] == name)
            self._require_binding_idle(name)
            intent = self._ownership().begin(plugin, repo, action, read_frozen(decky.DECKY_HOME))
            async with self._catalog_lock:
                self._require_binding_idle(name)
                service = self._catalog_service()
                if isinstance(service, Catalog):
                    service.remember(disk, intent["repo"], "user")
            self._diagnostic("ownership_stage", pluginId=diagnostic_id("plugin", name), ownershipId=intent["id"], action=action, phase=intent["state"])
            return intent

    @_observed_rpc
    async def ownership_native_step(self, identifier: str, phase: str) -> dict:
        async with self._ownership_lock():
            ledger = self._ownership().load()
            intent = next((r for r in ledger["entries"] if r["id"] == identifier), None)
            if not intent:
                raise DiagnosticError("identity_changed", "接管意图已变化")
            self._require_binding_idle(intent["name"])
            disk = self._require_plugin(intent["name"])
            if disk["folder"] != intent["folder"]:
                raise DiagnosticError("identity_changed", "插件目录已变化，不能继续原接管意图")
            frozen = read_frozen(decky.DECKY_HOME)
            if phase == "read":
                return {"intent": intent, "frozen": frozen}
            updated = self._ownership().transition(identifier, phase, frozen)
            self._diagnostic("ownership_stage", pluginId=diagnostic_id("plugin", intent["name"]), ownershipId=identifier, action=updated["action"], phase=updated["state"])
            return {"intent": updated, "frozen": frozen}

    def _require_owned_repo(self, intent, repo):
        parsed = parse_github_input(repo) if isinstance(repo, str) else None
        if not parsed or parsed.lower() != intent["repo"].lower():
            raise RuntimeError("安装仓库与接管时确认的仓库不一致；请先交还后重新接管以更换仓库")

    def _require_plugin(self, name: str) -> dict:
        local = read_plugins_dir(os.path.join(decky.DECKY_HOME, "plugins"))
        matches = [p for p in local if p["name"] == name]
        if len(matches) != 1 or name == decky.DECKY_PLUGIN_NAME:
            raise RuntimeError("插件不存在、名称重复或是 decky-sideloader 自身")
        return matches[0]

    def _require_binding_idle(self, name: str) -> None:
        if getattr(self, "_catalog_closing", False):
            raise RuntimeError("插件正在停止，请重新打开后重试")
        if not hasattr(self, "_flow") and getattr(decky, "DECKY_PLUGIN_RUNTIME_DIR", None):
            if (Path(decky.DECKY_PLUGIN_RUNTIME_DIR) / "installations" / "operation.json").is_file():
                self._install_flow()
        operation = getattr(getattr(self, "_flow", None), "operation", None)
        if operation and operation["stage"] not in ("failed", "closed", "cancelled") and name in (operation.get("existing_name"), (operation.get("package") or {}).get("name"), (operation.get("target") or {}).get("name")):
            raise RuntimeError("此插件有正在处理的安装任务，请结束任务后修改关联")

    @_observed_rpc
    async def set_binding(self, name: str, repo: str) -> dict:
        async with self._ownership_lock():
            return await self._set_binding(name, repo)

    async def _set_binding(self, name, repo):
        self._require_binding_idle(name)
        plugin = await self._binding_plugin(name)
        if plugin["ownership"]["state"] == "owned":
            raise RuntimeError("已接管商店插件的仓库已由接管记录确认；请先交还后重新接管以更换仓库")
        parsed = parse_github_input(repo)
        if not parsed:
            raise RuntimeError("GitHub 链接无效")
        settings = load_settings(_settings_path())
        bindings = settings.setdefault("bindings", {})
        raw = self._require_plugin(name)
        self._require_binding_idle(name)
        bindings[name] = {"repo": parsed, "overridden": True, "folder": raw["folder"],
                          "package_name": raw.get("package_name"), "package_repo": raw.get("repo")}
        save_settings(_settings_path(), settings)
        return {"name": name, "repo": parsed}

    @_observed_rpc
    async def clear_binding(self, name: str) -> None:
        async with self._ownership_lock():
            await self._clear_binding(name)

    async def _clear_binding(self, name):
        self._require_binding_idle(name)
        plugin = await self._binding_plugin(name)
        if plugin["ownership"]["state"] == "owned":
            raise RuntimeError("请使用交还商店，避免解除关联后商店仍暂停检查")
        settings = load_settings(_settings_path())
        self._require_binding_idle(name)
        settings["bindings"][name] = {"disabled": True}
        save_settings(_settings_path(), settings)
