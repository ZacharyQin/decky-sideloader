# Sideloader 测试缺口与补齐进度

## 当前实施进度（2026-10-30）

用户要求本轮直接补齐全部12类，Neo本人实施和验证。**G01–G11的规定本地矩阵和完整组合已通过；G12已有实际网络与Deck隔离后端兼容证据，当前安装插件／Steam界面和远端CI仍未验收。**

| 编号 | 当前状态 | 已验证范围 | 剩余范围 |
| --- | --- | --- | --- |
| G01 | 本地统一通过 | 真实双非空依赖／SHA／合包复验／0755／注释／原包，声明／路径／链接／短体／取消／失败；实际关键词选Beta和第二依赖中断恢复 | 真实宿主另见G12 |
| G02 | 本地统一通过 | 真正非空settings/data全文／清单／SHA／0600；变化／取消／真实EFBIG、失败阻止派发；原子发布和严格复用完整本任务备份、保留原age／bytes | 真实宿主另见G12 |
| G03 | 本地统一通过 | 版本页升级／同版／降级／往返／卸载重装／取消，实际原生类型2/1/3/0和重启恢复type4、真实文件／备份 | 真实宿主另见G12 |
| G04 | 本地统一通过 | 四模式API／主包／依赖，状态和跳转限制、部分失败回退不混字节，headers／正文停滞，持续25秒进度仍在真实60秒总预算失败，进行中route固定 | 实际API受匿名限流，实网资产见G12 |
| G05 | 本地统一通过 | 真正0.6.0→0.6.1候选ELF／bundle／ZIP发现下载替换启动收尾；9组取消／失败／重启／旧前端保护；当前候选已重建 | 实际Loader自更新另见G12 |
| G06 | 本地统一通过 | 八个隔离真实atomic-write EFBIG子case；主包／第二依赖／备份／ready／awaiting／installing／uninstalling／verified结果八处真实SIGKILL；显式继续、原证据复用、迟到不重放 | 不声称穷尽任意指令级故障切点 |
| G07 | 本地统一通过 | 接管／冻结／版本／交还／原冻结恢复、写失败和跨实例迟到、身份变化、binding/map/cache优先级 | 真实宿主另见G12 |
| G08 | 本地统一通过 | 关键词／多插件／多ZIP、真实双依赖、第二依赖失败／截断tree／第二页失败／重启重探查及Rust缓存限额 | 真实服务状态见G12 |
| G09 | 本地统一通过 | 真实手动批量部分失败；实际Rust60秒定时、重挂载仅一次toast、卸载无订阅／权限、关闭通知仍保存；不以frontend RPC代替scheduler | 实际Steam通知见G12 |
| G10 | 本地统一通过 | 实际许可正文SHA，5项Rust分享生命周期；真实私有接口UI导出／QR／复制／HEAD／GET／停止和独立ZIP内容／隐私／权限 | 不等于物理远端LAN客户端验收 |
| G11 | 本地统一通过 | 严格HTTP方法／headers／Range／消费和负控制；有效ZIP单项破坏／CRC／限额；IPC32MiB／UTF8／depth／并发／EOF、18真实桥接及资源回收 | 没有任意源码mutation campaign结论 |
| G12 | 部分实际验收 | 真实Rust四模式固定公开资产均188377B／SHA一致，TLS／URL／跳转未放宽；API均rate_limited；当前最终包在实际Deck/Python3.14.6隔离SDK profile启动原适配器／release ELF，握手／ping／settings／null operation／退出0／pending0／清理通过 | 当前安装插件加载、Steam布局／实体输入／游戏资源性能、远端Actions未验；本轮没有生产安装或提交推送授权 |

