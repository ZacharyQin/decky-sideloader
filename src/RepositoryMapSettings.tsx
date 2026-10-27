import { useEffect, useRef, useState } from "react";
import { getRepositoryMapStatus, updateRepositoryMap } from "./api";
import { refreshAfterPluginMutation, useChecks } from "./checkController";
import { Action, PanelSection, Status } from "./components";
import { errorText } from "./errors";
import { isActive, useInstallation } from "./installController";
import { t } from "./i18n";
import type { RepositoryMapStatus, RepositoryMapUpdate } from "./types";
import { messages, type Message } from "./locales";

function mappingMessage(message: string) {
  return Object.prototype.hasOwnProperty.call(messages, message) ? t(message as Message) : message;
}

let pending: Promise<RepositoryMapUpdate> | null = null;
function updateMap() {
  if (!pending) {
    const request = updateRepositoryMap().then(async result => {
      await refreshAfterPluginMutation();
      return result;
    });
    pending = request;
    const clear = () => { if (pending === request) pending = null; };
    void request.then(clear, clear);
  }
  return pending;
}

export function RepositoryMapSettings({ disabled = false }: { disabled?: boolean }) {
  const { checking, refreshing } = useChecks();
  const { operation, preparing } = useInstallation();
  const [status, setStatus] = useState<RepositoryMapStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changed, setChanged] = useState<boolean | null>(null);
  const request = useRef(0);
  const observe = async (promise: Promise<RepositoryMapStatus>, updating: boolean) => {
    const ticket = ++request.current;
    setBusy(updating); setError(null); setChanged(null);
    try {
      const result = await promise;
      if (request.current === ticket) {
        setStatus(result);
        if ("changed" in result) setChanged((result as RepositoryMapUpdate).changed);
      }
    } catch (failure) {
      if (request.current === ticket) setError(errorText(failure));
    } finally {
      if (request.current === ticket) setBusy(false);
    }
  };
  useEffect(() => {
    void observe(pending ?? getRepositoryMapStatus(), Boolean(pending));
    return () => { ++request.current; };
  }, []);
  const blocked = disabled || checking || refreshing || preparing || isActive(operation);
  return <PanelSection title={t("仓库映射表")}>
    {status && <Status title={t("修订 {0} · {1} 个插件", [status.revision, status.count])}>{t("数据更新于 {0}", [status.updated_at])}</Status>}
    {!status && !error && !busy && <Status tone="loading" title={t("正在读取映射表…")} />}
    {status?.warning && <Status tone="warning" title={mappingMessage(status.warning)} />}
    <Action secondary kind="updateRepositoryMap" loading={busy} disabled={blocked || busy}
      onClick={() => { if (!blocked && !busy) void observe(updateMap(), true); }}>{t("更新映射表")}</Action>
    {changed !== null && <Status tone="success" title={t(changed ? "仓库映射表已更新" : "仓库映射表已是最新")} />}
    {error && <Status tone="error" title={t("仓库映射表操作失败")}>{mappingMessage(error)}</Status>}
  </PanelSection>;
}
