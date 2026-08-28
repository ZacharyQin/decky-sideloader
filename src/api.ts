import { callable } from "@decky/api";

import type { PluginListResponse } from "./types";

export const listSideloadPlugins = callable<[], PluginListResponse>("list_sideload_plugins");
