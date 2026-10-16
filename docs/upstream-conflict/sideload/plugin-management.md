---
semantic_state: active
last_reviewed: 2026-10-17
origin: 用户要求重新设计 GitHub Sideload 界面并由主代理实施
provenance:
  - HANDOFF.md
owners:
  - src/loaderManagement.ts
  - py_modules/ownership.py
  - src/index.tsx
  - src/installController.ts
  - main.py
  - py_modules/repo_probe.py
  - py_modules/install_ops.py
  - py_modules/install_flow.py
  - py_modules/github_transport.py
  - py_modules/catalog.py
participants:
  - src/Home.tsx
  - src/ownershipController.ts
  - src/checkController.ts
  - src/InstalledTab.tsx
  - src/InstallTab.tsx
  - src/BindingEditor.tsx
  - src/SettingsTab.tsx
  - src/NetworkSettings.tsx
  - src/CheckResults.tsx
  - src/RepositorySearch.tsx
  - src/ReleasePicker.tsx
  - src/OperationPanel.tsx
  - src/uiIcons.ts
  - src/pluginIcons.tsx
  - src/components.tsx
  - src/api.ts
  - src/types.ts
  - src/viewModel.ts
  - src/viewState.ts
  - src/loaderBridge.ts
  - py_modules/github_meta.py
  - py_modules/persist.py
  - py_modules/scan.py
archived_at: null
archive_reason: null
superseded_by: null
---

# GitHub 来源插件管理

## Requirement

- Requirement identity: GitHub Sideload 插件管理界面与安装闭环。
- Business outcome: 在 QAM 内找到需更新的插件、维护仓库、选择 Release，并在明确备份范围和原生确认后安装。
- Summary: 首页采用原生焦点行，详情进行版本管理，新装支持直填/搜索、仓库插件探查及发布预览，后端准备可校验的包和数据备份，Loader 拥有安装执行权。
- Intent: 降低窄面板操作负担，避免假更新状态、安装请求重复发送和未备份即替换。
- Scope: multi-file。
- Risk level: high，涉及插件代码与数据替换。

## Local Semantic Contract

独立的 [有界诊断与导出契约](diagnostics.md) 仅观测此流程，不能改变事务顺序、安装裁决、管理SSOT或现有错误反馈。

### 本地关系与手动刷新

- 普通列表、QAM重挂、详情/设置导航、手动与定时GitHub更新检查和安装授权只读本地关系，不请求商店或登记服务。`list_sideload_plugins(refresh=False)`是默认只读路径，只有唯一刷新图标调用`refresh=True`。GitHub查询仍按原用户偏好进行，不顺带刷新商店。
- `catalog.json`在本插件SDK settings目录保存version=1的成功目录、累计商店正向名称及按name/folder/package.name记录的识别事实和仓库。可读旧timestamp/names格式；新记录最多512项，商店名称最多4096项，总4MiB，字段有界、有限时间、唯一键、regular/no-follow/本UID/单链接校验，0600临时文件/fsync/原子替换，写成功才发布内存。失败不覆盖旧记录或伪装空目录成功，损坏文件保留并阻止从损坏历史推导权限。此文件包含持久状态，不能按临时缓存清理；旧版刷新可能重写新字段，版本回退前须保留该状态。
- 已确认关系不因5分钟、7天或进程重载消失。当前目录与累计商店正向历史独立，商店暂时移除名称不自动变成非商店授权。旧目录中的缺席不为后来新装项或新身份授予权限，负向事实必须来自显式成功刷新当时的唯一、匹配本地身份。版本变化不改变身份；目录、package.name或已有明确仓库元数据变化要求重新确认，旧事实不被刷新自动改绑。
- 手动绑定和已核验安装的幂等关联保存同步记录folder/package.name/package.repository身份标记，仍与接管ledger独立。显式确认的新绑定可修复非商店身份变化，旧名称绑定不能跳过已发现的身份冲突。缺少仓库元数据可使用匹配记忆的仓库并标记remembered；显式disabled优先，不能恢复用户已解除的追踪。
- 归属仍由ownership.resolve集中裁决，持久历史不单独等于商店接管。已知历史商店项可通过既有独立入口显式确认仓库并执行冻结/回查，不依赖实时目录。身份变化的active接管项禁止安装，仍可显式交还并重新接管。
- 首页不显示商店来源、缓存过期、目录时间或正常Loader清单计数。摘要右侧只保留一个原生DialogButton刷新图标，含可访问名称和A动作说明，装饰图标不增加焦点，busy真实旋转且禁重复，遵守reduced-motion。只在实际读取失败时显示简短错误并保留有效旧列表，不暴露缓存工作原理。
- checkController区分默认读取和一次性显式刷新意图，跨QAM合并，检查进行时排队，网络刷新已在途的重复点击不再排一次；原generation、listError/checkError、正常读取和检查队列契约保留。新检查使旧读取失效不自动重放商店请求。
- 显式刷新持有原归属锁，活动安装任务及恢复的活动任务期间拒绝变更来源。真实Catalog worker登记并shield，取消外层RPC仍等待线程；unload先撤销诊断分享，再等待已启动Catalog worker，停止后的排队刷新不得新写盘。普通读取始终零商店网络。

### 显式商店接管与交还

