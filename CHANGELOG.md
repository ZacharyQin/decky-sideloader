# Changelog / 更新记录

This file records reviewable source versions. A version commit does not imply a release or deployment; `package.json` is the version source of truth. Every entry must include English first, followed by Chinese. GitHub Release notes must contain the complete matching version section from this file, not an automatically generated commit summary or the full changelog. Publishing requires explicit approval.

本文件记录可审核的源码版本。版本提交不代表发布或已部署，版本号以 `package.json` 为准。每条记录必须英语在前、中文在后；GitHub Release 正文须使用本文件对应版本的完整章节，不以自动生成的提交摘要或整份日志代替。发布须获明确授权。

## [0.4.0] - 2026-10-27

### English

- Show package warnings in a native confirmation dialog as soon as preparation completes. Continuing opens Decky's installation confirmation; cancellation keeps the prepared task available for an explicit retry. Preserve uninstall-and-reinstall confirmation and do not replay a pending dialog after reload.
- Bundle a maintained, independently versioned `plugin.json.name` to GitHub repository map for offline first-use recognition. Preserve explicit bindings, completed-install links, takeover repositories, stopped tracking and identity/ownership guards; fall back to package metadata when an entry is absent.
- Add a six-language Repository map section in Settings with revision/count/date and a separate update action using the selected network mode. Validate and atomically replace complete snapshots, retain the last good version on failure, prevent downgrades and changes during installation/release checks, and drain updates on reload. Validate maintained data and revision changes in CI and include the source-commit snapshot in runtime ZIPs.

### 中文

- 安装包准备完成后，以原生确认弹窗展示安装警告；继续后进入 Decky 安装确认，取消则保留已准备任务供用户主动重试。保留卸载并安装的独立确认，重载后不自动重弹。
- 随包附带独立版本的 `plugin.json.name` → GitHub 仓库映射表，首次使用离线识别；保留手动关联、成功安装确认的关联、接管仓库、停止追踪选择及身份／归属保护，未收录条目继续使用包元数据等现有来源。
- 设置新增六语“仓库映射表”区段，展示修订号、条目数、日期并单独更新，复用所选网络模式；完整校验后原子替换整表，失败保留旧版、防止降级，安装／版本检查期间不切换，重载时排空更新任务。CI 校验数据与修订号变更，运行 ZIP 附带源提交中的映射快照。

## [0.3.1] - 2026-10-27

### English

- Separate Return to store and Re-identify plugins into distinct maintenance sections with the existing section spacing.
- Make B on the QAM home page return to Decky's plugin list. Preserve hierarchical Back/B navigation on inner pages.
- Show a full-width refresh button with its text when plugin details have no release-update timestamp; keep the compact icon beside an existing timestamp or primary version action.
- Hide Skip this version and Undo skip when the selected release is already installed, without changing saved skip rules.
- Display the plugin as Sideloader in Decky, the panel title, notifications and self-protection messages. Keep the repository, package, installation directory and settings/data namespaces named `decky-sideloader`; update API identity, frontend error-source matching and six-language installation instructions for the new display name.

### 中文

- 将“交还商店”和“重新识别插件”拆为独立维护区段，沿用现有区段间距。
- QAM首页按B返回Decky插件列表；内页仍按原层级返回。
- 插件详情没有版本更新时间时，刷新按钮整行展示图标与文字；有时间或主要版本操作时保留紧凑图标按钮。
- 所选版本已安装时隐藏“跳过此版本／取消跳过”，不更改已保存的跳过规则。
- Decky插件列表、面板标题、通知及自身保护提示显示为Sideloader；仓库、包、安装目录与settings/data命名空间仍为`decky-sideloader`。同步API身份、前端错误来源匹配及六语安装入口说明。

## [0.3.0] - 2026-10-23

### English

