import { expect, test } from "bun:test";
import { mergeLoaderPlugins, pluginKey, pluginReadOnly, validateLoaderPlugins } from "../src/loaderManagement";
import type { PluginListResponse, PluginRow } from "../src/types";
const disk = (patch: Partial<PluginRow> = {}): PluginRow => ({ name: "Example", folder: "example", version: "1", author: "", repo: "acme/example", repo_source: "user", in_store: null, is_self: false, sideload: true, ...patch });
const list = (...plugins: PluginRow[]): PluginListResponse => ({ plugins, store_ok: false, store_error: "timeout", last_check: null, catalog: { source: "cache", stale: true, timestamp: 1, warning: "stale" } });
const native = (name = "Example", disabled = false) => ({ name, version: "2", disabled, load_type: 0 });
test("unique Loader match preserves package version, explicit binding and stale store facts", () => {
  const result = mergeLoaderPlugins(list(disk()), { ok: true, plugins: [native()] });
  expect(result.plugins[0]).toMatchObject({ version: "1", repo_source: "user", in_store: null, management: { state: "enabled", version: "2", versionMismatch: true } });
  expect(result.catalog?.stale).toBe(true);
  expect(result.store_error).toBe("timeout");
});
test("disabled, native-only, disk-only and self stay distinct", () => {
  const result = mergeLoaderPlugins(list(disk({ repo_source: "disabled", repo: null })), { ok: true, plugins: [native("Native", true), native("Other"), native("GitHub Sideload")] });
  expect(result.plugins[0]).toMatchObject({ repo_source: "disabled", management: { state: "unregistered" } });
  expect(result.plugins[1]).toMatchObject({ folder: null, version: null, in_store: null, repo: null, management: { state: "disabled", version: "2" } });
  expect(pluginReadOnly(result.plugins[1])).toBe(true);
  expect(pluginKey(result.plugins[1])).not.toBe(pluginKey(result.plugins[2]));
  expect(pluginKey(result.plugins[0])).toBe("example");
  expect(result.plugins[3].is_self).toBe(true);
});
test("Native-only keys cannot collide with disk directory basenames", () => {
  const result = mergeLoaderPlugins(list(disk({ folder: 'loader:"Native"' })), { ok: true, plugins: [native("Native")] });
  expect(pluginKey(result.plugins[0])).toBe('loader:"Native"');
  expect(pluginKey(result.plugins[1])).toBe('loader/"Native"');
  expect(new Set(result.plugins.map(pluginKey)).size).toBe(2);
});
test("duplicate disk names retain all directories read-only without choosing ownership", () => {
  const result = mergeLoaderPlugins(list(disk(), disk({ folder: "other" })), { ok: true, plugins: [native()] });
  expect(result.plugins).toHaveLength(2);
  expect(result.plugins.every((p) => p.management?.state === "ambiguous" && pluginReadOnly(p))).toBe(true);
});
test("empty snapshot is valid; failed snapshot is unknown and never store-negative", () => {
  const source = list(disk());
  expect(validateLoaderPlugins([])).toEqual([]);
  expect(mergeLoaderPlugins(source, { ok: true, plugins: [] }).plugins[0].management?.state).toBe("unregistered");
  const failed = mergeLoaderPlugins(source, { ok: false, error: "timeout" });
  expect(failed.plugins[0]).toMatchObject({ in_store: null, management: { state: "unknown" } });
  expect(pluginReadOnly(failed.plugins[0])).toBe(false);
  expect(failed.loader).toMatchObject({ ok: false, error: "timeout" });
});
test("malformed and duplicate native snapshots reject atomically", () => {
  for (const value of [null, {}, [native(), native()], [{ ...native(), disabled: 0 }], [{ ...native(), version: undefined }], [{ ...native(), load_type: NaN }]]) expect(() => validateLoaderPlugins(value)).toThrow();
});
