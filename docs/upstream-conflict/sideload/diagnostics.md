---
semantic_state: active
last_reviewed: 2026-10-16
origin: 用户任务47，有界可观测性日志和一键导出
provenance:
  - HANDOFF.md
owners:
  - py_modules/diagnostics.py
  - src/diagnosticsController.ts
participants:
  - main.py
  - py_modules/install_flow.py
  - src/api.ts
  - src/types.ts
  - src/loaderBridge.ts
  - src/DiagnosticsSettings.tsx
  - src/SettingsTab.tsx
  - src/uiIcons.ts
  - src/index.tsx
archived_at: null
archive_reason: null
superseded_by: null
---

# 有界本地诊断与导出

## Requirement

- Requirement identity: 任务47。
- Business outcome: 用户从设置入口进入诊断内页导出容量有限的本插件诊断包，自行附到问题反馈。
- Summary: 记录阶段、固定类型和计数，不保留原始载荷。日志和导出包均有固定字节及数量上限，不自动上传。
- Intent: 调查来源读取、网络与安装阶段问题，同时避免泄露凭据、无限增长和重复业务操作。
- Scope: multi-file。
- Risk level: high，涉及隐私、文件删除和安装事务隔离。

## Local Semantic Contract

### 记录与隐私

- 日志位于 `DECKY_PLUGIN_LOG_DIR/diagnostics`，UTF-8 JSONL。含格式和换行的单条最多 8192 字节。活动文件和三个历史文件各最多 1048576 字节，总共最多 4194304 字节；写入前轮转，不依赖重启。
- 事件名、RPC 名、阶段、状态、来源、实际 route、mode、异常类型及模块均采用固定白名单。新增cancel_operation和dismiss_operation也只记录固定RPC名/受控异常类型及耗时，不能记录任务载荷或自由错误。数值限为非负 32 位整数，任务 ID 限为 32 位小写十六进制。未知字段或字符串丢弃，不调用任意对象的 `str/repr`，不记录自由文本错误。
- 不记录 URL、query、userinfo、headers、cookie、Authorization、令牌、密码、私钥、本机 home 路径、配置、绑定表、进程参数、下载内容或备份。SDK 日志只记录本插件可识别模块、行号和级别，不格式化 message/args/traceback。
- 在本插件隔离 Python 进程内，只移除并关闭 `baseFilename` 与 SDK `DECKY_PLUGIN_LOG` 精确匹配的 `FileHandler`，添加同一个有界白名单 sink。其他 handler 保持不变，不调用 `logging.basicConfig`，不删除旧 SDK timestamp 文件，不修改 Loader 或 journald，也不把旧 SDK 文件打包。
- 启动/停止、关键 RPC 起止/失败与耗时、列表来源/数量、手动/定时检查摘要、真实网络尝试、持久化后的安装阶段和下载状态变化均可观测。`get_operation` 仅失败时记录固定 RPC 名、受控异常类型与耗时，涵盖状态读取/解析、核验结果、关联和最终成功结果的保存失败；原异常原样抛出，记录器失败不能替换原异常。成功轮询、每个下载块和诊断写入 RPC 本身不逐次记录。
- `InstallFlow._update` 仍先保存候选，再更新内存，最后调用异常隔离的 observer。安装成功仍依赖原有证据、关联及成功结果持久化；candidate、下载完成或文件匹配不能单独冒称成功。诊断导出只读取已有内存任务，不构造、恢复或提交安装任务。
- 前端只观测 `loader/get_plugins` 的成功/失败、数量和耗时，不改变读取结果、异常或安装通道。队列最多 32 条，单次最多 16 条、8192 字节；每字段为固定类型和有限数值。丢弃计数饱和于 65535，失败批次不重发，计数随下一批发送，不递归记录诊断失败。

### 导出、容量与文件安全