- 业务结果：已知当前或历史商店插件默认由商店负责。普通列表不把它们列为待关联；首页另有“接管商店插件”入口。异常可见性独立，Native-only与同名多目录始终只读。缺少有效本地记录时不猜商店或非商店，元数据仓库不构成所有权；匹配身份的持久非商店关系继续管理，未知来源的显式用户仓库修复保留，历史已知商店项不能借此绕过。
- `py_modules/ownership.py` 是归属裁决与 ledger schema 的 owner。`main.py` 组合磁盘、商店、绑定、独立 ledger 和原生冻结回查，输出 `ownership.state/eligible/intent/error`。`src/loaderManagement.ts` 只增加 Loader 只读约束，`pluginEligible` 是前端统一资格入口，页面不能自行用 repo_source、加载状态或旧报告授予权限。
- ledger 位于本插件设置目录 `ownership.json`，不附着于 `bindings`。缺失为 version=1/空 entries；损坏不覆盖、不默认为已接管，禁用 mutation。最多 512 个唯一 name+folder 条目、1 MiB；名称/目录/仓库最多 256 字符，目录必须是 basename，intent id 为 32 位十六进制，prior_frozen 为严格 boolean。读取有界、regular/no-follow，读取与写入前使用同一完整 schema 校验；容量检查使用与持久化一致的 UTF-8、ensure_ascii=false、indent=2 序列化字节数。无效候选不得替换已有 ledger。写入使用既有独占临时文件/fsync/原子替换。旧绑定原样保留，不迁移为同意。
- RPC `begin_store_ownership(name,folder,repo,action)` 只接受唯一非自身磁盘身份；takeover必须有已确认的当前或历史商店正向记录和有效、显式确认的仓库。先记录原冻结值、动作、新 intent id 和 prepared，再允许原生副作用。return 只由 active 记录产生新意图，继承原 prior_frozen。目标存在活动安装任务，包括恢复的旧 ready、未知任务和尚未产出包的 target 时，禁止修改归属或关联。
- `ownership_native_step(id,phase)` 以当前 intent id、名称、目录和活动任务再次核验。read 只返回该意图与 bounded frozen 名称；writing 先持久化原生写入领取；confirming 表示原生调用已返回，不等于激活；complete 必须核对当前冻结值与 desired policy，才保存 active/released。prepared 不能直接 complete。原生失败、超时、重载或保存失败保持未确认；显式“回查接管结果”可在 writing/confirming 后核对落盘结果，“显式继续冻结操作”使用同一意图读取最新列表再写，不自动重试。不恢复全局旧快照，旧 id 不得激活新意图。
- `src/ownershipController.ts` 在 QAM 外保存单一动作、错误与原生 Promise。QAM 重挂不重放；插件卸载使后续步骤失效。原生等待 10 秒后提示未知，但超时不等于底层取消，本前端实例仍等真实调用结束才解除排他；迟到回调不激活。`loaderBridge.ts` 只调用已验证的 frozenPluginsService.update/invalidate，保留接收者，不猜写设置 RPC。每次基于后端最新列表只增加/移除当前名称，保留无关名称。
- Loader 后端设置仅从 SDK DECKY_HOME/settings/loader.json 读取 `frozenPlugins`。4 MiB 有界 regular/no-follow 文件，最多 4096 个唯一受限名称。损坏、缺文件和无法读取是未知；合法配置缺 key 才等于默认空数组。不得返回或记录完整 Loader 配置、用户资料。
- active 只有当前冻结回查仍包含准确名称才具管理资格。已接管项由 ledger 确认的仓库追踪；泛用解除关联被禁止，详情改为“交还商店”。交还只恢复 prior_frozen：原先 true 仍冻结，原先 false 只移除当前名字，停止本插件追踪，保留程序、数据与旧关联。修改已接管仓库需交还后重新明确接管，不能通过泛用关联修改绕过接管确认。
- 冻结暂停原生更新检查并清理当前待更新项，不禁止手动商店安装，也不保证取消已经在途的检查。原生数组写入不具跨应用事务保证；并发其他应用仍可能在读取和写入之间改动名单，不能宣称全局无覆盖风险。
- `main.py` 对手动/定时检查、绑定/解绑、prepare_install、submit_prepared、claim_uninstall、complete_uninstall 及独立卸载授权统一设门槛。新装探查匹配已有名称必须回到既有资格判断，旧 ready 不能跳过。已接管项在准备、提交、卸载领取及卸载后继续时，规范化请求或任务仓库必须与 ledger 确认仓库一致，GitHub 名称大小写不造成失配；拒绝不修改归属、绑定或安装完成证据。合法显式卸载后的目录缺席按原流程继续核验 ZIP/备份/同名缺席，不能误要求被卸载目录仍存在。单独卸载先调用 authorize_plugin_mutation，再进入 Loader 已有原生卸载确认；不叠加同类弹窗，wrapper 的 void 不代表卸载完成。迟到授权按控制器 generation 和当前活动任务隔离。
- 检查报告记录 folder/ownership_id，已接管商店项只接受当前 consent id 的匹配报告。旧报告不迁移、不删除，失配或已交还只显示历史，无可执行更新箭头。GitHub 查询期间交还后，后端返回重新解析的当前列表，迟到检查报告不得恢复旧权限。成功安装仍先持久化候选，再内存，再原幂等 bindings 收尾与成功状态；独立 ledger 不会被原绑定替换覆盖。
- 纯说明使用无 label、无焦点的原生 Field description，采用插件行为为主体。新装说明为“插件会静态读取仓库信息，识别可安装的 Decky 插件。”仓库确认、安装风险、备份范围、错误和网络风险仍在决策前可见，无新增 info/help 控件。

### 页面与查询

