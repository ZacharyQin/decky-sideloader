import { addEventListener, callable, removeEventListener } from "@decky/api";
import { validate } from "compare-versions";
import { readLoaderPlugins } from "./loaderBridge";

type UpdateCache = { channel: string; names: string[] };
type Request = { id: string; refresh: boolean; cache: UpdateCache | null };
const respond = callable<[identifier: string, payload: unknown], void>("receive_native_management");

async function updateNames(url: string, version: string): Promise<string[]> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const renew = () => { clearTimeout(timer); timer = setTimeout(() => controller.abort(), 5000); };
  renew();
  try {
    const response = await fetch(url + "/versions", { headers: { "X-Decky-Version": version }, signal: controller.signal });
    if (!response.ok || !response.body) throw new Error("Loader 更新目录读取失败");
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4 * 1024 * 1024) { await reader.cancel(); throw new Error("Loader 更新目录过大"); }
      chunks.push(value); renew();
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const data: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (!data || typeof data !== "object" || Array.isArray(data) || Object.keys(data).length > 4096) throw new Error("Loader 更新目录格式无效");
    const names: string[] = [];
    for (const [name, entry] of Object.entries(data)) {
      if (!name || name.length > 256 || /[\x00-\x1f\x7f]/.test(name) || !Array.isArray(entry) || typeof entry[0] !== "string" || typeof entry[1] !== "string") throw new Error("Loader 更新目录条目无效");
      if (validate(entry[0])) names.push(name);
    }
    return names;
  } finally { clearTimeout(timer!); }
}

export function startNativeManagement(): () => void {
  let disposed = false;
  const listener = addEventListener<[Request]>("native_management_requested", async request => {
    const snapshot = await readLoaderPlugins();
    let cache: UpdateCache | null = null;
    let capable: string[] | null = null;
    let error: string | null = null;
    try {
      if (snapshot.ok) {
        const host = window as unknown as { DeckyBackend: { call: (route: string, ...args: unknown[]) => Promise<unknown> }; DeckyPluginLoader: { updateVersion: () => Promise<{ current: string }> } };
        const store = Number(await host.DeckyBackend.call("utilities/settings/get", "store", 0));
        const channel = store === 1 ? "https://testing.deckbrew.xyz/plugins" : store === 2 ? await host.DeckyBackend.call("utilities/settings/get", "store-url", "https://plugins.deckbrew.xyz/plugins") : "https://plugins.deckbrew.xyz/plugins";
        if (typeof channel !== "string" || !/^https?:\/\//.test(channel) || channel.length > 4096) throw new Error("Loader 商店地址无效");
        cache = request.cache?.channel === channel ? request.cache : null;
        if (request.refresh) cache = { channel, names: await updateNames(channel, (await host.DeckyPluginLoader.updateVersion()).current) };
        if (cache) {
          const available = new Set(cache.names);
          capable = snapshot.plugins.filter(p => available.has(p.name) && validate(p.version || "0.0.0")).map(p => p.name);
        }
      }
    } catch { error = "Loader 更新目录读取失败"; }
    if (!disposed) await respond(request.id, { snapshot, capable, cache: request.refresh && !error ? cache : null, error });
  });
  return () => { disposed = true; removeEventListener("native_management_requested", listener); };
}
