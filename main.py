import asyncio
import json
import os
import urllib.error
import urllib.request

import decky

from scan import classify_plugins, read_plugins_dir

STORE_URL = "https://plugins.deckbrew.xyz/plugins"


def _fetch_store_names(url: str) -> tuple[set[str], str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "github-sideload/0.0.1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        return set(), str(error)
    if not isinstance(payload, list):
        return set(), "store payload is not a list"
    names = {
        item["name"]
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    return names, None


class Plugin:
    async def ping(self) -> str:
        return "pong"

    async def list_sideload_plugins(self) -> dict:
        plugins_dir = os.path.join(decky.DECKY_HOME, "plugins")
        local = await asyncio.to_thread(read_plugins_dir, plugins_dir)
        store_names, store_error = await asyncio.to_thread(_fetch_store_names, STORE_URL)
        store_ok = store_error is None
        if not store_ok:
            decky.logger.warning("store list failed: %s", store_error)
        plugins = classify_plugins(
            local,
            store_names,
            decky.DECKY_PLUGIN_NAME,
            store_ok=store_ok,
        )
        return {
            "plugins": plugins,
            "store_ok": store_ok,
            "store_error": store_error,
        }

    async def _main(self):
        decky.logger.info("GitHub Sideload starting")

    async def _unload(self):
        decky.logger.info("GitHub Sideload unloading")

    async def _uninstall(self):
        decky.logger.info("GitHub Sideload uninstall cleanup")
