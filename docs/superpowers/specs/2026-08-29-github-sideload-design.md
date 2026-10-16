# GitHub Sideload 设计

> 历史首版方案。当前界面与安装行为以 `docs/upstream-conflict/sideload/plugin-management.md` 为准，进度见 `HANDOFF.md`。本文 2.4 中 Loader 保留 settings 的事实不能替代实际备份；当前实现已单独备份标准 settings/data。

日期：2026-08-29

本文是第一版规划，不是 implementation plan。实现前必须先完成「安装通道」spike。

## 1. 问题

目标设备上有一批未上 Decky 商店的插件，更新走 GitHub zip。当前人工路径：

1. 发现 GitHub 有新版本
2. 下载 zip 到 Downloads
3. 进 gamescope，在 Decky 卸载旧插件
4. Decky 设置 → 开发者 → 从 zip 安装
5. 等待安装结束

本插件要替代这条路径，覆盖存量管理与从 GitHub 新增。

## 2. 已核实的 Loader 事实

来源：

- wiki getting-started：https://wiki.deckbrew.xyz/plugin-dev/getting-started
- wiki settings / 手动安装：https://wiki.deckbrew.xyz/en/user-guide/settings
- loader `backend/decky_loader/plugin/plugin.py`
- loader `backend/decky_loader/browser.py`
- loader `backend/decky_loader/loader.py`
- loader `frontend/src/store.tsx`
- loader `frontend/src/plugin.ts`
- loader `frontend/src/components/settings/pages/developer/index.tsx`

### 2.1 本机插件如何被发现

Loader 扫描插件目录下每个含 `plugin.json` 的子目录。身份是 `plugin.json.name`（菜单显示名）。版本是同目录 `package.json.version`。目录名可以和显示名不同。`find_plugin_folder(name)` 靠遍历 `plugin.json.name` 反查目录。

### 2.2 没有「zip 安装」来源字段

`plugin.json` 官方字段只有 `name`、`author`、`flags`、`api_version`、`publish.{tags,description,image}`。没有 GitHub URL，没有 install origin。

商店更新在 loader 前端 `checkForPluginUpdates`：用本机 `plugin.name` 去当前商店列表里找同名项，再 `compare-versions` 比最新 `versions[0].name`。不在商店目录里的插件，官方更新器不会管。

因此「非商店源插件列表」可做的数据是：

1. 本机 `DECKY_HOME/plugins/*/plugin.json` + `package.json`
2. 当前商店目录（默认 `https://plugins.deckbrew.xyz/plugins`，测试/自定义商店走 loader 设置 `store` / `store-url`）
3. 差集：本机有、商店列表无同名
4. 本插件自己的绑定表（用户补的 GitHub 链接）

这不是安装途径的精确还原。商店插件若当初用 zip 装，名字仍在商店目录里，会被当成商店插件。自定义商店里的插件也会被当成商店插件。规划接受这个定义：本插件管「当前商店目录覆盖不到的本机插件」，外加用户强制绑定的条目。

### 2.3 GitHub 链接能从哪来

官方 meta 锁不住 GitHub。

可探测字段，按可靠度：

1. `package.json.repository`（官方模板自己就有 `git+https://github.com/...`）
2. `package.json.homepage`，仅当 host 是 github.com
3. 用户手填，写入本插件 `DECKY_PLUGIN_SETTINGS_DIR`

不扫 README。误报高。

### 2.4 官方安装/卸载已经在做「卸旧装新」

`PluginBrowser._install`：

- artifact 可以是 `https://...zip` 或 `file:///path/to.zip`
- 已安装则先 `uninstall_plugin`（停进程、`rmtree` 插件目录、清 frozen/hidden/order/disabled）
- 再 `extractall` 到插件根目录
- 下 `remote_binary`、chown、`import_plugin`

设置文件在 `~/homebrew/settings/<plugin_directory>/`，不在插件目录里。卸载不删这份 settings。用户说的「备份数据」在官方路径里通常不必另做一份拷贝；要防的是新旧 zip 顶层目录名变了，settings 目录对不上。

开发者页「从 zip 安装」走 `file://` + `installFromURL`。`version == "dev"` 时才从 zip 里的唯一 `*/plugin.json` 解析显示名。商店安装带真实版本号和 hash。

