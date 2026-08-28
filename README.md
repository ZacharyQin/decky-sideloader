# GitHub Sideload

Decky 插件：管理本机 zip/GitHub 来源的非商店插件，绑定仓库，查询与切换 GitHub Release 版本。

QAM：已安装 / 安装 / 设置。可绑定 GitHub、选 Release、走 Decky 官方确认框安装或卸载、手动或定期查更新。真机未验证。需求原文见 `HANDOFF.md`。

## 构建

需要 Node.js `v16.14+` 与 pnpm `v9`。

```bash
pnpm i
pnpm run build
```

产物是 `dist/index.js`。

## 元数据

| 字段 | 值 |
|---|---|
| 目录 / `package.json` name | `github-sideload` |
| `plugin.json` name | `GitHub Sideload` |
| 作者 | `zacharym` |
| 版本 | `0.0.1` |
| flags | `debug` |

`plugin.json` 的 `name` 是 Decky 菜单显示名。`package.json` 的 `name` 必须全小写加横线。版本只写在 `package.json`，loader 运行时从这里读。

## 官方依据

- 插件结构与 `plugin.json` 字段：https://wiki.deckbrew.xyz/plugin-dev/getting-started
- 环境变量：https://wiki.deckbrew.xyz/plugin-dev/env-vars
- 官方模板：https://github.com/SteamDeckHomebrew/decky-plugin-template
- Loader：https://github.com/SteamDeckHomebrew/decky-loader