- Rename the project and Decky plugin to `decky-sideloader`, including the Loader/API identity, scoped frontend styles and storage keys, diagnostic paths, package names, workflow artifacts and all six READMEs. Keep the existing version and plugin behavior.
- Apply the full readability review to all six READMEs: separate updating an installed plugin from installing a new one; keep feature summaries short and centralize version/maintenance instructions; group installation checks, cancellation, backup scope/location and cleanup; show capacity limits in a separate table; move connection and diagnostic guides into Usage and settings, with diagnostics grouped by download, sharing, included information and technical contents; give source builds a standalone developer section. Link package acquisition to the actual build instructions and `dist/` output instead of inventing a public download address. Keep internal storage-budget arithmetic out of the user guide.
- Move the project's original installation and update background out of the header into a dedicated Why decky-sideloader section after What you can do and before Install. Add a contents link in all six READMEs and keep the header focused on the product introduction; preserve the background about cross-mode ZIP installation, discovering updates through videos or group chats, and inconvenient version switching.
- Place the gallery's home-screen image after the contents and before What you can do. Keep all five feature titles and descriptions visible, with only screenshots in default-collapsed details/summary blocks, using tables for multi-image flows. Add compressed WebP screenshots for all six languages, captured from the real localized UI where needed; lazy-load images at 240px for single views and 180px per table image, and include the shared assets in runtime ZIPs for offline reading.
- Collect device information only for diagnostic exports: obtain the running Steam client version from the native API with a bounded wait, and read OS/build/kernel/architecture plus non-unique DMI vendor/model fields in the ZIP worker. Identify Steam Deck LCD/OLED from Valve Jupiter/Galileo hardware, not SteamOS alone; leave unavailable identities unknown, exclude unique identifiers, and never copy device metadata into routine logs or collect it on status refresh.
- Expand bounded diagnostic bundles with classified error codes and controlled source locations, session/request/parent/task correlation, independent dependency events, management/freeze snapshots, ownership transitions, operational settings and backup-cleanup counts. Treat healthy cached catalogs separately from Loader failures. Read existing persisted tasks without recovery or network activity; omit original messages, URLs, paths, credentials and user files, using pseudonymous target identifiers.
- Report independently calculated frontend/backend build fingerprints and the actual loaded frontend version; verify installed bundle metadata and refresh it on export so frontend-only replacements remain visible. Record plugin-scoped page/action/JS/render errors without swallowing the host's error, drain queued events before export with a bounded wait, and retain existing log/package size and sharing limits.
- Split the Python entry by feature under `backend/`: retain lifecycle handling in `backend/main.py` and move settings, plugin management, releases, installation and diagnostics into `backend/sideload/`. Package the Loader entry as `main.py` and modules as `py_modules/sideload/`; migrate consumers without legacy import shims.
- Add `pnpm package-plugin` and a GitHub build workflow for pull requests, `main` pushes and version tags. Validate, build and upload the runtime ZIP; matching version tags publish it with the complete English/Chinese changelog section, rejecting tag/package version mismatches.
- Automatically remove backup archives older than seven days at startup, hourly and before new installation preparation. Add a confirmed Delete data backups action in Settings, including recent backups while protecting active/unknown tasks and running workers. Preserve original data, packages and task evidence, show deletion status in backup details, and report partial cleanup failures across all six languages.
- Rewrite the user-facing README in the Power Timeline layout, keeping English at the repository root and complete Simplified Chinese, Portuguese, Spanish, Japanese and Korean translations under `docs/readme/`. Include six-language navigation, installation and first-use instructions, backup boundaries, language settings and executable source-packaging commands; do not restore the removed design documents.
- Remove the standalone design prototype (`demo/`), unused empty `defaults/` directory and supplemental `docs/`. Remove obsolete source documentation indexes and README links; production behavior and translations are unchanged.
- Bring the approved demo's page hierarchy into the plugin: focus the home page on plugins and updates, with Install and Settings scrolling above the list. Split details into Overview, Other versions and Maintenance; move store plugins and source re-identification into Settings. Preserve installation, takeover, backup, cancellation and diagnostics.
- Unify new-install repository URLs and keyword search in one native input. Place the search button on its right with 16px outer insets and an 8px gap; Enter submits through the same path, without triggering on IME confirmation or repeated keys. Replace repository dropdown results with home-style rows that inspect the selected repository immediately. Keep drafts, results and return focus across navigation, stale-response guards, explicit monorepo selection and installation confirmation.
- Dismiss the native keyboard after Enter submits. Return from installation previews and search-origin installed details to the retained search results with either Back or B. Open the task page as soon as preparation starts, then show real download bytes, percentages and stages; preserve the preview/search return path and native installation confirmation.
- Add a third-party code/source warning above Continue installation in new-install previews. When the selected project's manifest declares root access, also show “This plugin has full access to your Steam Deck.” Keep it out of installed-plugin details, resolve monorepo candidates independently and cover all six UI languages. Reuse repository discovery without extra requests; retain actual ZIP checks without adding a checkbox or confirmation dialog.
- Prepare declared remote binaries before native installation using the task's existing network mode and deadline, verify SHA-256, and embed them in a rechecked local ZIP while retaining the source archive. Show separate main-package/dependency progress, native stages and explicit failures. Require correlated Loader start/finish evidence plus all installed-file checks before success; remove timeout-based false success and revoke old success records lacking completion evidence.
- Replace the task's phase list with compact connected dots: show all eight installation steps, activate the current and preceding dots, and show only the current step's label below. Add an uninstall step for explicit uninstall-and-install. Derive the position from actual task evidence; failures, cancellation, manual closure and unknown results never light later steps. Preserve download progress, errors, backup details and actions.
- Refresh the installed list and selected details after successful installation, including QAM remounts, so Back/B uses the current version and update state. Discard list reads started before completion instead of letting them satisfy the refresh.
- Observe completion of native standalone uninstall, refresh the local plugin list, and leave removed-plugin pages for Home instead of retaining stale identity errors across QAM remounts. Preserve native confirmation, cancelled/failed operations, newer selections and the intermediate removal phase of reinstall.
- Determine native store management from the current Loader registry and update capability in its configured Default, Testing or Custom channel. Historical store-name matches no longer force takeover of an unregistered plugin. Share the same native observation between backend authorization and UI, retain explicit ownership/freeze guards, cache update capability per channel and refresh it only explicitly; unknown capability does not grant installation permission.
- Place Dismiss notice and Don't remind again side by side for terminal tasks. Dismiss only the exact task; Don't remind again first saves the task-notice preference and then dismisses that task. Apply the saved preference on every page, hide banners until it is loaded, and prevent late reads from overriding a newer choice. Keep task evidence and the recent-task entry, preserve save-failure visibility, and allow re-enabling notices in Settings.
- Add a native-styled offline license reader under Settings, with grouped components, collapsed transitive dependencies, versions, attribution, source links and full license documents. Generate notices from modules actually shipped in the bundle, including Font Awesome and embedded-source notices; distinguish host-provided Decky UI. Preserve document selection and expansion across QAM remounts and restore focus on hierarchical Back/B navigation.
- Add Simplified Chinese, English, Portuguese, Spanish, Japanese and Korean. The native language dropdown shows SVG flags and language names in their own languages. Follow the Steam/browser language automatically or persist a manual choice independently; switching languages preserves drafts, version selections, tasks and confirmation checkboxes.
- Use the actual 300px QAM container with 16px content insets and the native panel background; remove the custom gray background and fixed bottom bar. Home rows have 12px vertical and 16px horizontal padding; navigation has a 44px minimum height and 8px vertical padding, expanding for long text. Scope Field overrides to prevent native 10px vertical padding from taking precedence.
- Correct differences between the first plugin UI and the demo: headings, group counts, icon columns, version summaries and explicit version lists, maintenance navigation, four network choices and expandable backup details. Return to Overview after selecting a version. Remove duplicate spacing caused by differences between Decky's section styles and similarly named Steam classes, leaving 268px of content width; preserve native controls, confirmations, persistence and user themes.
- Keep the home update-check button compact and rectangular with a clear focus contrast, scoped to this plugin so global theme rounding and focus rings do not override it. Align Install and Settings with the content, with an 8px gap between them and 24px before the list, without stacking margins.
- Unify horizontal insets across all pages: remove home/settings-only layout branches and section-level horizontal padding. Lists, navigation, forms, lightweight actions and expandable rows use their own 16px padding, full 300px backgrounds and zero horizontal margins instead of negative form margins. Align headings, descriptions, release bodies and non-full-row buttons separately; include native input labels, fields and descriptions so focus does not shift their position.
- Fix the missed parent section around task notices: the View task and Dismiss task notice rows on Home and Settings span 300px with zero horizontal margins and 16px padding. Keep content insets for the home heading, update check and button pair without narrowing task backgrounds through the whole overview section. Preserve full-row focus and internal insets for Back, repository mode, release notes, version skipping and expandable rows. Remove the blue outline and duplicate native focus ring from network radio choices; cover native form controls such as the download timeout.
- Fix the list still showing Not checked for updates after reading versions in plugin details. Installed-plugin details reuse the backend check and save the individual result, including after returning to the list or a late response. Failures do not masquerade as Up to date; preserve other plugin results, scheduled history and the full-check cycle.
- Stop repeated network requests when reopening details, remounting QAM or switching between Overview, Versions and Maintenance. Share complete successful Release snapshots with full update checks, merge in-flight reads for the same identity and replace successful results only on explicit refresh. On failure, retain the previous content and timestamp alongside the error; do not invent a fallback without a successful record. Persist bounded snapshots separately, restore them after reload and invalidate them on identity changes, without changing version comparison, installation authorization or network preferences.
- Show download totals for the selected release in plugin details and new-install previews, updating when the version changes. Sum GitHub's uploaded-asset download counts; show zero for an empty asset list and Unknown for missing statistics or older snapshots. Reuse the existing cached release reads and refresh behavior, without extra requests on navigation. Group package names and download totals as aligned release metadata with matching 12px text and icons, without explanatory subtext; all six UI languages are covered.
- Move the detail update action above the release summary, with an icon-only refresh button on its right. Both use the same native primary-button appearance and height. Place Skip this version / Undo skip last, using the same full-width navigation row as Choose another version and Maintain this plugin. Preserve loading, disabled states and installation guards.
- Render release notes as GitHub-flavored Markdown in both the preview and a separate full-notes page, including headings, lists, code, tables and images. Full release notes uses the same native navigation row as Choose another version and Maintain this plugin, rather than expanding inline. Preserve the selected release and install draft across navigation and QAM remounts, return focus to the entry, and provide native paragraph reading stops. Sanitize embedded HTML and URLs; reuse the cached body without another Release request.
- Keep a compact Back row on every inner page, naming its actual destination. Use 8px vertical padding and automatic height rather than a fixed-height bar; remove the extra top gap. Preserve native B-button navigation and return focus, and keep shared page headings out of the focus order without changing their appearance or body reading stops. Keep the native ScrollPanel responsible for left-stick scrolling. When no primary version action is shown, place Refresh at the far right of the last-updated row; keep it beside the primary action otherwise. Move the skipped-version notice beneath the plugin heading and size it to its content.
- Remove the gap between Full release notes and Choose another version. Distinguish Up to date from Latest release skipped in the home list using matching cached releases and skip rules; without a matching snapshot, only claim Releases skipped. Keep errors and other available updates visible, without extra network requests.
- Treat Skip / Undo skip as local filtering, not a Release cache identity change. Recompute the affected update result from complete cached releases and saved package-version evidence without Release, ZIP or Git requests. Preserve the selected release/asset, successful timestamps, scheduled history and existing refresh errors; an in-flight check uses the latest skip rules before publishing.
- Correct the five-second query timeout boundary: JSON/raw, ZIP Range and official store queries wait at most five seconds for complete response headers, then time out only after five consecutive seconds without body progress. Continuous reception may exceed five seconds; HTTP 200 responses are no longer cut off because total elapsed time reaches five seconds. Timeouts still select fallback routes according to the existing network mode, with body-size, length and parsing checks unchanged. Full downloads retain 60-second connection/header and no-body-data waits plus the existing 3–30-minute total package deadline; cache identity rules and skipped versions remain unchanged.
- Add a Language / 语言 entry with a language icon at the bottom of both Home and Settings, sharing the same native dropdown and preference. It scrolls with the content rather than adding a fixed footer; changing either entry updates the other. Keep language self-names and flags so users can recover from an unfamiliar language. Render flags as separate SVG images to isolate them from the native menu's forced white fill on focused items, without changing native highlighting or language persistence.
- Align icon columns and content insets across plugin details, loading notices, repository search results and task pages; remove the native icon container's 10px right padding. Make Choose another version and Maintain this plugin two consecutive compact detail rows. Reduce Settings navigation icons to 16px; home plugin rows and settings navigation span 300px with zero horizontal margins and 16px horizontal padding aligned to group headings, without shifting on focus or pushing icons against the edge.
- Show the full service address and trust risks before switching to a third-party connection mode, and require explicit consent before saving. Cancelling retains the previous mode; active tasks do not change routes.
- Preserve original Release bodies, repositories/paths, backend errors and historical evidence rather than inventing translations. Not pushed or published.