**当前完整 `pnpm test:all` 已通过**：242前端、85现行Rust库＋5 IPC（公共服务观察器1项默认ignored且另外显式执行）、18真实桥接、341冻结Python、6许可、typecheck/fmt/两套Clippy、release构建、全60组应用及10成员运行ZIP/CRC/身份/私有标记排除。`test-all-current-status.json` code0；`e2e-final60.json` status=passed、60唯一组。旧38组/各分次/失败full记录只作历史，不再限制当前完成状态。实际定时和慢传总预算各使用真实一分钟；其他UI夹具50ms轮询不构成性能结论。

有红证据的产品修复包括ZIP注释／物理重复项／IPC换行／备份0600／失败下载状态／Release重验／显式恢复ACK，依赖序号及有界进度worker、SHA失败状态，原包不覆盖且拒绝链接／不匹配。备份已原子临时写→完整校验→不覆盖发布，合法本任务完整备份保留原SHA/age复用。第二依赖真实中断的stale downloading及临时目录遗留均已修且分组／全60通过：只按匹配source/operation/count marker、UID/0700、marker0600单链接及已知普通0600单链接成员回收，未知内容保留。八个独立保护变体是一个库测试内矩阵。取消／未知请求／实例关联保护保持。

当前证据根Neo `work/sideloader-close-gaps-z_jfbhw3/`：PLAN、`test-all-current.log/status.json`、`e2e-final60.json`、`final-artifact-identity.json`、`live-services.json`、`real-public-reference.json`、`device-private-current.json`、`ignore-verification.json`、`owned-profile-cleanup.json`。当前包2886561B、SHA `a9aad019328df1dd102f79c15c1b90b9f010e7cb589ec61d786956ecf45e064e`；ELF SHA `867ee4d90094629569311146424be689b2195bc04462bfea24e26fd6e43b1ddc`、requiredGLIBC2.34，实际Deck glibc2.43。三份旧自有超时profile已逐个核验清理，不通配删。没有生产部署、Git历史／远端修改、发布或Loader维护。Node/React模拟宿主及真机隔离SDK profile都不替代当前实际Loader/Steam验收。

## 首次审计记录

审计日期：2026-10-30。以下为开始补齐前的审计快照，保留用于对照；本段当时只审计并修正文档，没有修改产品逻辑或访问设备、真实 GitHub／加速服务。

## 结论与更正

现有测试通过，说明当前已写用例通过，不代表功能已测全。确认有 12 类主要缺口，前 6 类优先补。

此前将“远程依赖下载／合包、备份创建已有现行 Rust HTTP／文件覆盖”概括得过宽，现更正：

- 非空 `remote_binary` 下载、校验、合包分支没有找到当前 Rust 直接用例；相关 13 个 Python 用例属于冻结 0.5 参照。当前 E2E 的两个 bin 文件早已在输入 ZIP 内，不会触发额外依赖下载。
- 当前 Rust 成功安装／重装夹具没有设置非空目标 settings/data，备份只断言文件存在；E2E 覆盖组的非空备份由夹具预先制作。缺少让现行 Rust 真正创建非空用户数据备份并核对内容的正例。
- 应用 E2E 中 Example 的 1.0.0→2.0.0 是预置 ready 记录后的通用覆盖。它没有从版本页开始走 UPDATE、同版本 REINSTALL、DOWNGRADE 或先卸载再安装的完整流程。

旧验收日志、构建身份和通过记录仍保留；其中过宽的覆盖解释由本审计取代。

## 本轮实际核验

- 重跑并观察全部 16 组应用 E2E，结果通过；观察器只在外部记录方法名、次数和原生路由，没有改业务或夹具响应。
- 50 个登记 RPC 中实际尝试了 21 个，另 29 个未出现。这个数字不是覆盖率；出现的 `start_diagnostics_share` 也只是 loopback 被拒绝的负例。
- 原生路由只出现清单读取、设置读取、安装、确认和取消；没有实际卸载路由或冻结／交还链。
- 枚举当前 Rust 43 库＋1 IPC 测试，并检查 1,317 个差分样本的函数分布。样本只有 18 类纯转换／解析函数，800 个是两类脱敏输入，没有网络下载、依赖合包或安装状态流程。
- 前端组件测试通过模拟 API 验证交互，冻结 Python 用例通过 `test-reference.py` 绑定旧实现；两者不能替代当前 Rust 或跨层 E2E。

