import { describe, expect, test } from "bun:test";
import { checkFor, compactCheckSummary, initialSelection, latestScheduledReport, onlyAsset, orderedCheckItems, pluginSummary, shortCheckTime, versionAction } from "../src/viewModel";
import type { CheckReport, CheckStatus, PluginRow, ReleaseRow } from "../src/types";

const release = (extra: Partial<ReleaseRow> = {}): ReleaseRow => ({ tag: "v2.0.0", title: "Version 2", repo: "a/b", prerelease: false, body: "", published_at: "", zips: [], comparison: 1, has_zip: false, ...extra });
const plugin: PluginRow = { name: "Test", folder: "test", version: "1.0.0", author: "", repo: "a/b", repo_source: "user", is_self: false, in_store: false, sideload: true };

describe("release selection", () => {
  test("latest without a ZIP never falls back to an older release", () => {
    expect(initialSelection([release(), release({ tag: "v1", zips: [{ name: "old.zip", url: "old" }] })])).toEqual({ tag: "v2.0.0", zip: null });
  });
  test("multiple assets require selection", () => {
    expect(onlyAsset(release({ zips: [{ name: "a.zip", url: "a" }, { name: "b.zip", url: "b" }] }))).toBeNull();
    expect(onlyAsset(release({ zips: [{ name: "a.zip", url: "a" }] }))).toBe("a");
  });
  test("newer, older, same and unknown are distinct actions", () => {
    expect(versionAction(release({ comparison: 0 }), true).label).toStartWith("重新安装");
    expect(versionAction(release({ comparison: -1 }), true).label).toStartWith("切换到");
    expect(versionAction(release({ comparison: 1 }), true).label).toStartWith("更新到");
    expect(versionAction(release({ comparison: null }), true).label).toStartWith("安装所选版本");
    expect(versionAction(release(), false).type).toBe(0);
  });
});

describe("check summaries", () => {
  test("errors and missing results are never called up to date", () => {
    const report = { checkedAt: "now", items: [{ name: "Test", repo: "a/b", current: "1.0.0", latest: null, status: "error" as const, error: "offline" }] };
    expect(pluginSummary(plugin, report)).toContain("检查失败");
    expect(pluginSummary(plugin, null)).toContain("尚未检查");
    expect(checkFor({ ...plugin, version: "2.0.0" }, report)).toBeUndefined();
    expect(checkFor({ ...plugin, repo: "another/repo" }, report)).toBeUndefined();
  });
  test("unlink is shown as stopped tracking", () => {
    expect(pluginSummary({ ...plugin, repo: null, repo_source: "disabled" }, null)).toContain("停止更新追踪");
  });
});

describe("compact scheduled reports", () => {
  const item = (status: CheckStatus) => ({ name: status, repo: "a/b", current: "1", latest: "2", status, error: null });
  test("only nonzero actionable counts appear in the settings summary", () => {
    const report: CheckReport = { checkedAt: "2026-10-15T07:00:00Z", items: [item("update"), item("current"), item("unbound"), item("no_asset"), item("uncomparable")] };
    expect(compactCheckSummary(report)).toBe("可更新 1，无法确认 2");
    expect(compactCheckSummary({ ...report, items: [item("error")] })).toBe("失败 1");
    expect(compactCheckSummary({ ...report, items: [item("current")] })).toBe("没有可用更新");
    expect(compactCheckSummary({ ...report, items: [item("unbound")] })).toBe("尚无可检查的关联仓库");
    expect(compactCheckSummary({ ...report, items: [] })).toBe("没有可检查的插件");
    expect(compactCheckSummary({ ...report, error: "long private diagnostic" })).toBe("检查失败，查看原因");
  });
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