### 中文

- 项目与Decky插件统一改名为`decky-sideloader`，同步Loader／API标识、前端作用域样式与存储键、诊断路径、安装包名称、workflow产物及六语README；保留当前版本和既有功能行为。
- 六语README全量落实易读性审查：首次使用拆为更新已有插件与安装新插件；功能概览只讲能力，版本与维护细则集中到使用说明；安装确认、取消、备份范围与位置、清理规则分别分组，容量限制独立成表；连接与诊断归入“使用与设置”，诊断按下载、分享、采集信息及技术内容分组；源码构建独立到开发者信息。获取安装包链接到实际构建步骤及`dist/`产物，不虚构公开下载地址；内部存储预算计算不放入用户说明。
- 六语README将两段原始背景移至“可以做什么”之后、“安装”之前新增的“为什么做decky-sideloader”一节，并加入目录；头部只保留产品介绍。保留跨模式下载与寻找ZIP的安装长链路、靠视频或群消息发现更新、新版不好用时切换版本不便的原始叙事。
- 首页头图移至目录之后、“可以做什么”之前；五个功能场景的标题和正文常显，仅配图放入默认折叠的details/summary，多图流程用table并排。六语配图统一压缩为WebP，缺失语言从实机对应界面补拍；图片懒加载，单图240px、表格内每张180px，并随运行ZIP打包以支持离线阅读。
- 设备信息仅在诊断导出时采集：通过原生API有界读取运行中的Steam客户端版本，在实际生成ZIP的工作线程中读取系统／构建号／内核／架构及非唯一DMI厂商／型号。按Valve Jupiter／Galileo识别Steam Deck LCD／OLED，不用SteamOS推断硬件；无法识别时保留未知，排除唯一标识，不写入日常日志，状态刷新不采集。
- 扩展有界诊断包：补齐分类错误码、受控源码位置、会话／请求／父请求／任务关联、独立依赖事件、管理归属／冻结快照、接管步骤、必要运行开关与备份清理统计。区分健康的本地目录缓存与 Loader 故障；只读已有落盘任务，不恢复任务或发起联网。目标使用匿名标识，不包含原始错误文本、URL、路径、凭据或用户文件。
- 独立计算前后端构建指纹，记录实际加载的前端版本；校验磁盘 bundle 元数据并在导出时重新读取，识别只替换前端的情况。补齐仅限本插件的页面／操作／JS／渲染异常记录，不吞掉宿主原异常；导出前有界等待队列刷新，保留既有日志／包大小及分享限制。
- 将Python后端按功能拆入`backend/`：`backend/main.py`只保留生命周期，设置、插件管理、发布版本、安装与诊断移至`backend/sideload/`。打包时生成Loader要求的根`main.py`与`py_modules/sideload/`，调用方及测试同步迁移，不保留旧导入兼容层。
- 新增`pnpm package-plugin`及GitHub构建workflow，覆盖Pull Request、`main`推送和版本标签；检查、构建并上传运行ZIP。版本标签与包版本一致时发布ZIP，Release正文使用完整英中版本章节，版本不匹配则拒绝打包。
- 超过七天的备份在启动、每小时及准备新安装前自动删除；设置新增须确认的“清理数据备份”，可同时清理近期备份，保护活动／未知任务与仍运行的工作线程。保留原数据、安装包和任务证据，备份详情显示删除状态，部分清理失败如实报告，六语同步。
- 按Power Timeline结构重写面向用户的README：根目录只保留英语，简中、葡萄牙语、西班牙语、日语、韩语全文放在`docs/readme/`；提供六语导航、安装与首次使用、备份边界、语言设置及可执行的源码打包命令，不恢复已删除的设计文档。
- 删除独立设计原型`demo/`、未使用的空目录`defaults/`及附属文档目录`docs/`；清理源码失效文档索引与README链接，正式运行逻辑与多语言词条保持不变。
- 将已确认 Demo 的页面层级接入正式插件：首页聚焦插件与更新，安装和设置在列表上方随正文滚动；详情拆分概览、其他版本和维护，商店插件与重新识别移入设置。原安装、接管、备份、取消和诊断能力保留。
- 新装仓库地址与关键词搜索合并为一个原生输入框，右侧放搜索按钮，左右各16px留白、间隔8px；回车走相同提交入口，不把输入法确认或重复按键当成新提交。仓库结果由下拉改为首页风格列表，点选即探查；保留输入草稿、结果与返回焦点、过时响应隔离、monorepo显式选择和安装确认。
- 回车提交后收起原生键盘。安装预览及搜索进入的已安装详情通过顶部返回或B键回到原搜索结果，保留输入与行焦点。安装准备开始即进入任务页，随后展示真实下载字节、百分比与阶段；保留任务返回预览／搜索的路径及原生安装确认。
- 新装预览在“继续安装”上方提示第三方代码与来源风险；所选项目声明root权限时另显示“此插件可以完全访问你的 Steam Deck。”。已安装详情不显示，monorepo按所选项目独立判断，覆盖六种界面语言。复用仓库探查、不增加请求；保留实际ZIP校验，不新增勾选或确认弹窗。
- 在原生安装前按任务既有连接模式与时限下载声明的额外依赖，校验SHA-256后合入重新核验的本地ZIP，并保留原始包。任务页分开展示主包／依赖进度、Loader阶段和明确失败原因。成功必须同时取得关联的Loader开始／完成证据及全部安装文件核验；移除超时后仅凭文件判成功的路径，撤销旧的无完成证据成功记录。
- 任务阶段列表改为紧凑圆点连线：保留全部8个安装步骤，点亮当前及之前节点，下方只显示当前步骤文字；“卸载并安装”另加卸载节点。位置由真实任务证据决定，失败、取消、手动结束及未知结果不点亮后续步骤；保留下载进度、错误、备份详情和操作。
- 安装成功后刷新已安装清单及当前详情，QAM重挂同样生效；B/返回显示当前版本与更新状态。丢弃安装完成前启动的迟到清单，不能以旧请求代替完成后的刷新。
- 监听独立卸载的原生调用完成，刷新本地插件清单并从已移除目标的页面返回首页，避免QAM重挂后仍显示过时身份错误。保留原生确认、取消／失败时的页面、新选择及重装中间卸载阶段。
- 商店管理改以Loader当前登记与其Default／Testing／Custom频道的原生更新能力共同判定；历史商店同名记录不再迫使未登记插件先接管。前后端授权与展示共用同一原生快照，保留显式接管、冻结及身份守卫；按频道保存更新能力，只有显式刷新才联网，未知不授予安装权限。
- 终态任务的“关闭任务提醒”与“不再提醒”改为两个并排按钮。关闭只隐藏精确当前任务；不再提醒先保存关闭任务提示，再隐藏当前任务。所有页面统一遵守已保存的开关值，读取完成前不显示横栏，迟到读取不能覆盖新选择。保留任务证据及最近任务入口，保存失败不假成功，可从设置重新开启提示。
- 设置新增本插件风格的离线开源许可页：组件分组、间接依赖折叠、版本／署名／来源及许可证全文。依据真正进入bundle的模块生成材料，包含Font Awesome和内嵌代码声明，区分宿主提供的Decky UI；QAM重挂保留文档与展开状态，顶部返回及B逐级返回并恢复焦点。
- 接入简中、英语、葡语、西语、日语、韩语，原生语言下拉显示 SVG 国旗与语言自称。自动读取 Steam/浏览器语言，手动选择独立持久保存；切换不清空草稿、版本选择、任务或确认框勾选。
- 使用真实300px QAM容器及16px内容留白，沿用原生面板背景，移除插件自绘灰底与固定底栏。首页条目上下12px、左右16px；导航最小44px、上下8px，长文案自然增高；局部提高Field规则优先级，避免原生上下10px覆盖。
- 纠正首轮正式界面与 Demo 的还原偏差：标题/分组计数/图标列、版本摘要与显式版本列表、维护导航行、网络四选项和备份详情展开；选择版本后返回概览。修正 Decky 内部区段样式与 Steam 同名类不同造成的重复留白，正文可用宽度268px；保留原生控件、确认、持久化和用户主题。
- 修正首页检查更新按钮被全局主题圆角／聚焦环覆盖的问题，仅在本插件内保留紧凑矩形与清晰聚焦对比。安装／设置按钮左右与正文对齐、相隔8px，与下方列表留24px，不叠加多层边距。
- 全部页面统一左右留白：移除仅首页／设置生效的布局分支，区段横向padding归零，列表、导航、表单、轻量操作和展开项均由自身16px padding承担内容留白，背景铺满300px、左右margin为0，不再依赖表单负边距。标题、说明、发布正文和非整行按钮单独对齐；原生输入标题、输入框及说明一并覆盖，聚焦前后位置不变。
- 修正任务提示父区段漏改的问题：首页及设置中的“查看任务／关闭任务提示”两行背景铺满300px，左右margin为0、padding为16px；首页标题、检查和双按钮区保留内容留白，不再用整个概览区段的padding缩窄任务背景。返回、仓库模式、发布说明、跳过版本和展开项保留整行焦点及内部留白。网络单选移除蓝框及原生重复焦点环；表单留白覆盖网络下载时限等原生控件。
- 修复详情读取版本后列表仍显示“尚未检查更新”：已安装插件详情复用后端检查并保存单项结果，返回列表或请求迟到完成都会同步；失败不冒充已是最新，其他插件结果、定时历史及整轮检查周期保留。
- 修复详情重进、QAM重挂及概览／版本／维护切换重复联网：与整轮检查共享完整成功Release快照，同身份在途读取合并，显式刷新才替换成功结果；失败保留上次内容与时间并显示错误，无成功记录时不虚构回退。快照独立有界落盘、重载恢复，身份变化失效；不改变版本比较、安装授权或网络偏好。
- 插件详情与新装预览显示所选版本的下载量，切换版本同步变化；合计 GitHub 当前 Release 上传附件的下载次数，空附件列表为0，统计缺失或旧快照显示未知。复用现有 Release 缓存与刷新，不因切页增加请求。包名与下载量合并为对齐的版本元属性，文字与图标均为12px，不显示解释小字，覆盖六种界面语言。
- 详情更新按钮移至版本摘要上方，右侧保留图标刷新按钮；两者使用相同原生主按钮外观与高度。“跳过此版本／取消跳过”放到最后，复用“选择其他版本／维护此插件”的整行导航尺寸和布局，保留加载、禁用状态及安装守卫。
- 版本说明预览与独立全文页均渲染GitHub风格Markdown，支持标题、列表、代码、表格与图片。“查看完整发布说明”复用“选择其他版本／维护此插件”的原生导航行，不再原地展开；跨页面与QAM重挂保留所选版本和新装草稿，返回恢复入口焦点，全文提供原生段落阅读焦点。清理嵌入HTML与URL，复用缓存正文，不额外请求Release。
- 所有内页保留紧凑返回栏，写明实际目标页面；上下padding为8px、自然撑高，不锁固定高度，移除额外顶部空白。保留原生B键层级返回与焦点恢复，共享页标题不再参与焦点导航，标题样式及正文阅读焦点不变。原生ScrollPanel继续承载左摇杆滚动。没有主要版本操作时，刷新收到“版本信息更新于”行最右侧；有操作时仍与主按钮同排。已跳过提示位于插件标题下方，按内容自然撑高。
- 去掉“查看完整发布说明”和“选择其他版本”之间的间隔。首页依据匹配的Release快照与跳过规则区分“已是最新”和“已跳过最新版本”；无匹配快照时只显示“已跳过版本”。不遮盖检查失败或仍可安装的其他更新，不增加联网请求。
- 跳过／取消跳过改为本地筛选，不再使Release缓存身份变化；从完整缓存和已保存的包版本证据重算该插件的更新状态，不抓取Release、ZIP或Git。保留所选版本／安装包、成功时间、定时历史及已有刷新错误；在途检查发布前采用最新跳过规则。
- 修正查询5秒超时边界：JSON/raw、ZIP Range及官方商店查询等待完整响应头最多5秒，随后按连续5秒无正文进展超时；持续接收可超过5秒，不再在HTTP200后因整段耗时达到5秒而截断。超时仍按原网络模式切备用通道，正文大小、长度和解析校验不变。完整下载保持60秒连接／响应头及正文无数据等待、原3–30分钟整包总时限；缓存身份规则和跳过设置不变。
- 首页与设置页内容最底部均提供“Language / 语言”入口及语言图标，复用同一原生下拉与语言偏好，随正文滚动、不加固定底栏；在任一入口切换后另一处同步。选项继续使用各语言自称与国旗，误选陌生语言也可直接改回。国旗作为独立SVG图片显示，隔离原生菜单聚焦项强制白色fill，不改变原生高亮和语言保存行为。
- 统一插件详情、加载提示、仓库搜索结果与任务页的图标列和正文留白，清除原生图标容器的10px右内边距；详情“选择其他版本 / 维护此插件”改为连续紧凑两行。设置导航图标缩至16px；首页插件行和设置导航行的背景铺满300px，左右margin为0、内容用左右16px padding与分组标题对齐，聚焦不改变位置或让图标贴边。
- 切换到第三方连接模式前，完整展示服务地址与信任风险，须主动同意才能保存；取消保留原模式，进行中的任务不切换通道。
- 原始 Release 正文、仓库/路径、后端错误及历史证据保持原样，不改写为推测翻译。本轮未推送或发布。