`InstallType`：INSTALL / REINSTALL / UPDATE / DOWNGRADE / OVERWRITE。这些值只影响确认弹窗文案，后端 `_install` 不按类型分支。

插件 Python 沙箱没有官方 `utilities/install_plugin`。该 WS 路由挂在 loader 进程，由 loader 前端 `DeckyBackend.callable` 调用。插件 `@decky/api` 只能打到自己的 `main.py`。

## 3. 三种做法

### A. 复用 loader 安装通道（推荐）

插件列出本机插件、查 GitHub、选 zip URL，然后调用与开发者页相同的 `utilities/install_plugin` / `utilities/uninstall_plugin`。

好处：确认弹窗、hash、`remote_binary`、权限、热加载都是 loader 的。本插件不复制卸载装载。

代价：插件 JS 上下文能否调用 `DeckyBackend` 未核实。这是第一期 spike。调不到就不能选 A。

### B. 本插件后端自己下 zip、解压、重启 loader

`plugin.json` 加 `root`，Python 写 `DECKY_HOME/plugins`。

好处：不依赖 loader 未公开前端 API。

代价：要自己处理 zip 布局、hash、`remote_binary`、chown、停旧进程。装完如何 `import_plugin` 没有官方插件 API。可能要重启 `plugin_loader`。和 loader 抢同一目录，失败时难回滚。wiki 写 `root` 只在真正需要时用。

### C. 自建自定义商店 JSON，让用户改 Decky 商店地址

把 GitHub 插件编成 loader 能读的 store API。

好处：官方更新器可直接用。

代价：用户必须改商店频道；官方商店插件会从默认目录消失，除非自建商店做聚合。这不是「管存量 zip 插件」，是另一个产品。不做。

第一期只验证 A。A 失败再评估 B 的最小安装面，不先加 `root`。

## 4. 产品范围

做：

1. 列出本机非商店目录插件（差集定义见 2.2）
2. 为某插件补充/覆盖 GitHub 链接；若 `package.json` 能解析则预填
3. 已绑定仓库的插件：拉 GitHub Releases，选版本安装（默认 latest 的 zip asset）
4. 手动一键检查；可开定时检查；有更新时 `toaster` 提示「可更新到 x.y.z」
5. 删除：走 loader 卸载，禁止删自己
6. 从 GitHub 链接新装：默认 latest，可改历史 release

不做：

- 改官方商店插件的更新通道
- 自动安装更新（必须过确认）
- 扫 README 猜仓库
- 管理 loader 自身
- 发布本插件到商店（当前 `0.0.1` + `debug`）

## 5. 数据

本插件 settings JSON，目录 `DECKY_PLUGIN_SETTINGS_DIR`。

```json
{
  "bindings": {
    "Some Plugin Display Name": {
      "repo": "owner/name",
      "source": "package.json" ,
      "overridden": false
    }
  },
  "autoCheck": false,
  "autoCheckMinutes": 360,
  "lastCheckAt": null
}
```

`source` 只允许 `package.json` 或 `user`。用户手填后 `overridden: true`，之后不再被 package.json 探测覆盖。

列表项运行时组装，不持久化整表：

| 字段 | 来源 |
|---|---|
| `name` | `plugin.json.name` |
| `folder` | 插件目录名 |
| `version` | `package.json.version`，没有则 `null` |
| `author` | `plugin.json.author` |
| `inStore` | 当前商店目录是否有同名 |
| `repo` | bindings 或探测结果 |
| `isSelf` | `name == "GitHub Sideload"` |

GitHub 侧：`GET /repos/{owner}/{repo}/releases`。每个 release 选一个 zip asset。规则：

1. 名字以 `.zip` 结尾
2. 优先文件名包含 `plugin` 或仓库名
3. 多个 zip 时交给用户选，不静默猜

比较版本：本机 `package.json.version` vs release `tag_name`（去掉前导 `v`）。用 semver 能比就比，不能比就只展示「远端 tag」，不标红。

## 6. 前后端分工

系统操作在 Python。UI 在 React。

后端 RPC（名字待 spike 后定稿，这里是方向）：

- `list_plugins`：扫 `DECKY_HOME/plugins`，读 json，拉商店目录，返回列表项
- `set_binding(name, repo)` / `clear_binding(name)`
- `list_releases(repo)`
- `check_updates()`：对已绑定项比版本
- `get_settings` / `set_auto_check`