- `export_diagnostics()` 不接收路径，输出到 `DECKY_USER_HOME/Downloads/GitHub-Sideload-Diagnostics`，不硬编码用户名，不回退到 `/root` 或程序目录，不读取环境或执行 XDG shell 配置。
- 专属目录 `0700`，日志和 ZIP `0600`。通过目录描述符逐层打开路径，拒绝符号链接。只处理当前用户拥有、普通且单链接的文件；不跟随 hardlink，不递归扫描或删除目录。
- 活动/历史文件使用固定名称和格式标记；导出包使用带时间及 UUID 的受控名称和内容前缀，临时包使用 `building-<uuid>.tmp` 和同一前缀。不采用索引。外来文件即使名称相似，只要不能确认格式/归属，就保留；链接文件保留并拒绝或忽略。
- 日志快照在轮转锁内读取，各文件读取不超过 1 MiB，单次读行有上限。历史行再次应用白名单；损坏行只累加有界统计，不把原文放入 ZIP。超长物理行在同一剩余字节预算内持续丢弃至换行或 EOF，只计一次坏行；其尾部不得被解析成独立事件，后续独立合法行仍保留。
- 完整包包含有限 JSONL、版本/网络 mode/来源及管理健康计数/任务 stage 和 ID 的 `summary.json`，以及容量限制和各成员字节数、SHA-256 的 `manifest.json`。不导出原 operation/config、全目录或全系统日志。
- ZIP 使用存储模式，包本身和全部展开成员均不超过 6 MiB；构建前为格式元数据预留 64 KiB，不靠压缩隐藏无界数据。
- 单个导出 worker，重复调用忙时拒绝。保留最多 3 个 6 MiB 完整包，构建中最多 1 个 6 MiB 临时包；本插件新增数据稳态最多 22 MiB，瞬时最多 28 MiB。外来文件不计入本插件配额，也不因配额清理。
- 临时包独占创建，异常时按本次创建 inode 清理，即使在写格式标记前失败也不会留下空文件。启动和导出前只清理可确认归属的历史临时包/超大包，保留未知内容。目录检查最多 512 项，超过即安全失败。
- 完成包用平台独占原子 rename 发布，Linux `renameat2(RENAME_NOREPLACE)`，本地 macOS 验证用 `renameatx_np(RENAME_EXCL)`。不覆盖已有文件，不产生临时 hardlink；平台无该能力时导出安全失败。正常卸载设置停止标记并等待 worker 退出、清理。

### 协议与设置页

| RPC | 请求 | 严格响应 |
| --- | --- | --- |
| `record_diagnostic_events` | `events` 至多16个 `{event:"loader_read", ok:boolean, count:int, durationMs:int}`，`dropped` 为0至65535整数 | `{accepted:int}`，必须等于提交数 |
| `export_diagnostics` | 无 | `DiagnosticStatus`，忙时拒绝，不排队 |
| `get_diagnostics_status` | 无 | `DiagnosticStatus`，只读、不重新导出 |

`DiagnosticStatus` 只允许 `{state:"idle"|"exporting"}`、`{state:"error",error:string}` 或 `{state:"success",file:string,path:string,bytes:int}`。前端校验键、文件名、绝对路径、4096字节路径上限、512字节错误上限和6MiB包大小，不接受额外载荷。

- `src/diagnosticsController.ts` 拥有共享导出 Promise 和状态，QAM 重挂不会发起新导出。初次状态读取不得覆盖更新的用户导出意图；插件重载只查询状态，不重放导出。兼容旧导出后台忙状态最多查询61次，单个定时器，生命周期结束清理全部新增定时器，迟到结果按 generation 忽略。
- 诊断内页的当前下载交互见 [短时局域网领取契约](diagnostics-sharing.md)：主按钮“生成下载二维码”，下方“刷新状态”；短状态和人类可读大小，文件名/路径默认隐藏于详情，本地QR和显式复制下载链接/停止下载服务。用户可见状态及错误使用下载称谓。旧导出RPC和共享导出控制器保持兼容；共享准备、活动、排空时旧公开导出被同一后端门槛拒绝。
- 提示日志4MiB、每包6MiB、最多保留3包、旧包轮替和不会自动发送。不修改检查/通知/网络偏好，不清除其他设置或检查错误，不增加帮助浮层、配置控件或装饰焦点。