## Demo interaction validation / Demo 交互验证 - 2026-10-23 (preliminary record / 先期记录)

### English

- Add six languages, SVG flags, language self-names, automatic browser-language selection and an independently remembered language choice to the demo. Translate pages, statuses, confirmations, risk notices and error explanations; switching languages preserves selections, inputs and simulated tasks.
- Match Steam's native QAM with a fixed 300px plugin panel, 48px sidebar and 16px horizontal content insets. Remove responsive widening; wrap long text and scroll vertically instead of hiding actions to fit.
- Add multilingual support to the plugin roadmap. This preliminary entry changes only the static demo, not the plugin version or device installation.

### 中文

- Demo 增加六种语言、SVG 国旗与语言自称、跟随浏览器系统语言和独立的语言记忆；翻译覆盖页面、状态、确认、风险告知和错误说明。切换不清空当前选择、输入或模拟任务。
- 按 Steam 原生 QAM 样式固定 300px 插件面板、48px 侧栏及 16px 左右留白，移除响应式扩宽；长文案换行、纵向滚动，不隐藏操作来适配。
- 多语言列入正式插件后续路线。本节仅修改静态 Demo，正式插件版本和设备安装不变。

## [0.2.2] - 2026-10-22

### English

- Add a persistent GHFast (file acceleration) network option. Use GHFast for raw/Release files; unsupported GitHub API requests use direct access with GH-Proxy as fallback. Automatic mode adds GHFast as the third file-download route, reporting the actual route and attempt count. Keep the default mode, cancellation and download validation unchanged.
- When installation has started but no completion event arrives for more than two minutes, rechecking compares every file in the selected package. Matching files complete the task with an explanation that the event was missing; mismatches, a missing start event or conflicts remain unconfirmed.
- Offer download timeouts of 3, 5, 10, 15 or 30 minutes in Settings, retaining the three-minute default. After a route exhausts its deadline, the next fallback receives the same allowance; fast failures share the remaining time.
- Allow skipping the currently selected version in plugin details, remembered per plugin and repository. Subsequent checks ignore skipped versions; skipping can be undone.
- Accept ordinary spaces in directory names. If preparation fails after a successful download, the task page can retry installation preparation with the downloaded package; failed downloads can be retried. Neither action automatically submits the package to Decky.

