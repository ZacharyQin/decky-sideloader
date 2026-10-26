import { beforeEach, describe, expect, test } from "bun:test";
import { checkFor, compactCheckSummary, initialSelection, latestScheduledReport, onlyAsset, operationProgress, orderedCheckItems, pluginSummary, shortCheckTime, versionAction } from "../src/viewModel";
import type { CheckReport, CheckStatus, InstallOperation, PluginRow, ReleaseRow } from "../src/types";
import { setLocale } from "../src/i18n";
beforeEach(() => setLocale("zh-cn"));

const release = (extra: Partial<ReleaseRow> = {}): ReleaseRow => ({ tag: "v2.0.0", title: "Version 2", repo: "a/b", prerelease: false, body: "", published_at: "", zips: [], comparison: 1, has_zip: false, ...extra });
const plugin: PluginRow = { name: "Test", folder: "test", version: "1.0.0", author: "", repo: "a/b", repo_source: "user", is_self: false, in_store: false, sideload: true, ownership: { state: "nonstore", eligible: true, intent: null, error: null } };

describe("release selection", () => {
  test("a normal release without a zip stays selected", () => {
    expect(initialSelection([release(), release({ tag: "v1", zips: [{ name: "old.zip", url: "old" }] })])).toEqual({ tag: "v2.0.0", zip: null });
  });
  test("a newer prerelease yields to the latest installable stable release", () => {
    const rows = [
      release({ tag: "dlss-enabler-4.7.4", prerelease: true, has_zip: false }),
      release({ tag: "v0.3.0-dlss4.5.3", has_zip: true, zips: [{ name: "DLSS.Enabler.zip", url: "stable" }] }),
      release({ tag: "v0.3.0", has_zip: true, zips: [{ name: "old.zip", url: "older" }] }),
    ];
    expect(initialSelection(rows)).toEqual({ tag: "v0.3.0-dlss4.5.3", zip: "stable" });
  });
  test("multiple assets require selection", () => {
    expect(onlyAsset(release({ zips: [{ name: "a.zip", url: "a" }, { name: "b.zip", url: "b" }] }))).toBeNull();
    expect(onlyAsset(release({ zips: [{ name: "a.zip", url: "a" }] }))).toBe("a");
  });
  test("newer, older, same and unknown are distinct actions", () => {
    expect(versionAction(release({ comparison: 0 }), true).type).toBe(1);
    expect(versionAction(release({ comparison: -1 }), true).type).toBe(3);
    expect(versionAction(release({ comparison: 1 }), true).type).toBe(2);
    expect(versionAction(release({ comparison: null }), true).type).toBe(4);
    expect(versionAction(release(), false).type).toBe(0);
  });
});

describe("check summaries", () => {
  test("errors and missing results are never called up to date", () => {
    const report = { checkedAt: "now", items: [{ name: "Test", repo: "a/b", current: "1.0.0", latest: null, status: "error" as const, error: "offline" }] };
    expect(pluginSummary(plugin, report)).toContain("检查失败");
    expect(pluginSummary(plugin, null)).toContain("尚未检查");
    expect(pluginSummary(plugin, { checkedAt: "now", items: [{ name: "Test", repo: "a/b", current: "1.0.0", latest: "4.7.0", status: "update", error: null, notice: "最新发布 dlss-enabler-4.7.4 没有安装包" }] })).toContain("没有安装包");
    expect(checkFor({ ...plugin, version: "2.0.0" }, report)).toBeUndefined();
    expect(checkFor({ ...plugin, repo: "another/repo" }, report)).toBeUndefined();
  });
  test("unlink is shown as stopped tracking", () => {
    expect(pluginSummary({ ...plugin, repo: null, repo_source: "disabled" }, null)).toContain("停止更新追踪");
  });
});

describe("compact scheduled reports", () => {
  const item = (status: CheckStatus) => ({ name: status, repo: "a/b", current: "1", latest: "2", status, error: null });
  test("time formatting is short and report ordering respects timezone offsets", () => {
    const now = new Date(2026, 9, 15, 18, 0);
    expect(shortCheckTime(new Date(2026, 9, 15, 15, 17).toISOString(), now)).toBe("今天 15:17");
    expect(shortCheckTime(new Date(2026, 9, 14, 15, 17).toISOString(), now)).toBe("10-14 15:17");
    expect(shortCheckTime(new Date(2025, 9, 14, 15, 17).toISOString(), now)).toBe("2025-10-14 15:17");
    expect(shortCheckTime("invalid", now)).toBe("时间未知");
    const persisted = { checkedAt: "2026-10-15T15:00:00+08:00", items: [] };
    const live = { checkedAt: "2026-10-15T07:01:00Z", items: [] };
    expect(latestScheduledReport(persisted, live)).toBe(live);
    expect(latestScheduledReport(live, persisted)).toBe(live);
    expect(latestScheduledReport(null, undefined)).toBeNull();
  });
  test("updates and exceptional results precede current and unbound without mutating the report", () => {
    const report = { checkedAt: "now", items: [item("current"), item("unbound"), item("no_asset"), item("update"), item("error")] };
    expect(orderedCheckItems(report).map((i) => i.status)).toEqual(["update", "error", "no_asset", "current", "unbound"]);
    expect(report.items[0].status).toBe("current");
  });
});

test("rechecking starts from current evidence instead of a later failed observation", () => {
  const op: InstallOperation = {
    id: "retry", stage: "checking", repo: "a/b", tag: "v2", existing_name: null,
    created_at: "2026-10-25T10:00:00Z", error: null, artifact: "", directory: "", request_id: null,
    saw_finish: false, binding_saved: false, backup: null, observations: [
      { id: "download", label: "", status: "running", updated_at: "2026-10-25T10:04:00Z" },
      { id: "dependencies", label: "", status: "failed", updated_at: "2026-10-25T10:03:00Z" },
    ],
  };
  const progress = operationProgress(op);
  expect(progress.steps[progress.current][0]).toBe("download");
  expect(progress.observation).toBe(op.observations![0]);
});
