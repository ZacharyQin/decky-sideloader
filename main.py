# Semantic index:
# - owner: docs/upstream-conflict/sideload/plugin-management.md -- gate mutations by ownership, expose exact-task stopping and notice metadata, and retain evidence-gated finalization.
# - participant: docs/upstream-conflict/sideload/diagnostics-sharing.md -- expose pickup RPCs and revoke service before unrelated unload work.
# - participant: docs/upstream-conflict/sideload/diagnostics.md -- isolate RPC, network and lifecycle observation from business results.
import asyncio
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timezone
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import decky

from github_meta import compare_versions, parse_github_input, parse_releases
from pathlib import Path
from diagnostics import Diagnostics
from diagnostics_sharing import DiagnosticSharing
from install_flow import InstallFlow
from persist import apply_bindings, load_settings, save_settings
from ownership import Ownership, read_frozen
from scan import classify_plugins, read_plugins_dir
from repo_probe import probe_repository
from github_transport import GitHubTransport, MODES, content_length, read_chunk
from catalog import Catalog, REGISTRY_URL, MAX_CATALOG_BYTES, registration_for, validate_names

STORE_URL = "https://plugins.deckbrew.xyz/plugins"
GITHUB_API = "https://api.github.com/repos/{repo}/releases"
USER_AGENT = "github-sideload"
SYSTEM_CA_FILES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
)


def _settings_path() -> str:
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")


def _ssl_context() -> ssl.SSLContext:
    for ca_file in SYSTEM_CA_FILES:
        if not os.path.isfile(ca_file):
            continue
        try:
            return ssl.create_default_context(cafile=ca_file)
        except (OSError, ssl.SSLError):
            continue
    return ssl.create_default_context()