### 中文

- 网络设置新增持久化的“GHFast（文件加速）”。GHFast 用于 raw/Release 文件；不支持的 GitHub API 请求使用直连及 GH-Proxy 备用。自动模式新增 GHFast 作为文件下载的第三通道，通道名称和尝试次数如实显示；默认模式、取消与下载校验不变。
- 安装已开始且超过 2 分钟仍没有完成事件时，重新核对会比对所选包的全部文件。文件一致就结束任务，并说明没收到完成事件；文件不一致、没有开始事件或存在冲突时仍保持未确认。
- 下载超时可在设置里选择 3、5、10、15 或 30 分钟，默认仍是 3 分钟。当前通道耗尽时限后，下一备用通道获得同样时限；快速失败共享剩余时间。
- 插件详情可以跳过当前所选版本，并按插件和仓库记住。之后的检查会忽略被跳过的版本；可以取消跳过。
- 目录名里的普通空格不再被当成非法安装包。下载完成后如果准备失败，任务页可以继续用已下载的包安装；下载失败可以重试下载。两者都不会自动提交给 Decky。


## [0.2.1] - 2026-10-19

### English

- Prefer the semantic version in the sole installation package's `package.json` when checking updates, using HTTP Range to read only the ZIP directory and that file rather than downloading the whole package. If unavailable, use an unambiguous semantic version from the tag or filename; otherwise inspect the same-named plugin's source file at the tag root for display and comparison only. If none is available, leave versions incomparable rather than using release dates. When the package and tag disagree, the package version wins.
- If the newest release has no installation package, compare against the most recent earlier release that does, and explain the missing package. Reject a package confirmed to have no version before downloading it; do not guess among multiple packages.
- Add Show task notice on Home to Settings, enabled by default. Disabling it hides the home task banner without changing other pages or View latest installation task.
- Place Incorrect detection? Try refreshing before Take over store plugins on Home, instead of beside the available-updates summary.
- Add automatic deletion of the current installation package after success, with manual deletion on the task page. Do not delete data backups. Enable this by default, including when older settings lack the field.