- `src/api.ts` 是统一列表读取边界：普通列表、手动检查返回、关联后重读、新装探查和定时触发重读均通过 `loaderBridge.ts` 无参数调用 `window.DeckyBackend.call("loader/get_plugins")`，再由 `loaderManagement.ts` 纯合并。后端提供磁盘、商店与中央归属事实，后端 RPC 失败照常拒绝；Loader 读取失败独立返回错误，不清除检查错误或伪装成功空清单。
- Loader 管理快照必须是唯一非空名称、string/null version、有限数值 load_type、boolean disabled 的完整数组。接口缺失、拒绝、损坏、重复名称或 5 秒超时使 Loader 状态未知；合法空数组表示当前未登记。迟到结果不修改快照，不持久化管理信息，不依赖安装事件或卸载通道。
- Loader 管理集合与商店来源独立。唯一显示名磁盘项获得 enabled/disabled；仅磁盘项保留并标记未登记，不能据此推非商店。多个同名目录全部保留并标歧义，只读；不任选绑定。Loader-only 项保留 null folder、null 包内 version、未知 in_store，无猜测 slug/仓库。自身以准确名称排除。
- 包内 version 始终用于 GitHub 比较、报告身份与安装核验；Loader version 独立展示，差异只提示、不修复、不判安装成功。Native-only 详情仅显示 Loader记录版本。商店历史、用户绑定与显式所有权分别判定，页面不显示缓存工作原理。用户绑定不能覆盖商店责任。
- 首页默认保留既有 GitHub 管理范围，同时展示 Native-only、禁用、未登记、目录歧义及版本不一致项，不强制列出所有正常商店项；Loader 读取状态/错误只显示一次。原目录 key 不变，Native-only 使用按准确名称编码且含 `/` 的稳定 key，避免与真实目录 basename 冲突，贯穿分组、React key、详情选择与一次性返回焦点。
- Native-only 与目录歧义详情不读取 Release，不提供安装、卸载、绑定或解绑；检查记录不能为它们显示可执行更新，但只读详情仍以历史记录保留检查时间、检查时仓库、版本及完整错误。当前磁盘可核验项在 Loader 未知或未登记时仍保留受校验管理能力。定时后端检查规则、settings schema、安装控制器与安全闭环不受管理快照影响。