前端：

- 渲染列表与表单
- 触发安装/卸载：spike 确认后调用 loader；调用失败则禁用按钮并写原因
- `toaster` 提示可更新版本
- `_main` 里若 `autoCheck` 开启，按间隔跑 `check_updates` 再 `emit`

禁止前端直接碰文件系统。禁止 `shell=True`。GitHub 请求走后端，避免 Steam UI CORS。

## 7. UI

QAM 面板。组件全部来自 `@decky/ui` / `@decky/api`，对照 `frontend-ui.md`。

页结构用 `SidebarNavigation` 两个 page：

1. 已安装：非商店列表
2. 从 GitHub 安装：输入链接 + 版本下拉 + 安装

已安装每行：

- `PanelSection` + `PanelSectionRow`
- 标题用插件 `name`，description 用 `version` 与绑定状态
- `ButtonItem`：详情/绑定/检查
- 详情里：`TextField` 填 GitHub URL；`DropdownItem` 选 release；`ButtonItem` 安装该版本、卸载
- 卸载用 loader 确认弹窗，不自己做第二套确认，除非 spike 证明调不到 loader

设置行：

- `ToggleField`：定期检查
- `ButtonItem`：立即检查

通知：`toaster.toast({ title, body })`，body 写「Name 可更新到 x.y.z」。点 toast 不自动安装。

手柄：只用自带 Focusable 的组件。不引入 `controllerAwareItem.tsx`，除非现成组件组不出详情行。

## 8. 安全边界

- 不默认 `root`
- 不静默安装
- 不卸载 `GitHub Sideload` 自己
- GitHub URL 只接受 `https://github.com/owner/repo` 及其 `.git` / tree / releases 形式，解析成 `owner/repo`
- zip 必须经 loader 或后续书面批准的安装器处理，插件自己不解任意 zip 到系统目录
- 日志不写 token。当前不做 GitHub token；私有仓库本期不做

## 9. 分期

### P0：骨架已完成

本仓库。ping/emit 可构建。

### P1：安装通道（源码已核实，设备未跑）

loader `frontend/src/index.ts` 在插件 import 之前执行：

```ts
window.DeckyBackend = new WSRouter();
await DeckyBackend.connect();
```

`start.tsx` 再设 `window.DeckyPluginLoader`。插件 bundle 是同一窗口里的 `import(http://127.0.0.1:1337/plugins/.../dist/index.js)`。

`@decky/api` 的 `call` 只打到本插件 Python，不能用来装别的插件。

本仓库适配层：

- 安装：`window.DeckyBackend.call("utilities/install_plugin", artifact, name, version, "", installType)`
- 卸载：`window.DeckyPluginLoader.uninstallPlugin(...)`（官方确认框）
- 启动时 `probeLoaderChannel()`，通道不在就禁用按钮

设备上仍需用一个测试 zip 点一次确认框。

### P2：只读列表

`list_plugins` + 差集展示 + 探测 `package.json.repository`。无安装。

### P3：绑定与检查

手填 GitHub、releases 列表、手动/定时检查、toaster。

### P4：安装、换版本、删除

只在 P1 通过后做。换版本 = 对同一 `name` 再 `install_plugin`，hash 传空字符串（与开发者 zip 安装一致），`InstallType.UPDATE` 或 `DOWNGRADE`。

### P5：从 GitHub 新装

独立 page。无本机记录时 `InstallType.INSTALL`。

## 10. 未决问题

1. 插件 JS 能否调 loader `DeckyBackend`。阻塞 P4/P5。
2. 自定义商店开启时，差集是否仍相对「当前商店」计算。默认是。
3. GitHub API 无 token 的 60 次/小时限制。定时检查默认 6 小时，绑定数量大时再降频。
4. 作者名：当前 `zacharym`，可改。
5. 显示名：当前 `GitHub Sideload`，可改。

## 11. 成功标准（规划阶段）

- 仓库能 `pnpm i && pnpm run build` 产出 `dist/index.js`
- HANDOFF 含用户原文
- 本文写清非商店定义、GitHub 探测范围、安装通道依赖
- 未实现产品功能，未声称已在 Deck 上可用