## 缺口清单

| 编号 | 优先级 | 功能范围 | 已有覆盖 | 还缺什么 |
| --- | --- | --- | --- | --- |
| G01 | 优先补 | 远程依赖下载与合包 | 冻结 Python、前端模拟进度、包内已有 bin 文件的哈希核验 | 当前 Rust 的非空依赖正例；双依赖、坏哈希、短体／中断、取消、路径冲突、source.zip 保留、合包后重新校验和失败不替换 |
| G02 | 优先补 | 非空用户数据备份 | 空数据备份、符号链接拒绝、预置备份保留／篡改检测 | 当前 Rust 实际创建非空 settings/data 备份，核内容／清单／权限；备份中数据变化、写入失败、取消与清理保护，确保失败不派发安装 |
| G03 | 优先补 | 已安装插件的版本动作 | Rust 1→2 准备和重装局部；前端模拟版本选择；ready 后覆盖 E2E | 从版本页开始的升级、同版本重装、降级、往返切换、独立卸载和卸载重装全链；核对应原生动作类型、备份与取消边界 |
| G04 | 优先补 | 加速及下载失败矩阵 | 路由策略、API 限流→代理、坏 JSON 不回退、Range 限制、Rust 下载取消；E2E 直连新装 | 四种模式下 API／主包／依赖实际走路和失败回退；下载短体、中断、超时、错误状态、重定向及换通道不混文件。公共加速服务的真实可用性／速度见 G12 |
| G05 | 优先补 | Sideloader 自更新完整流程 | Rust 准备／取消；已安装 verifying 记录后的进程替换和身份收尾 | 从发现新版本到下载、原生替换、启动新版本程序／前端、收尾的完整 E2E，含失败、取消和中断；不能以同版本预置恢复记录代替真实版本升级 |
| G06 | 优先补 | 失败继续、重试与中断恢复 | 前端模拟；一个实际派发后超时重启 unknown；设置写入失败保旧文件；一般崩溃／父退出 | 当前 Rust 的 `continue_prepared`／`retry_download` 正反例；下载、依赖、备份、卸载、确认、结果保存各阶段中断恢复；安装／归属／自更新记录持久化失败与旧实例迟到结果 |
| G07 | 随后补 | 接管、交还、绑定和映射 | 前端模拟较多；Rust 接管冻结证据、映射与绑定保护局部 | 接管→冻结→版本操作→交还的真实前后端链；交还正例、原生写失败／跨实例、身份变化、绑定／映射更新后的权限与缓存一致性 |
| G08 | 随后补 | 搜索、复杂仓库、发布与缓存 | E2E 手输单插件仓库＋单发布单 ZIP；解析契约和一次 Rust 缓存复用 | 关键词搜索、多插件仓库选择、多 ZIP 选择、探查／分页失败和限额、缓存失效／重启后的完整链；不能把 UI 模拟或纯解析等同于实际请求流程 |
| G09 | 随后补 | 全量检查、定时检查与通知 | 前端模拟报表／通知；设置开关持久化；单插件发布读取 | 当前 Rust 批量检查、多插件部分失败、结果保存；真实定时触发、间隔／忙态跳过、通知一次及取消订阅。E2E 没有调用 `check_updates` |
| G10 | 随后补 | 诊断、分享与许可页面全流程 | 桥接真实导出；Rust listener 的 HEAD／Range／错误 token／配额／替换；E2E 许可索引 | UI→导出→分享→下载→停止完整链；当前 Rust 的过期、接口消失、慢／断开客户与配额竞争、准备中停止；用实际许可数据打开正文而非只看到索引 |
| G11 | 随后补 | 安全边界、协议与夹具质量 | 基础路径／链接拒绝、SHA／身份、非法／截断帧、少量类型错误、3 并发 getter | 当前 Rust ZIP 重复项／冲突／CRC／资源上限等有效恶意包矩阵；IPC 帧／并发上限与失败收尾；更强独立断言、严格 Rust HTTP 消费计数、有总期限的等待。详见下节 |
| G12 | 独立验收 | 真实运行环境 | 本机 Linux＋Node VM／React＋模拟宿主；历史设备证据属于旧构建 | 真实 GitHub／GH-Proxy／GHFast、TLS 与下载对比；真实 Loader 安装后插件能加载运行、磁盘与运行身份不一致；Steam 浏览器布局／输入、设备资源／游戏性能；实际 GitHub Actions 和目标环境兼容性 |