- `src/index.tsx` 控制首页、详情、仓库、新装、设置、网络连接、诊断内页、定时检查结果、安装任务页面；页面切换以 `key` 重建外层容器并触发原生 `autoFocus`。仅显式从详情返回首页/结果页，或从结果/网络/诊断内页返回设置时，使用 `PREFERRED_CHILD` 和对应行的 `preferredFocus` 恢复位置；首次 `onGamepadFocus` 消费恢复意图，此后使用 `FIRST` 按可见顺序进入，顶部返回箭头向下不得持续跳到上次插件行。恢复意图跨 QAM 保留，不使用定时猜测或自造导航。处理子页面 B 键时同时 `preventDefault` 和 `stopPropagation`，首页 B 交还 Loader。使用原生组件、主题及 A/B 导航语义。
- Decky 原生版本下拉框及键盘可能卸载 QAM 内容；`viewState.ts` 在前端会话内保留页面、输入草稿、安装预览和所选版本，并向重挂载后的组件发布旧弹窗回调的结果。详情选择以插件目录、仓库和已安装版本隔离；发布或资产消失时禁用对应选择，不静默换包。安装任务仍以后端持久化状态为准。
- `src/Home.tsx` 每条插件只有一个焦点。人工检查可重新分组；普通后台刷新保留当前分组避免焦点行移动。缓存的可更新组出现不再匹配 update 的成员时，标题改为中性的“检查结果”，保留原分组 key 与行位置，不让失败或旧结果继续被分组标题标成可更新。`checkController.ts` 保存独立的 checking/refreshing、唯一检查与列表刷新 Promise、列表和分源的 listError/checkError，兼容既有 error 读取接口。唯一刷新图标即时显示aria-busy和真实旋转，等待检查时仍保持busy；同一显式意图合并并跨QAM保留，finally恢复可重试。读取错误以简短“刷新失败”单独呈现，始终保留图标入口，不伪装成更新检查失败，不展示缓存或登记运作说明。QAM 卸载/重开不终止检查或允许重复发起，成功与失败均在 finally 停止 busy。检查期间的列表刷新合并排队，检查结束后实际读当前列表；旧读取按 generation 隔离，不能覆盖较新检查或其 loading。普通列表成功只清 listError，checkError 跨重挂载保留，由下一次检查失败更新或成功清除，重试进行中仍保留。检查按钮仅在真实检查时旋转装饰 SVG，遵守 prefers-reduced-motion。首页仅检查更新使用满宽主按钮，安装入口、显示范围和设置使用原生单焦点 Field，保持动作标识、禁用与回调保护，安装和任务页内部按钮不变，查看现有任务的导航入口使用次级原生 Field。
- `src/InstalledTab.tsx` 自动读取 Release，保留普通更新/切换版本/同版重装，另设显式“卸载并安装”，两者不互相替代；仓库关联与单独卸载分开。
- `src/InstallTab.tsx` 保留完整 GitHub 地址/owner-repo 直填，并复用 `RepositorySearch.tsx` 的搜索呈现，新装结果使用原生下拉且不默认选择仓库。两种入口统一探查，单插件进入发布预览，多插件需原生下拉显式选目录后读取版本；不满足 Decky 特征则阻止继续。可核验磁盘项只有名称和仓库均匹配时才转入管理；同名但当前不具管理资格的商店项优先进入接管详情，同仓库其他插件不得误转入；同名 Native-only 或目录歧义项优先进入只读详情，不读取 Release。包身份/目录碰撞仍由后端阻止。
- 新装的模式、关键词、结果、仓库选择、探查、插件路径和 Release/ZIP 选择都在 QAM 会话状态中。编辑输入、切换入口/仓库/插件或结束新装会使旧请求失效；旧原生弹窗不得改写其他仓库的选择。B 从预览回插件选择，再回仓库输入，输入页 B 交还上层。
- `src/BindingEditor.tsx` 默认按插件名搜索 GitHub，使用原生单焦点结果行展示仓库全名、说明、Star、Fork 与归档标记；选择后仍须显式保存，搜索不写入关联。保留 `owner/repo` 或 GitHub 链接的直接填写入口。
- `search_repositories(query)` 只请求固定 GitHub 搜索 API，校验 1 至 100 字符查询，最多返回 20 个合法且去重的仓库，保持搜索相关度顺序；默认包含 Fork，显式 Fork 条件保留。网络/限流/格式错误不得显示成空结果。
- 关联搜索词、结果、选中项、手填草稿和错误在前端会话内按插件目录/当前仓库隔离；编辑关键词、离开搜索模式或切换插件后，旧响应不得覆盖当前状态。选择预览及手填页的 B 返回搜索，结果行恢复到上次所选仓库。
- 新装后端必须先以默认分支完整 Git tree 探查根目录和所有子目录。候选需同目录的有效 `plugin.json` / `package.json`，再核对项目自身的现代/旧版 Decky 依赖或 Python/前端入口 import 特征。依赖被工作区提升时，须以子项目自己的 Decky import 确认，不能用根工作区的依赖把普通兄弟项目算成插件。只读、不执行源码、不构建仓库。静态特征不构成作者可信或代码安全保证。
- 探查上限为 20000 个目录项、10 个 plugin.json、40 个文件请求、120 秒文件探查预算；树响应 8 MiB、单文件 256 KiB、blob 响应 1 MiB。截断、超限、文件损坏与请求失败均阻止继续，不伪装成无插件。成功结果在后端最多缓存 10 仓库、5 分钟，限制重复 API 消耗。
- `probe_repository(repo)` 返回规范仓库、tree SHA 与候选路径/显示名/package.name/版本/特征证据。`prepare_install` 对新装重新读取后端探查结果，多个候选不得猜选，接收所选 `plugin_path`；ZIP 的显示名和 package.name 必须与该候选一致，不能用同一 monorepo 的其他插件包代替。既有插件替换仍按原插件身份/目录校验，不因新增探查改变备份机制。
- `persist.py` 保存显式停追踪标记，解除关联不得被 metadata 自动探测恢复，也不得卸载插件；仓库选择仍走现有 `set_binding` 校验，不跳过安装包身份检查。JSON 持久化使用独占创建的 0600 唯一临时文件、fsync 与原子替换，避免进度/目录缓存及重载线程争用同名临时文件；只清理本次创建的临时文件。
- `github_meta.py` 精确解析 GitHub 主机和仓库，按发布时间展示发布；选择最新无资产版本不得自动降到旧版，多资产必须手选。Semver 同版/新版/旧版/未知分别表达，不把任意标签前缀当完整版本。
- `scan.py` 区分商店存在、不存在和未知；手动绑定只覆盖仓库探测，不授予商店接管权限。不声称已经接入自定义频道。来源未知项直接显示，不要求先展开其他插件。
- `catalog.py`只通过显式刷新读取完整校验的官方目录，保存有界成功快照和持久身份关系，普通resolve默认纯本地。时间字段用于内部观测，不驱动自动网络或关系过期。默认`in_store=null`表示未重新读取当前目录，独立`cached_in_store/memory_in_store/memory_changed`及ownership表达历史事实、身份冲突与管理资格；不得把null当作丢失已确认关系。
- 显式刷新商店失败且无成功目录时，可在该次操作内只读官方`.gitmodules`登记辅助；普通读取最多复用当前进程已有辅助结果，不自动联网。仍按canonical仓库与显式folder匹配，登记不授予管理/接管资格，缺失匹配不推断下架或非商店。该次刷新失败返回简短错误，原有效列表保留，不能以辅助结果伪装商店刷新成功。
- 首页插件行优先由 `pluginIcons.tsx` 复用当前唯一准确名称匹配的已加载前端 `Plugin.icon` React元素，不为图标导入、启用或探测插件源码，不猜远程图标地址，不把图标写入后端DTO或持久配置。图标缺失/无效、重复前端名称、读取异常、禁用、未登记或磁盘归属歧义时回退PuzzlePiece；渲染异常只降级该图标，换新图标后可恢复。图标容器aria-hidden、inert、tabIndex=-1及pointerEvents=none，根图标装饰属性不改变原颜色/样式，保留原生单行焦点。Loader-only/管理未知项可复用唯一已加载图标，但不因此虚构磁盘身份、商店来源或管理成功。
- `main.py` 每次检查保存每插件状态与错误，读取报告时必须与当前仓库/安装版本匹配；错误、未关联、无发布、无包、不可比较都不是“已最新”。首页只有匹配 `update` 的状态文字旁及汇总使用独立向上箭头 SVG 与 `@decky/ui` 的 `updaterFieldClasses.UpdaterFieldStatusSuccess` 原生成功状态类；更新标记不替代插件自身图标，不增加焦点。状态文字着色独立于插件名称、版本、插件图标及来源。
- `lastCheck` 保存最近任意检查，`lastScheduledCheck` 仅由定时检查覆盖。新报告含 `source=manual|scheduled`，旧报告缺来源时保留未知；报告含 `checkedAt`、`items`，整轮失败另有顶层 `error` 并保存为空 items，不能沿用旧成功。手动检查不覆盖最近定时结果，也不发送定时通知；调度间隔仍依据 `lastCheck`。
- `checkNotifications` 是独立布尔偏好，新配置与旧配置缺字段默认 true，非法非布尔值恢复 true。`set_check_notifications(enabled:boolean)` 严格校验类型，只改通知偏好并返回 AppSettings；关闭自动检查保留此偏好，关闭通知不停止检查、结果保存或状态同步。
- 每轮定时完成只发送一次 `scheduled_check_completed`，参数为 `{report: CheckReport, checkNotifications: boolean}`，偏好取检查结束时最新配置。报告先持久化，再发事件；事件投递失败不得重写为检查失败。`src/index.tsx` 在插件生命周期监听与卸载清理，QAM 未挂载也可通知；开启时每轮一条更新/无需更新/失败/未关联/无法确认汇总，无更新也通知，无包/无发布/不可比较不能称为已最新。
- `checkController.ts` 接收定时结果后发布共享报告并排队读取当前列表，失效旧列表请求，不回填旧绑定、不清除已有手动检查错误。设置只显示一个“最近定时检查”可点击原生行，时间简写，摘要只含非零可更新/失败/无法确认计数，不重复手动报告、零值和逐项仓库信息。手动结果留在首页；报告整体失败时摘要仅提示查看原因，完整错误保留在结果页。`CheckResults.tsx` 独立显示完整结果，首次进入按更新、错误、无法确认、正常、未关联排序；会话内刷新保留行位置和稳定 key。列表只显示名称/状态和短更新版本，仓库、长版本与错误放到记录详情。结果行按更新/错误/无法确认/无需更新/未关联使用箭头/警告/问号/对勾/链接装饰 SVG；只有当前身份匹配的更新图标使用既有成功类，状态文字另按语义着色。名称单行省略但完整可访问名称与详情数据不丢，右侧状态不换行且对齐，只有更新显示必要短版本。与当前名称/仓库/版本不匹配的记录使用历史图标，不显示更新箭头或更新色，打开只读历史记录，不触发版本操作。结果详情返回结果页，结果页返回设置并恢复摘要焦点；选择与历史记录跨 QAM 保留。重开读取持久结果；事件只更新报告，不覆盖保存中的设置。通知开关保持后端已确认值，保存失败显示错误；等待关闭保存时立即抑制通知，成功关闭后拒绝迟到的开启偏好事件，失败则恢复已确认策略。
- 定时检查仅检查和通知，不自动安装。
- GitHub 限流必须展示可重试原因及响应头提供的恢复时间；普通 403 不得一律判为限流。Loader 的 Python 异常可能有空 `message`，`components.tsx` 须从 `pythonTraceback` 提取末行原因，不能把空字符串当作没有错误，也不向界面输出完整调用栈。

