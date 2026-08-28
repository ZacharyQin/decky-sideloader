import { ButtonItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { useEffect, useState } from "react";

import { checkUpdates, getAppSettings, setAutoCheck } from "./api";
import { probeLoaderChannel } from "./loaderBridge";
import { toastError, toastInfo } from "./toast";

export function SettingsTab() {
  const [autoCheck, setAutoCheckState] = useState(false);
  const [busy, setBusy] = useState(false);
  const channel = probeLoaderChannel();

  useEffect(() => {
    void getAppSettings()
      .then((settings) => setAutoCheckState(settings.autoCheck))
      .catch(toastError);
  }, []);

  const onToggle = async (enabled: boolean) => {
    setAutoCheckState(enabled);
    try {
      await setAutoCheck(enabled);
    } catch (err) {
      setAutoCheckState(!enabled);
      toastError(err);
    }
  };

  const onCheck = async () => {
    setBusy(true);
    try {
      const updates = await checkUpdates();
      if (updates.length === 0) {
        toastInfo("没有可比较的更新");
      } else {
        toastInfo(updates.map((item) => `${item.name} → ${item.latest}`).join("；"));
      }
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <PanelSection title="更新检查">
      <PanelSectionRow>
        <ButtonItem layout="below" disabled>
          {channel.ok ? "安装通道可用" : channel.reason}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="定期检查更新"
          description="按设置间隔查询已绑定仓库，有更新时弹出通知，不会自动安装"
          checked={autoCheck}
          onChange={(value) => void onToggle(value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={() => void onCheck()}>
          立即检查更新
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
