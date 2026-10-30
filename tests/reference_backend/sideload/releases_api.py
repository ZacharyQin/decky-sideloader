import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.parse
import decky
from sideload import http_client
from sideload.github_meta import assess_checked_release, compare_versions, git_tree_version, label_version, parse_github_input, parse_releases
from sideload.paths import _settings_path
from sideload.persist import load_settings, save_settings, skipped_tags
from sideload.release_cache import ReleaseCache, release_identity
from sideload.repo_probe import probe_repository
from sideload.rpc import _observed_rpc
from sideload.diagnostic_context import DiagnosticError, error_fields, identifier
from sideload.zip_version import probe_release_zip
from sideload.self_update import ACTIVE as SELF_UPDATE_ACTIVE, require_no_self_update


GITHUB_API = "https://api.github.com/repos/{repo}/releases"


def _search_repositories(query: str, fetch=None) -> list[dict]:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 100:
        raise RuntimeError("请输入 1 至 100 个字符的插件名或关键词")
    query = query.strip()
    if not any(word.lower().startswith("fork:") for word in query.split()):
        query += " fork:true"
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": query, "per_page": 20})
    payload = (fetch or http_client._fetch_json)(url, accept="application/vnd.github+json")
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
        payload = (fetch or http_client._fetch_json)(
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
        release["version"] = label_version(release["tag"], [item["name"] for item in release["zips"]])
        release["comparison"] = compare_versions(release["version"], current_version) if release["version"] else None
        release["has_zip"] = len(release["zips"]) > 0
    return releases


def _probe_plugin_release(plugin, releases, transport, fetch_json):
    packaged = next((item for item in releases if item.get("has_zip")), None)
    zips = packaged.get("zips") if isinstance(packaged, dict) else None
    package_state, package_version = "skipped", None
    if isinstance(zips, list) and len(zips) == 1 and isinstance(zips[0].get("url"), str):
        package_state, package_version = probe_release_zip(zips[0]["url"], transport)
    label = None if not isinstance(packaged, dict) else packaged.get("version") or label_version(packaged.get("tag"), [item.get("name") for item in (zips or []) if isinstance(item, dict)])
    git_version = None
    if packaged and package_state not in ("semver", "raw", "absent") and not label and plugin.get("repo") and packaged.get("tag"):
        git_version = git_tree_version(plugin["repo"], packaged["tag"], plugin["name"], fetch_json)
    if packaged is not None:
        packaged["version_evidence"] = {"package_state": package_state, "package_version": package_version, "git_version": git_version}


def _cached_plugin_release(plugin, releases, checked_at):
    result = {**release_identity(plugin), "latest": None, "status": "unbound", "error": None, "checkedAt": checked_at}
    if not plugin.get("repo"):
        return result
    skipped = set(plugin.get("skipped_tags") or [])
    visible = [row for row in releases if row.get("tag") not in skipped]
    if releases and not visible:
        return {**result, "status": "current", "notice": "已跳过全部新版本"}
    chosen = next((row for row in visible if row.get("has_zip")), visible[0] if visible else None)
    result.update(assess_checked_release(visible, plugin.get("version"), **((chosen or {}).get("version_evidence") or {})))
    if chosen:
        result["release_tag"] = chosen.get("tag")
    if skipped and visible:
        note = "已跳过 " + "、".join(sorted(skipped)[:3])
        result["notice"] = f"{result['notice']}，{note}" if result["notice"] else note
    return result


class ReleasesAPI:
    def _release_reader(self):
        fetch = self._query_fetch()
        return lambda repo, current: _releases_for(repo, current, fetch=fetch)

    @_observed_rpc
    async def set_skipped_release(self, name: str, tag: str, skipped: bool) -> dict:
        if type(skipped) is not bool:
            raise RuntimeError("跳过标记必须为布尔值")
        if not isinstance(tag, str) or not tag.strip() or len(tag) > 128 or any(ord(c) < 32 for c in tag):
            raise RuntimeError("版本标识无效")
        async with self._ownership_lock():
            plugin = await self._eligible_plugin(name)
        repo = plugin.get("repo")
        if not isinstance(repo, str) or not parse_github_input(repo):
            raise RuntimeError("尚未关联 GitHub，不能跳过版本")
        settings = load_settings(_settings_path())
        store = dict(settings["skippedReleases"])
        entry = store.get(name) if isinstance(store.get(name), dict) and store[name].get("repo") == repo else {"repo": repo, "tags": []}
        tags = [item for item in entry["tags"] if item != tag]
        if skipped:
            tags.append(tag)
        if len(tags) > 20:
            raise RuntimeError("这个插件跳过的版本过多")
        if tags:
            store[name] = {"repo": repo, "tags": tags}
        else:
            store.pop(name, None)
        if len(store) > 64:
            raise RuntimeError("跳过记录过多")
        settings["skippedReleases"] = store
        snapshot = self._release_cache().get(plugin)
        previous = settings.get("lastCheck") or {}
        items = previous.get("items", [])
        old = next((item for item in items if all(item.get(key) == value for key, value in release_identity(plugin).items())), None)
        if not old or old.get("status") != "error":
            retained = [item for item in items if item.get("name") != name]
            if snapshot:
                result = _cached_plugin_release({**plugin, "skipped_tags": tags}, snapshot["releases"], snapshot["checkedAt"])
                chosen = next((row for row in snapshot["releases"] if row.get("tag") == result.get("release_tag")), None)
                # Older snapshots lack probe evidence; preserve a known result for the unchanged target.
                if old and chosen and not chosen.get("version_evidence") and old.get("release_tag") == result.get("release_tag"):
                    result["status"], result["latest"] = old["status"], old.get("latest")
                retained.append(result)
            if previous or snapshot:
                settings["lastCheck"] = {**previous, "checkedAt": previous.get("checkedAt", snapshot["checkedAt"] if snapshot else None),
                                         "fullCheckedAt": previous.get("fullCheckedAt", previous.get("checkedAt")), "items": retained}
        save_settings(_settings_path(), settings)
        return await self.list_sideload_plugins()

    def _release_cache(self):
        if not hasattr(self, "_releases"):
            self._releases = ReleaseCache(Path(decky.DECKY_PLUGIN_RUNTIME_DIR) / "cache" / "releases.json")
        return self._releases

    @_observed_rpc
    async def search_repositories(self, query: str) -> list[dict]:
        return await asyncio.to_thread(_search_repositories, query, self._query_fetch())

    @_observed_rpc
    async def probe_repository(self, repo: str) -> dict:
        parsed = parse_github_input(repo) if isinstance(repo, str) else None
        if not parsed:
            raise DiagnosticError("invalid_input", "GitHub 链接无效")
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

    def _release_result(self, plugin):
        report = load_settings(_settings_path()).get("lastCheck") or {}
        item = next((item for item in report.get("items", []) if all(item.get(key) == value for key, value in release_identity(plugin).items())), None)
        return {"snapshot": self._release_cache().get(plugin), "error": item.get("error") if item and item.get("status") == "error" else None}, item.get("checkedAt") if item else None

    @_observed_rpc
    async def get_plugin_releases(self, repo: str, current_version: str | None, name: str, folder: str | None, refresh: bool = False) -> dict:
        if type(refresh) is not bool:
            raise RuntimeError("刷新参数无效")
        plugin = await self._eligible_plugin(name)
        if (plugin["folder"], plugin.get("repo"), plugin.get("version")) != (folder, repo, current_version):
            raise DiagnosticError("identity_changed", "插件信息已变化，请返回列表重新打开")
        result, previous_check = self._release_result(plugin)
        if not refresh and result["snapshot"] is not None:
            return result
        if not hasattr(self, "_release_reads"):
            self._release_reads = {}
        key = json.dumps(release_identity(plugin), sort_keys=True)
        task = self._release_reads.get(key)
        if task is None:
            task = asyncio.create_task(self._read_plugin_releases(plugin, previous_check))
            self._release_reads[key] = task
            def finished(done):
                if self._release_reads.get(key) is done:
                    del self._release_reads[key]
                if not done.cancelled():
                    done.exception()  # Retrieve errors even when the original caller left.
            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _read_plugin_releases(self, plugin, previous_check):
        if not hasattr(self, "_check_lock"):
            self._check_lock = asyncio.Lock()
        async with self._check_lock:
            current = await self._eligible_plugin(plugin["name"])
            if release_identity(current) != release_identity(plugin):
                raise DiagnosticError("identity_changed", "插件信息已变化，请返回列表重新打开")
            cached, checked_at = self._release_result(plugin)
            if checked_at is not None and checked_at != previous_check:
                return cached  # Reuse a full check that completed while this read waited.
            releases, result = await self._check_plugin_release(plugin)
            current = await self._eligible_plugin(plugin["name"])
            if release_identity(current) != release_identity(plugin):
                raise DiagnosticError("identity_changed", "插件信息已变化，请返回列表重新打开")
            if result["status"] != "error":
                result = _cached_plugin_release(current, releases, result["checkedAt"])
            settings = load_settings(_settings_path())
            previous = settings.get("lastCheck") or {}
            items = [item for item in previous.get("items", []) if item["name"] != plugin["name"]]
            items.append(result)
            settings["lastCheck"] = {
                "checkedAt": result["checkedAt"], "source": "manual", "items": items,
                "fullCheckedAt": previous.get("fullCheckedAt", previous.get("checkedAt")),
            }
            save_settings(_settings_path(), settings)
            snapshot = self._release_cache().get(plugin) if result["status"] == "error" else {**release_identity(plugin), "checkedAt": result["checkedAt"], "releases": releases}
            return {"snapshot": snapshot, "error": result["error"]}

    async def _check_plugin_release(self, plugin: dict) -> tuple[list[dict], dict]:
        releases = []
        result = {"name": plugin["name"], "repo": plugin.get("repo"), "current": plugin.get("version"), "latest": None, "status": "unbound", "error": None,
                  "folder": plugin["folder"], "ownership_id": (plugin["ownership"].get("intent") or {}).get("id")}
        if plugin.get("repo"):
            try:
                releases = await asyncio.to_thread(self._release_reader(), plugin["repo"], plugin.get("version"))
                skipped = set(plugin.get("skipped_tags") or [])
                visible = [item for item in releases if item.get("tag") not in skipped]
                if visible:
                    await asyncio.to_thread(_probe_plugin_release, plugin, visible, self._transport(), self._query_fetch())
                result = _cached_plugin_release(plugin, releases, None)
            except Exception as error:
                result["status"], result["error"] = "error", str(error)
                decky.logger.warning("check %s failed: %s", plugin["name"], error)
                self._diagnostic("check_summary", pluginId=identifier("plugin", plugin["name"]), repoId=identifier("repo", plugin.get("repo")), errors=1, **error_fields(error))
        result["checkedAt"] = datetime.now(timezone.utc).isoformat()
        if result["status"] not in ("error", "unbound"):
            listing = await self.list_sideload_plugins()
            current = next((row for row in listing["plugins"] if row["name"] == plugin["name"]), None)
            if current and current["ownership"]["eligible"] and release_identity(current) == release_identity(plugin):
                if current.get("skipped_tags") != plugin.get("skipped_tags"):
                    result = _cached_plugin_release(current, releases, result["checkedAt"])
                self._release_cache().put(plugin, releases, result["checkedAt"])
        return releases, result

    @_observed_rpc
    async def check_updates(self) -> dict:
        return await self._check_updates("manual")

    async def _check_updates(self, source: str) -> dict:
        require_no_self_update(self)
        if not hasattr(self, "_check_lock"):
            self._check_lock = asyncio.Lock()
        async with self._check_lock:
            listing = await self.list_sideload_plugins()
            results, checked_plugins = [], []
            for plugin in listing["plugins"]:
                if not plugin.get("sideload"):
                    continue
                _, result = await self._check_plugin_release(plugin)
                results.append(result)
                checked_plugins.append(plugin)
            report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "source": source, "items": results}
            settings = load_settings(_settings_path())
            for index, plugin in enumerate(checked_plugins):
                tags = skipped_tags(settings, plugin["name"], plugin.get("repo"))
                if tags != (plugin.get("skipped_tags") or []) and results[index]["status"] != "error":
                    snapshot = self._release_cache().get(plugin)
                    if snapshot:
                        results[index] = _cached_plugin_release({**plugin, "skipped_tags": tags}, snapshot["releases"], results[index]["checkedAt"])
            settings["lastCheck"] = report
            if source == "scheduled":
                settings["lastScheduledCheck"] = report
            save_settings(_settings_path(), settings)
            self._diagnostic("check_summary", source=source, count=len(results), errors=sum(item["status"] == "error" for item in results), updates=sum(item["status"] == "update" for item in results))
            # A return/binding change during GitHub I/O cannot restore an old
            # eligibility snapshot. The report remains an immutable history.
            current = await self.list_sideload_plugins()
            current["last_check"] = report
            if hasattr(self, "_self_record"):
                current["self_update"] = await self.check_self_update()
            return current

    async def _scheduled_check(self) -> None:
        self_update = None
        try:
            listing = await self._check_updates("scheduled")
            report = listing["last_check"]
            self_update = listing.get("self_update")
        except Exception as error:
            report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "source": "scheduled", "items": [], "error": str(error) or type(error).__name__}
            settings = load_settings(_settings_path())
            settings["lastCheck"] = settings["lastScheduledCheck"] = report
            save_settings(_settings_path(), settings)
            self._diagnostic("check_summary", source="scheduled", count=0, errors=1)
        # Delivery failure must not rewrite a completed check as a failed check.
        settings = load_settings(_settings_path())
        try:
            await decky.emit("scheduled_check_completed", {"report": report, "checkNotifications": settings["checkNotifications"],
                                                         "self_update": self_update})
        except Exception:
            decky.logger.exception("scheduled check event delivery failed")

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                settings = load_settings(_settings_path())
                if not settings["autoCheck"]:
                    continue
                if getattr(self, "_self_preparing", False) or (getattr(self, "_self_record", {}).get("operation") or {}).get("stage") in SELF_UPDATE_ACTIVE:
                    continue
                report = settings.get("lastCheck") or {}
                last = report.get("fullCheckedAt", report.get("checkedAt"))
                if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < settings["autoCheckMinutes"] * 60:
                    continue
                await self._scheduled_check()
            except asyncio.CancelledError:
                raise
            except Exception:
                decky.logger.exception("automatic update check failed")