### GitHub 传输

- `github_transport.py` 为搜索、Release、tree/blob、raw 登记数据和 ZIP 提供同一个受限 HTTPS 传输。设置 `networkMode` 只接受 direct、auto、gh-proxy；旧配置和新安装默认 direct。固定备用 origin 为 `https://gh-proxy.org`，不接收任意 host，不修改系统代理或 TLS 校验，不发送 Authorization、Cookie 或令牌。
- auto 先直连，只有连接/DNS/超时/TLS 网络异常、5xx、429 或明确 `X-RateLimit-Remaining=0` 的 403 可备用一次。401、普通 403、404、格式/长度/大小/身份校验失败不触发备用。原始 URL 保持 canonical GitHub HTTPS；代理不能扩大初始 Release ZIP allowlist。
- 每一跳和最终响应均校验精确 API/raw/Release CDN 主机，最多 5 次跳转；代理只允许固定 origin 加合法内嵌 GitHub URL。HTTP、userinfo、额外端口、lookalike、嵌套外站/代理与任意外站跳转均拒绝。JSON/raw 默认响应上限 8 MiB、socket 等待上限 20 秒、绝对单调时钟总预算 60 秒，探查另保留自己的预算。受控 HTTPSConnection/HTTPSHandler 与底层 guarded readinto 覆盖状态行、响应头、chunk-size、trailer 和正文；同一个预算跨每跳重定向和备用尝试，不能因持续小量输入重置。watchdog 在超时或取消时 shutdown 连接，真实下载 worker 退出后卸载才返回，不能遗弃后台写入。
- DNS 只解析白名单主机的 443 端口，等待受同一 deadline/cancel 控制，预解析 IP 不改变 TLS SNI 和证书验证的原始主机。系统 getaddrinfo 不可中断，进程最多 4 个仅解析的 daemon 调用可晚到完成，不持连接、不写文件或任务状态；槽位仅在底层调用真正结束后释放，满额请求失败，晚到结果丢弃。
- 主设置保留检查开关、间隔、独立通知、唯一最近定时摘要、网络入口及唯一诊断内页入口；入口显示配置模式和最近使用通道；历史不保证当前连通的说明留在网络页。`NetworkSettings.tsx` 二级页在模式控件之前默认显示完整第三方风险，并展示最近实际查询通道/时间，下载通道以任务记录为准，不能把模式当实际通道。页面与网络选择/保存意图跨 QAM 保留；保存使旧读取失效，重挂不重放保存，保存期间禁止重复写入，失败显示错误并恢复已确认模式。网络返回设置恢复网络行焦点，检查结果返回仍恢复摘要焦点；两行在设置异步加载前挂载。安装准备保存开始时的模式，下载记录每次真实通道与尝试次数。第三方可见公开查询与内容，也可修改返回；TLS、ZIP 结构/身份校验不证明作者或代码可信。

### 安装准备与安全边界

- `install_ops.py` 只下载经 Release 回查确认的 GitHub HTTPS 资产，限制初始地址和重定向主机。
- 下载以 64 KiB 分块读取，每约 0.25 秒报告实际 received、total 或 null、connecting/downloading/complete/failed、route 和 attempt。Content-Length 必须合法、有界且与最终字节数一致；未知总量只展示已下载 MiB，不制造百分比。网络失败切备用时归零并截断本次独占创建的文件，绝不追加混合两次响应或覆盖预存在的文件；失败保留最后实际值，3 分钟总预算跨尝试共享。
- `install_flow.py` 的线程回调只能经 loop.call_soon_threadsafe 在事件循环更新持久化状态，校验任务 id、阶段与停止标志；用户取消或卸载先通知下载停止，并等待所有已启动的下载、Release查询、ZIP检查、stamp和备份线程退出；取消不使用task.cancel遗留to_thread工作。迟到回调核对任务ID与cancel_requested，不得写终态/新任务。准备被重载中断时下载停止，持久取消意图恢复为cancelled，否则为failed，未提交Loader。
- `OperationPanel.tsx` 使用原生 Field 与 ProgressBar，nProgress 范围 0 至 100；未知长度活动下载显式 nProgress=0、indeterminate=true，失败或完成后停止 indeterminate。进度条和字段不获取焦点，nTransitionSec=0.2；字节/百分比与真实通道/尝试次数在 Field 描述中分行，childrenLayout=below，避免窄面板长操作文字横向溢出；不伪造剩余时间。下载完成不等于安装成功。下载界面不介绍任务连接模式，实际通道/次数仍保留，网络页偏好和风险不变。旧任务缺少 download 字段仍可读取。
- ZIP 必须单一合法顶层目录，含有效 plugin.json/package.json 和 dist/index.js；禁止路径逃逸、重复/冲突路径、符号链接、特殊文件、加密条目与超限内容。
- 名称/目录必须匹配更新对象，禁止覆盖本插件或把新装变成静默覆盖；缺少 LICENSE、声明 root 的包以警告要求显式继续。
- 覆盖前备份 Decky 标准 settings/data 目录，记录路径、文件数、大小和哈希；权限失败、符号链接、超限或复制期间观察到的数据变化必须阻止替换。
- 备份不承诺运行中应用的一致性，不覆盖插件任意自定义路径，不自动回滚。缺失标准目录记录为空范围，不称为包含全部数据。
- 原插件关键文件、准备包和备份在提交前再次核验。包与备份只放本插件运行数据目录，不由本插件解压或删除其他插件目录。
- 下载 128 MiB、ZIP 展开与备份各 512 MiB、每包/备份 10000 文件、任务数据预算 2 GiB。产物与备份保留供人工恢复，不自动删除。

