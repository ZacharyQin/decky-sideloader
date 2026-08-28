import asyncio
import json
import os
import urllib.error
import urllib.request

import decky

from github_meta import is_newer, parse_github_input, parse_releases
from persist import apply_bindings, load_settings, save_settings
from scan import classify_plugins, read_plugins_dir

STORE_URL = "https://plugins.deckbrew.xyz/plugins"
GITHUB_API = "https://api.github.com/repos/{repo}/releases"
USER_AGENT = "github-sideload/0.0.1"


def _settings_path() -> str:
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code} {url}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        raise RuntimeError(str(error)) from error


def _fetch_store_names(url: str) -> tuple[set[str], str | None]:
    try:
        payload = _fetch_json(url)
    except RuntimeError as error:
        return set(), str(error)
    if not isinstance(payload, list):
        return set(), "store payload is not a list"
    names = {
        item["name"]
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    return names, None


def _releases_for(repo_raw: str, current_version: str | None) -> list[dict]:
    repo = parse_github_input(repo_raw)
    if not repo:
        raise RuntimeError("GitHub 链接无效")
    payload = _fetch_json(GITHUB_API.format(repo=repo))
    releases = parse_releases(payload, repo.split("/", 1)[1])
    for release in releases:
        release["newer"] = is_newer(release["tag"], current_version) if current_version else None
        release["has_zip"] = len(release["zips"]) > 0
    return releases


class Plugin:
    async def ping(self) -> str:
        return "pong"

    async def get_app_settings(self) -> dict:
        return await asyncio.to_thread(load_settings, _settings_path())

    async def set_auto_check(self, enabled: bool) -> dict:
        settings = load_settings(_settings_path())
        settings["autoCheck"] = bool(enabled)
        save_settings(_settings_path(), settings)
        return settings

    async def list_sideload_plugins(self) -> dict:
        plugins_dir = os.path.join(decky.DECKY_HOME, "plugins")
        local = await asyncio.to_thread(read_plugins_dir, plugins_dir)
        store_names, store_error = await asyncio.to_thread(_fetch_store_names, STORE_URL)
        store_ok = store_error is None
        if not store_ok:
            decky.logger.warning("store list failed: %s", store_error)
        settings = load_settings(_settings_path())
        plugins = classify_plugins(
            local,
            store_names,
            decky.DECKY_PLUGIN_NAME,
            store_ok=store_ok,
        )
        plugins = apply_bindings(plugins, settings.get("bindings", {}))
        return {
            "plugins": plugins,
            "store_ok": store_ok,
            "store_error": store_error,
        }

    async def set_binding(self, name: str, repo: str) -> dict:
        parsed = parse_github_input(repo)
        if not parsed:
            raise RuntimeError("GitHub 链接无效")
        settings = load_settings(_settings_path())
        bindings = settings.setdefault("bindings", {})
        bindings[name] = {"repo": parsed, "overridden": True}
        save_settings(_settings_path(), settings)
        return {"name": name, "repo": parsed}

    async def clear_binding(self, name: str) -> None:
        settings = load_settings(_settings_path())
        bindings = settings.get("bindings", {})
        if name in bindings:
            del bindings[name]
            save_settings(_settings_path(), settings)

    async def list_releases(self, repo: str, current_version: str | None = None) -> list[dict]:
        return await asyncio.to_thread(_releases_for, repo, current_version)

    async def check_updates(self) -> list[dict]:
        listing = await self.list_sideload_plugins()
        found: list[dict] = []
        for plugin in listing["plugins"]:
            if not plugin.get("sideload") or not plugin.get("repo"):
                continue
            try:
                releases = await asyncio.to_thread(
                    _releases_for,
                    plugin["repo"],
                    plugin.get("version"),
                )
            except Exception as error:
                decky.logger.warning("check %s failed: %s", plugin["name"], error)
                continue
            latest = next((item for item in releases if item["zips"] and item.get("newer") is True), None)
            if not latest:
                continue
            found.append(
                {
                    "name": plugin["name"],
                    "repo": plugin["repo"],
                    "current": plugin.get("version"),
                    "latest": latest["tag"],
                    "zip_url": latest["zips"][0]["url"],
                    "zip_name": latest["zips"][0]["name"],
                }
            )
        return found

    async def _poll_loop(self) -> None:
        try:
            while True:
                settings = load_settings(_settings_path())
                await asyncio.sleep(max(60, int(settings.get("autoCheckMinutes", 360)) * 60))
                settings = load_settings(_settings_path())
                if not settings.get("autoCheck"):
                    continue
                updates = await self.check_updates()
                if updates:
                    await decky.emit("updates_found", updates)
        except asyncio.CancelledError:
            raise

    async def _main(self):
        decky.logger.info("GitHub Sideload starting")
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _unload(self):
        decky.logger.info("GitHub Sideload unloading")
        task = getattr(self, "_poll_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _uninstall(self):
        decky.logger.info("GitHub Sideload uninstall cleanup")