### 中文

- 更新检查优先读取唯一安装包内 `package.json` 的语义化版本，只用 HTTP Range 读取 zip 目录和该文件，不下载整包。读不到时才用标签或文件名中的唯一语义化版本；仍没有则看该标签根目录里同名插件的源文件，且只用于展示和比较。都没有则保持无法比较，不用发布日期。安装包版本与标签不一致时以安装包为准。
- 最新发布没有安装包时，改为比较之后最近一个有安装包的版本，并标明最新发布没有安装包。确认安装包没有版本时，在下载前拒绝。多个安装包不猜测。
- 设置新增“首页显示任务提示”。默认开启；关闭后首页不显示任务横栏，其他页面和“查看最近安装任务”不变。
- 首页“识别结果不对？试试刷新”放在“接管商店插件”前面，不再占用可更新摘要旁的按钮。
- 设置可在安装成功后自动删除本次安装包；任务页也可手动删除。数据备份不删除。默认开启，旧配置缺这个字段时也按开启处理。

## [0.2.0] - 2026-10-17

### English

Installed on Steam Deck for user review; not pushed or published. The source feature commit is `8b6a85f`, separate from the 0.1.0 baseline. The base version, hashes of 16 program files, single refresh icon and home-page loading were verified; full functionality was not yet accepted.

