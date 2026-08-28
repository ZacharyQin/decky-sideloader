import { callable } from "@decky/api";

import type { AppSettings, PluginListResponse, ReleaseRow, UpdateInfo } from "./types";

export const listSideloadPlugins = callable<[], PluginListResponse>("list_sideload_plugins");
export const getAppSettings = callable<[], AppSettings>("get_app_settings");
export const setAutoCheck = callable<[enabled: boolean], AppSettings>("set_auto_check");
export const setBinding = callable<[name: string, repo: string], { name: string; repo: string }>("set_binding");
export const clearBinding = callable<[name: string], void>("clear_binding");
export const listReleases = callable<[repo: string, current_version?: string | null], ReleaseRow[]>("list_releases");
export const checkUpdates = callable<[], UpdateInfo[]>("check_updates");