### Owner role

- `py_modules/diagnostics.py` 控制白名单、写入、快照、轮转、SDK 文件 sink 和安全导出。
- `src/diagnosticsController.ts` 控制有界观测队列、导出意图、QAM共享状态和定时器清理。

### Participant roles

- `main.py` 接入启动/停止、RPC、网络与检查观测以及三个诊断 RPC。
- `py_modules/install_flow.py` 仅在状态实际持久化后观测变化，隔离 observer 异常。
- `src/api.ts` 集中 RPC 调用及严格响应校验。
- `src/types.ts` 定义固定诊断事件和状态 DTO。
- `src/loaderBridge.ts` 只观测管理读取的结果计数和耗时。
- `src/DiagnosticsSettings.tsx` 使用原生操作和短状态呈现诊断，详情默认收起；领取交互由共享契约约束。
- `src/SettingsTab.tsx` 仅提供原生诊断内页入口，点击才进入；B/返回恢复入口一次焦点，随后FIRST正常。
- `src/uiIcons.ts` 提供装饰性导出图标。
- `src/index.tsx` 在插件生命周期启动和清理诊断模块，并渲染显式诊断内页；进入和QAM重挂不生成、不监听、不重放。

## Implementation Freedom

- 白名单字段和模块内辅助函数可调整，但不能引入自由载荷、凭据、任意路径或额外业务操作。
- 文件实现可等价替换，必须保持真实 UTF-8 字节上限、独占发布、外来文件保留和取消清理。
- Workaround semantics that may be retired: none。

## Non-Break Constraints

- 不改变 [插件管理契约](plugin-management.md) 的 Loader SSOT、列表自身图标、持久配置 schema、网络预算、安装确认/取消/unknown不重放、备份和成功证据。
- 日志失效不改变 API 返回/异常、报告、绑定或任务结果；日志失败不进入业务持久化事务。
- 不删除安装包、备份、其他插件文件或用户外来文件，不通过系统 Trash 绕过配额。
- 不修改全局日志系统，不自动上报。日志/ZIP本身不添加依赖；局域网领取仅使用共享契约允许的本地QR依赖。

## Coupling

- `tests/test_diagnostics.py` 覆盖字节/轮转、合成 canary、巨型行及伪合法尾部、历史过滤、完整包/manifest/权限/容量、单worker、取消/失败/重启清理、未知文件/链接、SDK exact handler、成功轮询无日志及读取/持久化失败的诊断与事务隔离。
- `tests/diagnostics.test.ts` 覆盖队列、批次、drop、协议校验、失败不重试、定时器清理和初始状态竞态。
- `tests/ui.test.tsx` 覆盖原生 busy/重复点击/QAM不重放/失败重试，以及设置/检查错误和偏好隔离。
- `tests/test_install_flow.py`、`tests/test_management.py`、`tests/loaderBridge.test.ts`、`tests/apiFacade.test.ts` 保持既有安装/报告/管理语义。
- `README.md`、`HANDOFF.md` 记录用户领取方式、本地交付和设备验收边界。
- 合流时同步这些 source index、DTO 和指南角色，不以诊断改变安装裁决。

## Validation

- `pnpm test`，前端逻辑和原生组件模拟回归。
- `pnpm test:python`，后端边界与既有事务回归，全部新日志/Downloads夹具均在 TemporaryDirectory。
- `pnpm typecheck`、`pnpm build`，类型与产物。
- `git diff --check`、`git diff --cached --exit-code`，差异和无暂存。
- Enforcement gap: 仓库没有专用 semantic validator，角色、路径与单个 source index 需手工核对。模拟组件及本机文件系统测试不能替代设备路径、权限、SDK handler 和真实 QAM 验收。