#### Changes

- Ordinary list reads, QAM remounts, GitHub update checks and installation-eligibility rechecks read only local store records, without automatically requesting the store catalog.
- Keep one native refresh icon on Home; re-identify sources only on activation. Support real busy state, coalesced repeated clicks and queuing across QAM remounts.
- Remove persistent store-source, expired-cache, catalog-date and normal Loader inventory-count text. If explicit refresh fails, keep the previous valid list and show a short error.
- Persist confirmed plugin identity, repository and store history without five-minute or seven-day expiry. Historical absence from the store applies only to the matching identity observed at that time and does not authorize newly installed or same-named replacements.
- Historically known store plugins remain managed by the store; old repository bindings do not imply takeover. The existing explicit confirmation and native freeze flow can take over without waiting for the store API to recover.
- Save directory and package identities with new bindings and associations after verified installation. Require reconfirmation when identity or repository metadata changes; ordinary version changes retain the relationship, and historical repositories do not restore an explicitly removed binding.

#### Fixes

- Fix automatically identified non-store relationships reverting to Unknown offline, and expired historical store records allowing old repository bindings to incorrectly imply management permission.
- Wait for the actual explicit store-refresh worker during plugin unload. Queued refreshes cannot begin writing after shutdown, preventing an old instance from overwriting new records.

Preserve installation cancellation, backups, evidence verification, takeover repository restrictions, Loader management as the source of truth and diagnostic safety limits. The full gate passed 184 frontend tests with 1,187 assertions and 231 Python tests. Local tests do not constitute user approval or acceptance of the new version on hardware.

### 中文

已安装到Steam Deck，等待用户实机审核，未推送或发布。源码功能commit为`8b6a85f`，相对0.1.0基线独立提交；基础版本、16程序哈希、唯一刷新图标和首页加载已确认，完整功能尚未验收。

