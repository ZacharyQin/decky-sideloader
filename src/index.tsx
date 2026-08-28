import { Tabs, staticClasses } from "@decky/ui";
import { addEventListener, definePlugin, removeEventListener } from "@decky/api";
import { useState } from "react";
import { FaGithub } from "react-icons/fa";

import { InstallTab } from "./InstallTab";
import { InstalledTab } from "./InstalledTab";
import { SettingsTab } from "./SettingsTab";
import { toastInfo } from "./toast";
import type { UpdateInfo } from "./types";

function Content() {
  const [tab, setTab] = useState("installed");

  return (
    <Tabs
      activeTab={tab}
      onShowTab={(id: string) => setTab(id)}
      tabs={[
        { id: "installed", title: "已安装", content: <InstalledTab /> },
        { id: "install", title: "安装", content: <InstallTab /> },
        { id: "settings", title: "设置", content: <SettingsTab /> },
      ]}
    />
  );
}

export default definePlugin(() => {
  const listener = addEventListener<[updates: UpdateInfo[]]>("updates_found", (updates) => {
    if (!updates?.length) {
      return;
    }
    toastInfo(updates.map((item) => `${item.name} 可更新到 ${item.latest}`).join("；"));
  });

  return {
    name: "GitHub Sideload",
    titleView: <div className={staticClasses.Title}>GitHub Sideload</div>,
    content: <Content />,
    icon: <FaGithub />,
    onDismount() {
      removeEventListener("updates_found", listener);
    },
  };
});
