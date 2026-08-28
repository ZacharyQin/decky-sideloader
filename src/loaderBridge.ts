export enum InstallType {
  INSTALL = 0,
  REINSTALL = 1,
  UPDATE = 2,
  DOWNGRADE = 3,
  OVERWRITE = 4,
}

export type ChannelProbe = {
  ok: boolean;
  reason: string;
};

type DeckyBackend = {
  call: (route: string, ...args: unknown[]) => Promise<unknown>;
};

type DeckyPluginLoaderHost = {
  uninstallPlugin: (name: string, title: string, buttonText: string, description: string) => void;
};

type LoaderWindow = Window & {
  DeckyBackend?: DeckyBackend;
  DeckyPluginLoader?: DeckyPluginLoaderHost;
};

const SELF_NAME = "GitHub Sideload";

function loaderWindow(): LoaderWindow {
  return window as LoaderWindow;
}

export function probeLoaderChannel(): ChannelProbe {
  const host = loaderWindow();
  if (typeof host.DeckyBackend?.call !== "function") {
    return { ok: false, reason: "window.DeckyBackend.call 不存在" };
  }
  if (typeof host.DeckyPluginLoader?.uninstallPlugin !== "function") {
    return { ok: false, reason: "window.DeckyPluginLoader.uninstallPlugin 不存在" };
  }
  return { ok: true, reason: "ok" };
}

export async function installFromArtifact(
  artifact: string,
  name: string,
  version = "dev",
  installType: InstallType = InstallType.INSTALL,
): Promise<unknown> {
  const probe = probeLoaderChannel();
  if (!probe.ok) {
    throw new Error(probe.reason);
  }
  return loaderWindow().DeckyBackend!.call(
    "utilities/install_plugin",
    artifact,
    name,
    version,
    "",
    installType,
  );
}

export function uninstallPlugin(name: string): void {
  if (name === SELF_NAME) {
    throw new Error("拒绝卸载 GitHub Sideload 自身");
  }
  const probe = probeLoaderChannel();
  if (!probe.ok) {
    throw new Error(probe.reason);
  }
  loaderWindow().DeckyPluginLoader!.uninstallPlugin(
    name,
    `卸载 ${name}`,
    "卸载",
    `确定卸载 ${name}？homebrew/settings 里的设置会保留。`,
  );
}
