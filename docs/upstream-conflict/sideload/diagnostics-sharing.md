---
semantic_state: active
last_reviewed: 2026-10-16
origin: 用户任务50，手机或电脑领取当前诊断包
provenance:
  - HANDOFF.md
owners:
  - py_modules/diagnostics_sharing.py
  - src/diagnosticsController.ts
participants:
  - py_modules/diagnostics.py
  - main.py
  - src/api.ts
  - src/types.ts
  - src/SettingsTab.tsx
  - src/DiagnosticsSettings.tsx
  - src/uiIcons.ts
  - src/index.tsx
archived_at: null
archive_reason: null
superseded_by: null
---

# 短时局域网诊断领取

## Requirement

- Requirement identity: 任务50的跨设备领取。
- Business outcome: 用户用同一可信局域网中的手机或电脑扫描本地二维码，领取本次生成的脱敏 ZIP。
- Summary: 仅在用户明确启动时监听所选私有 IPv4 地址。链接为内存凭据，固定时限和次数，到期、停止或卸载后撤销。
- Intent: 提供跨设备交付，同时限制 HTTP 凭据暴露、连接资源和包存储。
- Scope: multi-file。
- Risk level: high，涉及网络、凭据及文件安全。

## Local Semantic Contract

### 网络、请求和资源

- 启动不监听。用户选择当前 Linux 网卡地址，仅接受 RFC1918 的 `10/8`、`172.16/12`、`192.168/16`；网卡必须 UP、RUNNING 且非 loopback。只绑定该 IPv4 和系统分配临时端口，不使用 `0.0.0.0`、公网、link-local、组播或自动换地址。不改变防火墙、路由、代理或 TLS。
- 最多枚举128个网卡；有多个地址时必须通过原生 Dropdown 明确选择，单个有效已选地址使用原生次级文字而非单选框；此前选择失效时保持清空，仅提供显式使用当前地址的原生行，不自动切换接口。没有地址提供连接和刷新说明。网卡或地址失效后最多1秒检测停止，不重新绑定。每个请求在发送前再次核对地址。
- 一次仅一个会话，TTL300秒。后端 `secrets.token_hex(32)` 产生256bit随机凭据，只存在内存；只接受 GET/HEAD 的精确 `/pickup/<token>` 路由，使用 constant-time 比较。不解码路径，不接受 query、目录列表、上传、CORS、cookie 或外部服务。
- 仅 HTTP/1.1，Host 必须精确为所选地址和端口。拒绝重复头、折行、控制字符、无效头名、请求体、非零 Content-Length、Transfer-Encoding、Range、Expect、Upgrade 和其他方法。拒绝响应不回显请求。仅发送固定附件、no-store、nosniff、no-referrer 和 Connection: close 头。
- 请求行最多2048字节，头区最多16384字节、32项。解析总预算5秒，拒绝响应最多额外0.2秒。传输总预算30秒，块最多65536字节。应用请求缓冲最多18432字节，内核发送/接收缓冲请求为64KiB/16KiB，实际内核可调整但每个 socket 有界。
- 自定义异步 socket accept，不使用无界线程池或 ThreadingHTTPServer。backlog4，最多4个已接纳客户端，包括正在读慢请求头的连接；先在内存表登记再运行客户端任务。接纳循环主动让出执行权，饱和连接不能阻止到期检查。
- 同时最多2条 GET 下载；GET 在开始前同步预留，始终满足“成功数＋预留数≤3”。失败释放预留；成功把预留换成成功计数，不加第四次。HEAD不领取或消耗下载名额。`Purpose` 或 `Sec-Purpose` 含 `prefetch` 标记的请求在打开 ZIP、预留名额前返回固定400和空包体，不发送文件、不累计成功数；标记匹配忽略大小写，支持逗号、分号和空白分隔。成功仅代表服务器发送完整字节，不代表浏览器已保存或用户已反馈。
- 停止、到期、次数耗尽、接口失效和文件失效走同一关闭路径：先清凭据、关 listener 和全部客户端 socket，再取消并收齐有限任务。没有后台 daemon worker、额外线程或重绑队列。诊断原导出线程仍负责自己的临时文件清理；准备中停止会保持 draining 门槛至该线程完成，不提前放行新的导出。