### 提交与结果

- `install_flow.py` 保存唯一当前任务；候选状态写盘成功后才替换内存。普通安装仅 ready 可领取一次提交，提交前先持久化 awaiting。重载不得重放已领取的请求。
- `mode=reinstall` 点击后先由原生确认是否开始下载核验和标准数据备份，取消/B/普通关闭/popout关闭均不prepare。首次文案明确尚未备份。完成包准备和标准数据备份后，再打开原生 `ConfirmModal` 告知真实备份范围、卸载钩子风险、自定义路径不在备份内且不自动恢复/回滚。确认后 `claim_uninstall` 复核原插件、ZIP 与备份并持久化 uninstalling，只领取一次；前端再记录精确任务的uninstall_started授权，最终同步检查任务ID、generation和本地取消阻止标记后才调用原生 `utilities/uninstall_plugin`；claim不等于Native已发出。RPC resolve（包括 None）不等于卸载成功，`complete_uninstall` 必须确认目标程序目录及同名插件缺席，复核 ZIP/备份后持久化 awaiting，再提交一次原生安装确认。取消、失败、120 秒超时或重载不继续、不重放；标准数据不主动删除。
- `src/installController.ts` 的安装意图独立于 QAM 挂载；新点击触发的准备可继续到原生确认，重载恢复的 ready 必须显式继续。有包警告时亦需显式继续。取消后的补充complete仅用于尚未完成的卸载回查；该回查返回后，安装RPC的迟到拒绝不得返回卸载阶段。原生prompt到达与安装RPC返回是不同边界，不能据已收到prompt推定原RPC已结束。
- `loaderBridge.ts` 仅把已预检的本地 `file://` 包、包内真实身份/版本与 SHA-256 交给 `utilities/install_plugin`，不调用自动确认。不把请求返回当作成功。
- `file://` 后保留原始绝对路径，符合 Loader 直接切掉前缀后打开文件的契约。
- 事件使用原生 `loader/add_plugin_install_prompt`、`loader/plugin_download_start`、`loader/plugin_download_finish`。提示以名称与哈希关联 request_id；开始/完成必须匹配当前插件。
- 完成证据要求提示名称/哈希/request_id 关联、开始与完成事件，以及包内全文件/身份哈希与 changed_stamp 联合核验；同版重装不能以旧文件原本匹配作为成功证据。同名冲突请求、缺事件或 Loader 额外依赖失败不得用纯哈希或已加载后端兜底判成功。只称“安装文件已核验”，不宣称目标插件功能正常。
- verified 仍是活动阶段，继续轮询并阻止目标关联修改。`get_operation` 先幂等持久化仓库关联，再保存 `closed`、`binding_saved=true`、`outcome=success`、`close_reason=verified` 和 `completed_at`，返回最新快照，成功后自动结束任务。关联或任务写入失败不得提前标 bound/closed，只重试收尾，不重放安装；备份保留。手动结束记录 `close_reason=manual`，不能冒充成功；旧 closed 任务不得迁移为成功。
- 前端在事件到达时捕获任务 id，顺序回写证据，不因稍后的读取失败或新同名任务错投。controller 首次状态读取成功前，按当前 generation 缓冲事件；读取失败保留缓冲，首次成功只关联该次读取确认的任务，未匹配的事件丢弃。创建新任务或 controller 销毁会丢弃未关联缓冲，不得把旧事件投给下一同名任务；成功 closed 结果仍可查看，不显示手动结束按钮。
- 请求异常、事件丢失、取消不可确认、文件不匹配保持 unknown，不自动重试。取消未知或已开始的原生副作用不能通过结束跟踪放行新任务；旧普通unknown手动结束能力不冒充取消。

### 取消与任务提示