#### 变更

- 普通列表、QAM重挂、GitHub更新检查和安装资格回查只读取本地商店记录，不自动请求商店目录。
- 首页保留一个原生刷新图标，点击才重新识别；支持真实busy、重复点击合并和跨QAM排队。
- 移除常驻商店来源、缓存过期、目录日期和正常Loader清单计数。显式刷新失败保留旧有效列表并显示简短错误。
- 已确认插件身份、仓库和商店历史持久保存，不因5分钟或7天失效。历史不在店仅适用于当时已观察到的匹配身份，不给新装或同名替换授予权限。
- 历史已知商店项继续由商店负责，旧绑定不自动接管；可通过已有显式确认及原生冻结流程接管，无需等待商店接口恢复。
- 新绑定与已核验安装的关联写入同时记录目录和包身份；身份或仓库元数据变化要求重新确认，正常版本变化不清除关系，解除关联不被历史仓库恢复。

#### 修复

- 离线后自动识别的非商店关系退成未知，以及历史商店记录过期后可能被旧仓库关联误判为可管理。
- 插件卸载时等待显式商店刷新真实worker结束；停止后的排队刷新不能开始写入，不遗留旧实例覆盖新记录。

保留原安装取消、备份、证据核验、接管仓库限制、Loader管理SSOT及诊断安全限额。完整门禁184项前端测试、1187条断言、231项Python测试通过；本地测试不代表用户审核或新版真机验收。

## [0.1.0] - 2026-10-17

### English

First consolidated source baseline for the preceding development work, including completed features and Task 52 interaction adjustments. The device still ran an earlier 0.0.1 build; this version was not deployed or published.

#### Added

- List plugins using the Loader inventory and on-disk identity, with GitHub repository binding, search, static repository probing, Release selection and historical versions.
- Manual and scheduled update checks, an independent notification toggle, check results and history details.
- Explicit store-plugin takeover, pausing native store checks, and handing control back while restoring the target's original freeze policy.
- Download progress, bounded ZIP validation, standard data backups, native Decky installation confirmation, installation-evidence verification and final repository association.
- Bounded redacted diagnostic logs, a dedicated diagnostics page, local QR codes and short-lived LAN downloads.
- Loading animations for downloads and details, uninstall/reinstall confirmations, cancellation at precise task stages and dismissal of terminal notices across reloads.

#### Fixes

- Fix state overwrites or duplicate submissions caused by initialization, polling, QAM remounts, late callbacks and interleaved installation/cancellation.
- Wait for the full cancellation recheck coroutine during plugin unload, and prevent a late installation RPC rejection from entering the uninstall-cancellation phase.
- Prevent prefetch requests from consuming diagnostic-download allowances; fix native SVG rules shrinking QR codes to 16px and the narrow-panel progress layout.

#### Development notes

- Preserve the sandbox-required `py_modules` layout, source semantic index, current contract guides and regression tests.
- Start semantic versioning and this changelog. Runtime diagnostics use the actual plugin version supplied by the SDK; the HTTP User-Agent no longer carries a stale duplicate version.
- Verification before this baseline comprised 180 frontend tests, 1,164 assertions, 214 Python tests, type checking and build checks.
- Keep physical-controller input, saving on phones, complete upgrades/downgrades and other unverified areas explicitly outside the existing acceptance claims.

### 中文

首次整理此前持续开发的完整源码基线，汇总已完成的实现和任务52交互调整。设备仍运行此前0.0.1构建，本版本未部署或发布。

#### 新增

- 基于Loader管理清单与磁盘身份展示插件，支持GitHub仓库关联、搜索、静态仓库探查、Release和历史版本选择。
- 手动与定时检查更新、独立通知开关、检查结果和历史详情。
- 显式接管商店插件、暂停原生商店检查、交还并恢复该目标原冻结策略。
- 下载进度、受限ZIP核验、标准数据备份、Decky原生安装确认、安装证据核验与仓库关联收尾。
- 有界脱敏诊断日志、独立诊断内页、本地二维码和短时局域网下载。
- 下载及详情加载动画、卸载/重装确认、精确任务阶段取消，以及跨重载保留的终态提示关闭。

#### 修复

- 初始化、轮询、QAM重挂、迟到回调与安装/取消交错造成的状态覆盖或重复提交。
- 插件卸载未等待取消回查完整协程结束，以及安装RPC迟到拒绝误进入卸载取消阶段。
- 预取请求消耗诊断下载额度、原生SVG规则将二维码压至16px，以及窄面板进度布局。

#### 开发记录

- 完整保留沙箱所需 `py_modules` 布局、源码语义索引、当前契约指南和回归测试。
- 开始使用语义版本与本Changelog；运行诊断使用SDK提供的真实插件版本，HTTP User-Agent不再携带过时版本副本。
- 本版本整理前的验证为180项前端测试、1164条断言、214项Python测试及类型/构建检查。
- 物理手柄、手机保存、完整升降级等未验证范围保持，不扩大既有验收结论。

## [0.0.1] - Historical development version / 历史开发版本

### English

- Initialize from the official Decky template, then progressively implement repository binding, release selection, update checks and subsequent tasks.
- Multiple device deployments reused 0.0.1 during this stage; do not retroactively count every deployment as a separate formal version.

### 中文

- 官方Decky模板初始化，逐步完成仓库绑定、发布选择、更新检查和后续任务。
- 此阶段多次设备部署复用了0.0.1，不追认每次部署为独立正式版本。