### 当前包、互斥与隐私

- 每次明确领取复用原安全导出服务生成一包，不复制 ZIP，不增加三包保留或22/28MiB上限。准备、active、draining 与旧公开 `export_diagnostics` 共用 `Diagnostics.share_phase` 排他门槛；并行导出和替换领取拒绝，不排队。只有停止并排空后可重新开始。
- 只服务本次导出返回的受控名称。目录逐层 no-follow，普通文件、当前 UID、单链接、0600、标记、大小≤6MiB、inode/dev/size/mtime/ctime/UID/linkcount 和 SHA-256 均核对。无客户端路径参数。每次 GET/HEAD 重新打开和完整校验；传输每块及结束核对当前路径和 fd 身份。后台每秒核对文件身份。替换、删除、链接或变更停止领取，外来文件不清理。
- 等待请求时不保留包 fd，只在有界请求内打开，停止时关闭。不会通过复制或保留已删除 inode 扩大产品配额。停止后包仍按原三包轮替，不删除用户产物掩盖容量。
- 服务代码不记录异常文本、请求、头、IP、令牌或 URL。新增 RPC 不纳入逐载荷日志。原日志白名单也不接受这些字段。URL、QR、session 仅在运行中状态和前端内存，终止响应不含链接；插件重载为 idle，不恢复领取。

### 协议和前端

| RPC | 请求 | 响应及行为 |
| --- | --- | --- |
| `get_diagnostics_addresses` | 无 | `{addresses:string[]}`，最多128个不重复 RFC1918 IPv4 |
| `get_diagnostics_share` | 无 | 只读 `DiagnosticShare`，不生成包或启动服务 |
| `start_diagnostics_share` | `address, session, previous` | session为32位小写hex；previous为上次已读session或null，CAS检查，返回同一session；忙时拒绝 |
| `stop_diagnostics_share` | `session` | 只停止匹配会话，幂等；旧session不能停止较新会话 |

- `DiagnosticShare` 仅有 state，加非idle时的session和可选package。状态为 idle、preparing、active、draining、stopped、expired、exhausted、error。active额外含严格私有HTTP URL、0至300剩余秒和0至3成功数，且必须含package；error额外含≤512字节固定错误。package严格复用原 `DiagnosticStatus.success`，不得夹带其他字段。前端验证键、数值、路径、token长度、地址和请求相关性。
- 相同session重复调用只读原结果，不重放；创建新会话要求previous等于后端当前session，避免较旧启动RPC在新会话结束后再次启动。无需无限会话历史表或持久化令牌。
- 控制器拥有唯一启动Promise、session、状态轮询和generation/revision。QAM组件重挂只读取共享状态，不启动、不导出。停止可覆盖准备中的启动；迟到成功、初始读取和轮询不能恢复已停止链接。启动或停止未确认时，先显式刷新/停止，不能直接创建新意图。
- 地址选择草稿保存在共享诊断控制器内存，原生下拉框卸载 QAM 后，旧弹窗回调仍可更新重挂页面。回调核对插件 generation、当前 session、地址集合 revision、最新选择和当前可用地址；旧生命周期、旧会话、地址变更或较新选择使旧回调失效。已选地址失效时清空，不自动改选其他地址；选择本身不导出、不启动领取。
- 单个领取状态定时器每秒查询，只读轮询最多361次；失败停止轮询，隐藏链接并提供刷新/停止。插件卸载清定时器和前端凭据，并尝试停止已知session；后端卸载在安装流程等其他工作之前撤销服务。
- 设置仅一个“问题反馈 / 诊断日志”原生入口，点击进入独立内页；B/返回回设置并恢复该行一次焦点，页面跨QAM保留。进入只读初始化，不生成或监听。内页主按钮“生成下载二维码”，下方“刷新状态”，状态/错误/地址/复制/停止均使用下载称谓。
- 页面默认显示短“诊断包已准备”及KiB/MiB。完整文件名与路径默认隐藏，原生“文件详情”行展开。QR、链接、剩余时间、复制和停止为当前会话操作。未加密HTTP和持有链接即可领取的风险，在开始前用原生次级说明呈现。
- 唯一新增 runtime 依赖 `qrcode-generator@1.4.4`，MIT、无运行时子依赖。使用本地矩阵生成装饰性 SVG，不使用字符串HTML注入、第三方二维码/上传服务。二维码内联指定 width=100%、height=auto、display=block，以容器宽度等比显示，并以18em限制最大宽度，避免原生普通SVG图标尺寸将其压成16px。黑白和四模块边界用于扫码对比度；其他UI保持原生组件、单焦点SVG和既有返回焦点。
- 复制仅在用户操作时 feature-detect navigator.clipboard.writeText，失败或缺能力不称成功，无DOM降级或残留元素。迟到复制结果受当前链接/组件生命周期保护。不清理检查或设置错误，不写网络/通知/自动检查偏好。