- `cancel_operation(identifier)` 只停止精确当前ID，不依赖商店当前资格。先持久cancel_requested，再置下载Event；失败不假成功。checking/backup和validating保留活动阶段，等真实worker结束才cancelled。验证锁不能阻止取消意图及时落盘，claim/submit在worker返回后再次检查意图；取消等待响应收齐前新prepare也不能替换任务。
- ready直接取消；验证中取消不授权后续Native。前端点击先同步阻止automatic/dispatch，并对重复请求、所有await后的任务ID及generation检查。开始准备和破坏性确认都由控制器持有单意图，QAM卸载不重放，重载后的迟到确认无效。
- uninstalling未发出Native时取消阻止卸载；已记录started但前端尚未调用时，uninstall_not_started仅收齐未发副作用。实际卸载已经发出时，取消只承诺停止后续安装并等待Decky结果，不恢复旧插件或数据。complete_uninstall在持久取消后只做结果/目录回查，不再授权安装；结果未知保留unknown/uninstall_blocked与冲突资格。实际RPC已返回后的缺席回查可重试，超时/断链不以目录暂时消失冒充实际调用已完成。取消卸载完成回查和status未知结果回查均登记持锁的完整核验协程，直到最终状态持久化才释放；unload等待该协程而非仅等待底层线程。停止后complete回查拒绝进入，status只返回内存快照，不启动核验或写盘，旧实例不能在重载的新任务之后落盘。
- awaiting只有已关联request_id且未saw_start时才能精确调用utilities/cancel_plugin_install。先保存取消意图，Native成功后记录cancelled，失败记录cancel_unknown并保持unknown，不重放。安装提交后不得通过取消卸载回查收尾：complete和status的卸载回查共享权威门槛，仅接受reinstall的uninstalling/unknown或幂等cancelled阶段，且不存在submitted_at、request_id、saw_start或saw_finish安装证据；拒绝时不核验文件、不改状态和证据，目录缺席不能证明安装取消。安装调用已发但request未知、installing/verified及unknown的取消按钮禁用，明确原因；start/finish及核验/绑定收尾持续进行，取消不得吞掉已开始安装的证据。
- 前端在submit返回前已点击取消、而Native安装尚未调用时，dispatch_not_started只记录本地未发出的请求并终止该任务，不调用Native cancel。重载的cancel_requested ready/准备阶段不会再提交；遗留awaiting取消意图恢复unknown，不重放。
- 无operation不显示任务横栏。活动任务可查看且不能关闭风险提示。failed/cancelled/closed终态可通过dismiss_operation(identifier)原子写notice_dismissed=true，只隐藏此ID的横栏，不close、不取消、不改错误/outcome/binding/证据/备份/归属。旧记录缺字段默认未关闭，旧closed不变成功。设置保留“查看最近安装任务”，新ID正常显示。迟到轮询/事件/关闭响应不能恢复已关闭提示或覆盖新任务。
- `OperationPanel.tsx` 展示阶段、真实备份范围、包警告、失败与未知，不能使用虚构百分比。

### API 与展示参与者

- `api.ts` / `types.ts` 镜像 `main.py` 的 RPC 和状态。
- `viewModel.ts` 集中解析版本操作、单资产默认值和检查描述。
- `ReleasePicker.tsx` 统一详情与新装的版本/安装包/发布说明展示。
- `SettingsTab.tsx` 展示检查设置、通知、紧凑定时结果、网络、诊断内页与最近安装任务入口，原生标题/正文/描述分层，组间分组、组内减少不必要分隔。
- `NetworkSettings.tsx` 展示连接模式、默认全文第三方风险与实际通道记录，仅复用现有设置 API。
- `CheckResults.tsx` 展示简短结果行与完整历史记录，按当前插件身份决定能否进入版本管理；历史详情显式使用原生次级状态色，保留原状态文字、时间及完整错误而不占用设置页。
- `uiIcons.ts` 集中维护操作与 SVG 图标映射，`Action` 必须指定操作类型。使用现有 react-icons 内联 SVG，继承 Steam 主题颜色；装饰图标使用 aria-hidden、focusable=false，不添加独立焦点或替代文字标签。Status loading及主/次级Action loading都注入真实旋转动画和aria-busy，状态结束卸载动画，prefers-reduced-motion关闭旋转；详情真实Release读取和下载连接期间使用同一状态。首页次级入口与全局任务导航保持单个原生 Field 焦点，其他执行操作保持单个原生满宽按钮，输入、下拉和开关保持原生结构。
- 操作旁不显示 info 按钮，不提供额外说明弹窗或帮助焦点状态。插件行仍是单焦点，既有的风险提示、错误信息、备份范围和发布说明保留。
- `components.tsx` 使用状态文本与原生动作控件，不把禁用按钮当状态标签。仅状态 span 使用动态 `findClassByName("ErrorText")` 红色、`findClassByName("Yellow")` 黄色或 `updaterFieldClasses.UpdaterFieldStatusSuccess` 绿色；失败为红，无法确认/无包/无发布/不可比较为黄，更新/最新/本机更高及任务成功为绿，未关联/历史等保留原生次级色。插件名、版本不着状态色，不硬编码原生 class hash/RGB，不增加 info 控件。

## Implementation Freedom

- 页面文件布局、局部间距和分组标题可以变化，但须保留原生焦点语义、信息层级和风险状态。
- 安装执行者可以改成将来正式提供的 Loader API，必须保留用户确认、一次性提交和独立完成证据。
- Workaround semantics: 未公开 Loader 全局接口可在正式 API 提供等价能力后替换。

## Non-Break Constraints

- 保留 `py_modules` 沙箱模块布局、系统 CA 和 GitHub/商店 Accept 差异，不关闭 TLS。
- 不自动升级、不卸载本插件、不为备份自动提权、不修改 Loader 全局调用实现。
- 任意异步结果不能更换当前任务身份；旧任务状态不得覆盖新任务。
- 备份数据与操作产物属于运行数据，不进入源码提交。

## Coupling