这些是测试缺口，不是已证明的产品故障。G12 与确定性本地回归分开安排，不能由模拟宿主替代，也不以修改 Loader 为前提。

## 断言和夹具的具体弱点

1. 自更新组的“前端控制器确实查询后端”断言统计所有 `get_self_update_status` 调用；测试本身已直接调用两次，即使控制器未调用，`>1` 仍成立。现有身份 getter 检查有效，但该断言不能证明前端参与。
2. Rust 恶意路径／符号链接样本缺少一份其他部分有效的插件包。仅断言任意 `Err`，即使安全检查缺失，仍可能因缺 metadata 等其他原因失败；需要有效基线加单一破坏，并核对拒绝原因和无副作用。
3. Rust runtime HTTP server 对未知请求返回 404，静态 route 可不限次复用；多数用例没有必需消费清单，也未独立检查 method/header。应用 E2E 的严格 HTTP 契约不能自动约束这些 Rust 用例。
4. 桥接取消用例中 `while not self.events` 只给每次 `sleep(0)` 加 timeout，没有给整个等待设置截止时间；回归导致事件不来时可能一直循环至 CI 超时。
5. 现有 9 项 oracle 自检用于验证结果断言，并非产品源码变异测试；不能据此声称任意删去安全检查都会被捕获。

## 关键证据位置

- `backend-rs/src/package.rs`：`remote_binaries` 非空分支及 `backup`、`clean_backups`。
- `backend-rs/src/runtime_tests.rs`：`package_bytes` 没有 remote_binary；`Fixture::new` 没有目标 settings/data；成功安装只检查备份存在；加速只有限流回退等少量样本。
- `backend-rs/src/runtime_more_tests.rs`：三个重装局部用例；恶意包只断言 Err；分享两个 listener 用例。
- `scripts/test-reference.py` 与 `tests/test_remote_binaries.py`：13 个远程依赖用例跑冻结 Python。
- `backend-rs/src/contract_tests.rs`：只分派 18 类纯函数；不包含 remote_binary、backup、网络或业务状态机。
- `e2e/fixture_assets.py`、`e2e/fixtures/install.json`：ready 预置、非空备份手工制作、依赖文件已在 ZIP 中、mode 固定 install。
- `e2e/bridge.py`、`e2e/app.mjs`：只接收 INSTALL／OVERWRITE；自更新记录直接 seeded verifying；native snapshot 从磁盘生成，不实际加载目标插件。
- `tests/ui.test.tsx`：`../src/api` 被 mock；语言、导航、版本、接管等用例是组件层。
- `tests/test_bridge.py`：一个设置文件故障样本和缺少总体截止的等待。

本轮原始证据位于 Neo 工作目录 `work/sideloader-coverage-audit-8u1r9xgg/`：`coverage-inventory.json`、`e2e-observed-rpcs.jsonl`、`e2e-observation-result.json/log`、`current-rust-test-list.log`、函数／用例清单和源文件 SHA 快照。没有生成行／分支覆盖率百分比。