### Owner role

- `py_modules/diagnostics_sharing.py` 拥有会话、身份核验、HTTP解析、资源上限与关闭路径。
- `src/diagnosticsController.ts` 拥有单意图、地址选择草稿、状态轮询、竞态和内存凭据生命周期。

### Participant roles

- `py_modules/diagnostics.py` 提供原导出与共享排他门槛，保留容量契约。
- `main.py` 提供领取RPC，卸载先停止领取。
- `src/api.ts` 校验严格DTO、地址、凭据格式和会话相关性。
- `src/types.ts` 定义有界领取DTO。
- `src/SettingsTab.tsx` 提供唯一显式内页入口。
- `src/DiagnosticsSettings.tsx` 呈现原生操作、短状态、本地QR和可展开详情。
- `src/uiIcons.ts` 提供装饰性操作图标。
- `src/index.tsx` 在插件生命周期绑定领取控制器，不在QAM挂载中重放。

## Implementation Freedom

- 可等价替换socket与QR内部实现，必须保持资源限额、纯本地生成、单意图和先撤销后等待。
- Workaround semantics that may be retired: none。
- 不将文件夹打开或设备端复制当作跨设备领取替代。

## Non-Break Constraints

- 保留 [有界诊断](diagnostics.md) 的日志4MiB、ZIP/展开6MiB、三包、22/28MiB、安全发布与脱敏。
- 保留 [插件管理](plugin-management.md) 的接管政策、安装确认链、原错误、ZIP/备份和持久化顺序。
- 不自动上传、部署、启动监听、操作原生设置或改变全局网络。

## Coupling

- `tests/test_diagnostics_sharing.py`：loopback临时端口和TemporaryDirectory；恶意请求、慢头/读者、入场/下载预留、HEAD、预取早拒绝且保留三次正式领取、字节/哈希/权限、生命周期、接口/文件失效、链接/替换、取消/互斥、FD/任务回收、意图CAS。
- `tests/diagnostics.test.ts`：严格协议、初始化/轮询/停止竞态、单意图、timer清理。
- `tests/ui.test.tsx`：短状态、默认隐藏详情、本地SVG、QAM、接口选择及旧原生下拉框回调跨重挂保留、较新选择/地址失效/会话变化/插件卸载后的旧回调隔离、缺LAN、停止准备、剪贴板失败/迟到及原错误隔离；诊断内页只有点击才进入、B返回一次焦点、重挂不生成和下载称谓。
- `package.json`、`pnpm-lock.yaml`：仅一个本地QR runtime依赖，构建需打入bundle。
- `README.md`、`HANDOFF.md`：领取和真机验收边界。

## Validation

- `pnpm test`、`pnpm test:python`，回归。
- `pnpm typecheck`、`pnpm build`，类型和bundle。
- `git diff --check`、`git diff --cached --exit-code`，差异和无暂存。
- Enforcement gap: 没有专用语义validator，角色/路径/index手工核对。模拟测试不代表真机网卡发现、手机扫码/保存、窄屏和物理手柄通过。HTTP没有加密，LAN旁观者仍可能取得 bearer；不提供跨网络或公网领取。