- `tests/test_github.py` / `test_scan.py` / `test_persist.py` / `test_http.py`：解析、分类、旧绑定与 TLS 语义。
- `tests/test_repo_probe.py`：根目录、monorepo、frontend-only、工作区、非插件、损坏/截断/符号链接与探查预算。
- `tests/test_ownership.py`：旧绑定/报告无授权、持久意图/保存失败、冻结与回查分离、保留原冻结、无关名单、未知/非商店修复、同名/目录变化/自身、活动目标、旧 ready 与提交/卸载旁路、合法目录缺席、成功绑定不覆盖 ledger、迟到检查、损坏及 symlink/超限读取；257字符仓库、无效名称/目录及实际序列化超限写前拒绝、ledger字节不变且仍可交还；不同仓库的准备/旧ready提交/卸载领取/目录缺席后继续拒绝，同一规范仓库更新与重装通过。
- `tests/test_management.py`：定时各类结果/整轮失败、结束时通知偏好、投递失败不篡改结果、独立通知 RPC 与手动不覆盖定时；探查缓存和新装后端强制门槛、仓库搜索查询/返回契约、输入和结果边界、停追踪、报告持久化、失败与新旧版本判定。
- `tests/test_transport.py` / `test_download.py` / `test_catalog.py`：三模式、每跳/最终 URL 拒绝、可重试状态、实际字节进度、未知长度、短读/超限、备用重置、取消、目录损坏/重复 section/陈旧缓存与登记证据。
- `tests/test_install_flow.py` 的task52回归覆盖取消持久失败/重载/旧ID、新任务互斥、真实stream取消回收、Release/inspect/backup线程等待、claim/submit验证锁中取消、Native卸载前后/unknown/目录回查、原生开始后收尾、提示关闭只写metadata及完整证据保留。complete和status两条取消卸载回查分别暂停真实哈希线程与核验后的最终落盘，确认unload全程等待；重载后新ID文件不被旧实例改写，停止实例不能再启动回查。安装提交/request/start/finish证据分别阻止迟到卸载complete且不写盘、不启动文件核验；取消未知保持冲突资格，安装start/finish和真实文件核验后仍能成功收尾，status不能用残留卸载结果标记改判安装已取消。
- `tests/test_install_ops.py` / `test_install_flow.py`：ZIP、备份、身份保护、一次性提交、事件与磁盘联合证据、关联/任务写失败与幂等自动收尾、legacy closed 不迁移；显式重装领取/取消/目录缺席/篡改/超时/重载及标准数据保留，以及事件循环进度写入、旧 id 隔离、真实 HTTPResponse 滴流与 DNS 挂起时的卸载、下载 worker 退出及无迟到落盘。
- `tests/viewModel.test.ts` / `loaderBridge.test.ts`：选择和 Loader 参数契约、卸载领取门槛及超时不能作为完成。
- `tests/ui.test.tsx` 的task52回归覆盖动画/secondary/reduced-motion、详情busy、两阶段确认取消/B/关闭/重复/重挂/迟到、Native边界取消、持久失败显式回查、无task/活动不可隐藏/终态精确ID关闭与旧响应隔离、诊断内页返回一次焦点；重装安装RPC延迟返回期间收到prompt、取消结果未知后原RPC拒绝，不得再次complete卸载，任务保持活动；真正未完成的卸载回查因取消失败仍可补充回查。
- `tests/ui.test.tsx` 的安装生命周期回归：普通安装与显式重装分开、原生确认取消与 dismiss、领取失败/RPC失败/目录回查失败、QAM重挂不重放、controller重载不继续、verified继续轮询、成功自动结束及结果入口、快速事件直接回写与旧任务隔离、首次读取延迟/失败时事件缓冲、缓冲固定初始任务及空任务/新建任务/controller销毁隔离、历史详情状态保留次级色与原文、状态span原生颜色、首次原生焦点消费恢复后按可见顺序进入。
- `tests/ui.test.tsx`：插件自身图标/原样式、装饰inert/不增加焦点、缺失/损坏/重复/禁用/歧义回退、异常渲染隔离及替换恢复、Native-only不虚构身份、插件图标与身份匹配更新箭头并存；匹配更新图标与主题色/单焦点、关闭 QAM 通知、监听清理、保存期间抑制迟到通知、定时实时报告与当前绑定刷新；SVG装饰可访问性、每项操作单按钮与无额外说明控件、既有任务不被改写；网络分离与风险全文/模式默认值、网络返回焦点/QAM保存意图/迟到读取/失败重试、结果状态图标/长名称可访问性/首页单主按钮；模拟原生组件的页面交互、新装搜索下拉/统一探查/monorepo显式选择与身份跳转、搜索后显式保存、过期响应隔离、QAM 重挂载、检查期间改绑/解除后的排队刷新、关闭后检查失败与分源错误保留、B 键与控制器重载行为。
- `tests/test_local_catalog.py`：默认读取/更新检查/授权零商店网络、显式刷新一次、30天及重载仍保留关系、旧目录缺席不授权新身份、目录/包名/仓库冲突、显式重新绑定、解除优先、旧商店与旧绑定不接管、活动任务/保存失败/损坏/链接/超限、真实刷新worker卸载等待/外层RPC取消/停止后排队不写盘。
- `tests/ui.test.tsx` / `tests/apiFacade.test.ts` 的task53回归：单一原生icon和可访问名称、无常驻来源/日期、默认无true刷新参数、显式点击与跨QAM合并、检查排队/错误隔离、历史商店隐藏与显式接管、记忆非商店仍可管理。
- `package.json` / `pnpm-lock.yaml`：类型和测试工具；`README.md` / `HANDOFF.md` / `CHANGELOG.md`：使用边界、版本与交接。
- Merge-time actions: 重新核对 Loader RPC、事件参数及 Decky 原生控件声明，再保留本需求语义。

- `tests/ui.test.tsx` 另验证商店默认隐藏与独立接管、无授权详情和新装匹配、显式仓库确认、次级说明、原生顺序及无关名单/原冻结、失败/超时/重挂/卸载/迟到回调不激活、交还代替 unlink、报告同意身份及独立卸载授权；`tests/apiFacade.test.ts` 验证接管 DTO 边界，`tests/loaderBridge.test.ts` 验证原生冻结服务接收者与禁止猜测设置 RPC。
- `tests/loaderManagement.test.ts` 验证纯合并、歧义、失败、空快照、来源与版本隔离；`tests/apiFacade.test.ts` 在独立运行时验证列表/手动检查的实际 RPC 与错误传播；`tests/loaderBridge.test.ts` 验证原生只读 route、超时与迟到隔离；`tests/ui.test.tsx` 验证只读详情、新装跳转与稳定焦点。

## Validation

- `pnpm test:python`：后端功能与安全回归。
- `pnpm test`：前端逻辑与模拟控件交互。
- `pnpm typecheck` / `pnpm build`：类型与实际打包。
- `git diff --check`：补丁格式。
- Enforcement gap: 没有语义索引自动校验器；仍须主代理人工核对路径与角色。模拟控件不证明真实 QAM 布局、手柄、软键盘及当前设备 Loader 兼容性，须真机验收。