class _StoreRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl != STORE_URL:
            if fp is not None:
                fp.close()
            raise RuntimeError("商店目录重定向来源不受支持")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_json(url: str, *, accept: str = "application/json", max_bytes: int | None = None, transport=None) -> object:
    if transport is not None or url.startswith(("https://api.github.com/", "https://raw.githubusercontent.com/")):
        return (transport or GitHubTransport(_ssl_context())).fetch_json(url, accept=accept, max_bytes=max_bytes)
    if url != STORE_URL:
        raise RuntimeError("查询地址不受支持")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _StoreRedirect(), urllib.request.HTTPSHandler(context=_ssl_context()))
        start = time.monotonic()
        with opener.open(request, timeout=3) as response:
            if response.geturl() != STORE_URL:
                raise RuntimeError("商店目录来源不受支持")
            # The store is separate from GitHub routing and bounded independently.
            maximum = max_bytes or MAX_CATALOG_BYTES
            length = content_length(response.headers, maximum)
            data = bytearray()
            while True:
                if time.monotonic() - start > 5:
                    raise TimeoutError("商店目录读取超过 5 秒")
                chunk = read_chunk(response, min(65536, maximum + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > maximum:
                    raise RuntimeError("商店响应过大")
            if length is not None and len(data) != length:
                raise RuntimeError("商店目录长度不完整")
            return json.loads(data)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        raise RuntimeError(str(error)) from error


def _fetch_store_names(url: str) -> tuple[set[str], str | None]:
    try:
        return validate_names(_fetch_json(url)), None
    except RuntimeError as error:
        return set(), str(error)


def _search_repositories(query: str, fetch=None) -> list[dict]:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 100:
        raise RuntimeError("请输入 1 至 100 个字符的插件名或关键词")
    query = query.strip()
    if not any(word.lower().startswith("fork:") for word in query.split()):
        query += " fork:true"
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": query, "per_page": 20})
    payload = (fetch or _fetch_json)(url, accept="application/vnd.github+json")
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise RuntimeError("GitHub 搜索响应格式异常，请重试")
    rows, seen = [], set()
    for item in payload["items"][:20]:
        if not isinstance(item, dict):
            continue
        repo = item.get("full_name")
        if not isinstance(repo, str) or parse_github_input(repo) != repo or repo in seen:
            continue
        seen.add(repo)
        stars = item.get("stargazers_count", 0)
        rows.append({
            "repo": repo,
            "description": item.get("description") if isinstance(item.get("description"), str) else "",
            "stars": max(0, stars) if type(stars) is int else 0,
            "fork": item.get("fork") is True,
            "archived": item.get("archived") is True,
        })
    return rows


def _releases_for(repo_raw: str, current_version: str | None, fetch=None) -> list[dict]:
    repo = parse_github_input(repo_raw)
    if not repo:
        raise RuntimeError("GitHub 链接无效")
    releases = []
    for page in range(1, 11):
        payload = (fetch or _fetch_json)(
            GITHUB_API.format(repo=repo) + f"?per_page=100&page={page}",
            accept="application/vnd.github+json",
        )
        releases.extend(parse_releases(payload, repo.split("/", 1)[1]))
        if len(payload) < 100:
            break
    else:
        raise RuntimeError("仓库发布超过 1000 条，请使用发布较少的插件仓库")
    releases.sort(key=lambda item: item["published_at"], reverse=True)
    for release in releases:
        release["repo"] = repo
        release["comparison"] = compare_versions(release["tag"], current_version)
        release["has_zip"] = len(release["zips"]) > 0
    return releases


def _observed_rpc(method):
    failure_only = method.__name__ == "get_operation"
    @wraps(method)
    async def observed(self, *args, **kwargs):
        started = time.monotonic()
        if not failure_only:
            self._diagnostic("rpc_start", rpc=method.__name__)
        try:
            result = await method(self, *args, **kwargs)
        except BaseException as error:
            self._diagnostic("rpc_failed", rpc=method.__name__, durationMs=max(0, int((time.monotonic() - started) * 1000)),
                             errorType=type(error).__name__ if type(error).__name__ in ("OSError", "RuntimeError", "TimeoutError", "ValueError", "CancelledError") else "Exception")
            raise
        if not failure_only:
            self._diagnostic("rpc_end", rpc=method.__name__, durationMs=max(0, int((time.monotonic() - started) * 1000)))
        return result
    return observed


class _ObservedTransport(GitHubTransport):
    def __init__(self, *args, diagnostic, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostic = diagnostic

    @contextmanager
    def open(self, url, route, **kwargs):
        attempt = 2 if self.mode == "auto" and route == "gh-proxy" else 1
        self.diagnostic("network_attempt", route=route, attempt=attempt, status="connecting")
        try:
            with super().open(url, route, **kwargs) as response:
                self.diagnostic("network_attempt", route=route, attempt=attempt, status="ok", httpStatus=getattr(response, "status", 0))
                yield response
        except Exception as error:
            self.diagnostic("network_attempt", route=route, attempt=attempt, status="failed", httpStatus=error.code if isinstance(error, urllib.error.HTTPError) else 0)
            raise


class Plugin:
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
                 "frontend": getattr(decky, "DECKY_PLUGIN_VERSION", ""), "loader": getattr(decky, "DECKY_VERSION", "")})
            try:
                self._diagnostics.attach_sdk(decky.logger, getattr(decky, "DECKY_PLUGIN_LOG", ""))
            except Exception:
                pass
        return self._diagnostics

    def _sharing_service(self):
        if not hasattr(self, "_sharing"):
            self._sharing = DiagnosticSharing(self._diagnostic_service())
        return self._sharing

    async def get_diagnostics_addresses(self) -> dict:
        return self._sharing_service().available()

    async def get_diagnostics_share(self) -> dict:
        return self._sharing_service().status()

    async def start_diagnostics_share(self, address: str, session: str, previous: str | None = None) -> dict:
        operation = getattr(getattr(self, "_flow", None), "operation", None) or {}
        summary = {"operationId": operation.get("id"), "stage": operation.get("stage"),
                   "mode": load_settings(_settings_path())["networkMode"]}
        return await self._sharing_service().start(address, session, summary, previous)

    async def stop_diagnostics_share(self, session: str) -> dict:
        return await self._sharing_service().stop(session)

    async def record_diagnostic_events(self, events: list, dropped: int = 0) -> dict:
        return self._diagnostic_service().record_batch(events, dropped)

    async def get_diagnostics_status(self) -> dict:
        return self._diagnostic_service().status()

    async def export_diagnostics(self) -> dict:
        # Inspect only the existing in-memory operation. Never construct/recover a flow here.
        operation = getattr(getattr(self, "_flow", None), "operation", None) or {}
        summary = {"operationId": operation.get("id"), "stage": operation.get("stage"),
                   "mode": load_settings(_settings_path())["networkMode"]}
        return await self._diagnostic_service().export(summary)

    @_observed_rpc
    async def ping(self) -> str:
        return "pong"

    def _transport(self):
        loop = asyncio.get_running_loop()
        def observe(snapshot):
            loop.call_soon_threadsafe(setattr, self, "_last_route", snapshot)
        return _ObservedTransport(_ssl_context(), load_settings(_settings_path())["networkMode"], observe, diagnostic=self._diagnostic)

    def _query_fetch(self):
        transport = self._transport()
        return lambda url, **kwargs: _fetch_json(url, transport=transport, **kwargs)

    def _release_reader(self):
        fetch = self._query_fetch()
        return lambda repo, current: _releases_for(repo, current, fetch=fetch)

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
    async def set_auto_check(self, enabled: bool, minutes: int = 360) -> dict:
        if type(minutes) is not int or minutes not in (60, 360, 720, 1440):
            raise RuntimeError("检查间隔应为 1、6、12 或 24 小时")
        settings = load_settings(_settings_path())
        settings["autoCheck"] = bool(enabled)
        settings["autoCheckMinutes"] = minutes
        save_settings(_settings_path(), settings)
        return settings

    def _catalog_service(self):
        if not hasattr(self, "_catalog"):
            self._catalog = Catalog(Path(_settings_path()).with_name("catalog.json"),
                                    lambda: _fetch_store_names(STORE_URL), lambda: "")
        return self._catalog

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
                service.fetch_registry = lambda: transport.fetch_bytes(REGISTRY_URL, max_bytes=1024 * 1024, timeout=3, deadline=8).decode("utf-8")
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
            memory = memories[plugin["folder"]]
            plugin["registration"] = registration_for(plugin, entries)
            plugin["cached_in_store"] = plugin["name"] in history
            plugin["memory_in_store"] = memory.get("in_store")
            plugin["memory_changed"] = bool(plugin.get("binding_changed") or memory.get("changed", False) and not plugin.get("binding_confirmed"))
            plugin["memory_error"] = memory_error
            if plugin["repo_source"] is None and memory.get("repo"):
                plugin["repo"], plugin["repo_source"] = memory["repo"], "remembered"
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
        self._store_names, self._store_ok = store_names, store_ok
        self._diagnostic("list_summary", source=catalog["source"], count=len(plugins), ok=store_ok)
        return {
            "catalog": catalog,
            "plugins": plugins,
            "store_ok": store_ok,
            "store_error": store_error,
            "last_check": settings.get("lastCheck"),
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
            raise RuntimeError("此插件由商店负责或归属未确认，请先显式接管并完成冻结回查")
        return matches[0]

    async def _binding_plugin(self, name: str) -> dict:
        self._require_plugin(name)
        listing = await self.list_sideload_plugins()
        plugin = next(p for p in listing["plugins"] if p["name"] == name)
        owner = plugin["ownership"]
        if not owner["eligible"] and not (owner["state"] == "unknown" and not owner["error"] and not owner["intent"] and plugin.get("cached_in_store") is not True):
            raise RuntimeError("此插件由商店负责或接管未确认，请先使用显式接管")
        return plugin

    async def authorize_plugin_mutation(self, name: str) -> None:
        async with self._ownership_lock():
            self._require_binding_idle(name)
            await self._eligible_plugin(name)

    async def begin_store_ownership(self, name: str, folder: str, repo: str, action: str) -> dict:
        async with self._ownership_lock():
            self._require_binding_idle(name)
            disk = self._require_plugin(name)
            if disk["folder"] != folder:
                raise RuntimeError("插件目录已变化")
            listing = await self.list_sideload_plugins()
            plugin = next(p for p in listing["plugins"] if p["name"] == name)
            self._require_binding_idle(name)
            intent = self._ownership().begin(plugin, repo, action, read_frozen(decky.DECKY_HOME))
            async with self._catalog_lock:
                self._require_binding_idle(name)
                service = self._catalog_service()
                if isinstance(service, Catalog):
                    service.remember(disk, intent["repo"], "user")
            return intent

    async def ownership_native_step(self, identifier: str, phase: str) -> dict:
        async with self._ownership_lock():
            ledger = self._ownership().load()
            intent = next((r for r in ledger["entries"] if r["id"] == identifier), None)
            if not intent:
                raise RuntimeError("接管意图已变化")
            self._require_binding_idle(intent["name"])
            disk = self._require_plugin(intent["name"])
            if disk["folder"] != intent["folder"]:
                raise RuntimeError("插件目录已变化，不能继续原接管意图")
            frozen = read_frozen(decky.DECKY_HOME)
            if phase == "read":
                return {"intent": intent, "frozen": frozen}
            updated = self._ownership().transition(identifier, phase, frozen)
            return {"intent": updated, "frozen": frozen}

    def _require_owned_repo(self, intent, repo):
        parsed = parse_github_input(repo) if isinstance(repo, str) else None
        if not parsed or parsed.lower() != intent["repo"].lower():
            raise RuntimeError("安装仓库与接管时确认的仓库不一致；请先交还后重新接管以更换仓库")

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
                binding = load_settings(_settings_path())["bindings"].get(name)
                repair = isinstance(binding, dict) and isinstance(binding.get("repo"), str) and parse_github_input(binding["repo"]) and not binding.get("disabled")
                if name in self._store_names or not listing["store_ok"] and not repair:
                    raise RuntimeError("原插件的商店归属不能确认，已阻止继续安装")
        return flow

    def _require_plugin(self, name: str) -> dict:
        local = read_plugins_dir(os.path.join(decky.DECKY_HOME, "plugins"))
        matches = [p for p in local if p["name"] == name]
        if len(matches) != 1 or name == decky.DECKY_PLUGIN_NAME:
            raise RuntimeError("插件不存在、名称重复或是 GitHub Sideload 自身")
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

    @_observed_rpc
    async def search_repositories(self, query: str) -> list[dict]:
        return await asyncio.to_thread(_search_repositories, query, self._query_fetch())

    @_observed_rpc
    async def probe_repository(self, repo: str) -> dict:
        parsed = parse_github_input(repo) if isinstance(repo, str) else None
        if not parsed:
            raise RuntimeError("GitHub 链接无效")
        if not hasattr(self, "_probe_lock"):
            self._probe_lock, self._probe_cache = asyncio.Lock(), {}
        async with self._probe_lock:
            now = time.monotonic()
            self._probe_cache = {key: item for key, item in self._probe_cache.items() if now - item[0] < 300}
            if parsed.lower() not in self._probe_cache:
                result = await asyncio.to_thread(probe_repository, parsed, self._query_fetch())
                if len(self._probe_cache) >= 10:
                    self._probe_cache.pop(next(iter(self._probe_cache)))
                self._probe_cache[parsed.lower()] = (time.monotonic(), result)
            return self._probe_cache[parsed.lower()][1]

    @_observed_rpc
    async def list_releases(self, repo: str, current_version: str | None = None) -> list[dict]:
        return await asyncio.to_thread(self._release_reader(), repo, current_version)

    @_observed_rpc
    async def check_updates(self) -> dict:
        return await self._check_updates("manual")

    async def _check_updates(self, source: str) -> dict:
        if not hasattr(self, "_check_lock"):
            self._check_lock = asyncio.Lock()
        async with self._check_lock:
            listing = await self.list_sideload_plugins()
            results = []
            for plugin in listing["plugins"]:
                if not plugin.get("sideload"):
                    continue
                result = {"name": plugin["name"], "repo": plugin.get("repo"), "current": plugin.get("version"), "latest": None, "status": "unbound", "error": None,
                          "folder": plugin["folder"], "ownership_id": (plugin["ownership"].get("intent") or {}).get("id")}
                if plugin.get("repo"):
                    try:
                        releases = await asyncio.to_thread(self._release_reader(), plugin["repo"], plugin.get("version"))
                        latest = releases[0] if releases else None
                        if latest is None:
                            result["status"] = "no_release"
                        else:
                            result["latest"] = latest["tag"]
                            result["status"] = "no_asset" if not latest["has_zip"] else {1: "update", 0: "current", -1: "ahead", None: "uncomparable"}[latest["comparison"]]
                    except Exception as error:
                        result["status"], result["error"] = "error", str(error)
                        decky.logger.warning("check %s failed: %s", plugin["name"], error)
                results.append(result)
            report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "source": source, "items": results}
            settings = load_settings(_settings_path())
            settings["lastCheck"] = report
            if source == "scheduled":
                settings["lastScheduledCheck"] = report
            save_settings(_settings_path(), settings)
            self._diagnostic("check_summary", source=source, count=len(results), errors=sum(item["status"] == "error" for item in results), updates=sum(item["status"] == "update" for item in results))
            # A return/binding change during GitHub I/O cannot restore an old
            # eligibility snapshot. The report remains an immutable history.
            current = await self.list_sideload_plugins()
            current["last_check"] = report
            return current

    async def _scheduled_check(self) -> None:
        try:
            listing = await self._check_updates("scheduled")
            report = listing["last_check"]
        except Exception as error:
            report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "source": "scheduled", "items": [], "error": str(error) or type(error).__name__}
            settings = load_settings(_settings_path())
            settings["lastCheck"] = settings["lastScheduledCheck"] = report
            save_settings(_settings_path(), settings)
            self._diagnostic("check_summary", source="scheduled", count=0, errors=1)
        # Delivery failure must not rewrite a completed check as a failed check.
        settings = load_settings(_settings_path())
        try:
            await decky.emit("scheduled_check_completed", {"report": report, "checkNotifications": settings["checkNotifications"]})
        except Exception:
            decky.logger.exception("scheduled check event delivery failed")

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                settings = load_settings(_settings_path())
                if not settings["autoCheck"]:
                    continue
                last = (settings.get("lastCheck") or {}).get("checkedAt")
                if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < settings["autoCheckMinutes"] * 60:
                    continue
                await self._scheduled_check()
            except asyncio.CancelledError:
                raise
            except Exception:
                decky.logger.exception("automatic update check failed")

    def _install_flow(self) -> InstallFlow:
        if not hasattr(self, "_flow"):
            self._flow = InstallFlow(decky.DECKY_HOME, decky.DECKY_PLUGIN_RUNTIME_DIR,
                                     decky.DECKY_PLUGIN_NAME, Path(decky.DECKY_PLUGIN_DIR).name,
                                     _releases_for, _ssl_context, self._transport, observer=self._diagnostic)
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
        return await flow.status(finalize)

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

    async def _main(self):
        self._diagnostic_service()
        self._diagnostic("startup")
        decky.logger.info("GitHub Sideload starting")
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _unload(self):
        self._catalog_closing = True
        if hasattr(self, "_sharing"):
            await self._sharing.stop(closing=True)
        worker = getattr(self, "_catalog_worker", None)
        if worker is not None:
            await asyncio.shield(worker)
        self._diagnostic("shutdown")
        decky.logger.info("GitHub Sideload unloading")
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
        decky.logger.info("GitHub Sideload uninstall cleanup")
