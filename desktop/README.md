# Crew V2 桌面版

V2 的独立默认 HOME 是 `~/.crew-v2`，应用和快捷方式名称为“Crew V2”。不会在 V1 HOME 中启动或更新，源码更新只检查 `rebuild/v2`。顶部全局助手跨工作区保持同一会话；工作区主页展示任务、讨论、拍板、人工 TODO 与成果。快速想法可先本地保存，再显式开始聊。设置中的“行为配置”提供作用域、版本和实际发送记录。

Windows Codex 桌面接续通过交接与返回按钮轮流使用同一原生对话。Crew 打开专用的官方桌面窗口；点“回到 Crew”时窗口可以保持打开，结束后也能再次交出。已有普通窗口的旧交接需先释放其连接并接回，才能进入新路径。真实两次往返、工具验证和官方标签历史缓存的显示边界见 [0.3.3 接续说明](../docs/v2/NATIVE_DESKTOP_0.3.3_2026-10-30.md)。V2 与 V1 并存，无需先导入旧数据。

以下版本段落保留为基线演进记录，不代表本次新版本的验证结果。

原生 Electron 窗口、工作区首页和系统托盘。需要本机已有 Python 3，以及实际要使用的 Claude Code / Codex；普通工作文件夹无需 Git，后端继续使用 Python 标准库。

Windows 构建还需 .NET Framework 4 自带的 C# 编译器。`npm run build` 与 `npm run build:dir` 会先编译 `native/CrewCodexBridge.cs`，找不到 `csc.exe` 或编译失败会中止构建。生成的 `assets/CrewCodexBridge.exe` 不提交 Git，打包时放在 `app.asar.unpacked/assets/`，通过后台环境传给原生桌面接续功能。开发模式可先运行 `npm --prefix desktop run build:codex-bridge`。

0.2.40 修复普通 spawn 开工后又被目标分配重复启动成员的问题：自动派工遇到仍忙的成员总对话会明确返回冲突；`crew spawn --goal <ID>` 可直接创建并启动一个目标执行，反向重复唤醒也会拒绝。原生线程绑定跨总对话和目标统一检查唯一归属，并补齐并发回归及 GitHub Actions 自动测试。已有独立目标与用户明确发起的总对话继续保留各自历史。

0.2.39 调整对话工具栏：模型、上下文与信任放在一起，待处理、任务资料、架构相邻，更多操作置于末尾；统一控件高度，长名称可省略，窄列收短文案并保留菜单入口。上下文环的普通悬停不再使用绿色边框，避免压缩结束后看起来仍被按下；压缩中的提示与键盘焦点仍保留。隔离原生交互覆盖 CC / Codex 压缩结束后鼠标停留、键盘 Tab、长名称、窄窗、大字号及 leader / 成员 / 任务工具栏。

0.2.38 修复正式后台未注册右侧面板样式、导致待处理按钮亮起却看不到浮卡的问题。入口页引用的全部脚本和样式均通过真实 HTTP 路由回归；隔离 Electron 交互另外核对实际后台的待处理展开、下推架构及反向收起，并可用 `CREW_TEST_PLUGIN_ROOT` 指向构建产物中的 backend 复验。

0.2.37 按确认稿实现右侧推移动效：架构单独打开时占满右侧单列，保留原来的成员信息和子代理详情；待处理从头部按钮下方展开，向下推移架构，收起时反向回收。任务资料、需求、交付阅读层均高于辅助卡片。按钮、任务切换与资料开合共享动效，并响应系统“减少动态效果”；开合保留输入法、草稿和滚动位置。

0.2.35 将任务、待处理和架构图放入右侧同一浮卡，三块可同时显示、分别收起和滚动。架构图不再常驻占列；进入任务后顶部仅留返回、状态、标题和换任务，操作、需求、上下文、交付结论移入任务区。资料和完整报告关闭后回到原入口，保留备注、中文输入、草稿及阅读位置。隔离验收：`node plugin/tests/smoke_attention_popover.cjs`（仓库根目录）。

0.2.34 修复打开已有工作区时的工具调用历史迁移错误：重复工具调用和消息修订分别保留正确的消息归属，调用结果随关联变化追加历史版本。已有记录保留，迁移继续执行外键完整性检查。遇到 `Migrated foreign key violation` 的旧版用户，退出旧 Crew 后启动新版，再打开原工作区；无需删除项目或数据目录。

0.2.32 补齐总对话与任务并行时的协作路由。任务中可联系别组 leader，由对方总对话协调；明确回复按原消息回到原任务或总对话。首次总对话与已有任务可共存，停止、等待、失效和原生回执未知分别处理，不盲目重发或误唤醒暂停任务。CC / Codex 共用这套消息归属；外部 Claude 的总对话通过下一次原生 Hook 接收，不另开副本。

隔离交互验收可运行 `node plugin/tests/smoke_conversation_routing.cjs`（仓库根目录），使用真实页面、临时 HTTP 服务和假载体，检查多个任务与总对话并行、跨 leader 请求和原消息答复，以及切换后的草稿和进度状态；结果保存到 `desktop/smoke-output/conversation-routing/`。

0.2.28 修复会话已经结束或转交执行后仍显示“等模型回应”的问题：结束记录立即收起等待进度，同一条旧状态快照不能重新点亮它；新的实际工作照常显示。总会话、任务及 CC / Codex 使用同一套规则。

```powershell
cd desktop
$env:electron_config_cache = Join-Path (Get-Location) '.cache/electron'
$env:ELECTRON_CACHE = $env:electron_config_cache
$env:ELECTRON_BUILDER_CACHE = Join-Path (Get-Location) '.cache/electron-builder'
npm install
npm start
```

首页可用系统文件夹对话框打开普通目录；已有 Git 项目的子目录仍会解析到真实仓库根目录，已登记的工作区优先复用。新建工作区只创建空文件夹，不自动初始化 Git，已有目录不会被覆盖。工作区注册到 Crew 后，在原生窗口打开对应项目。分支、提交和独立 worktree 等功能仅在 Git 仓库中使用。

项目的 Git 页右上角有常驻“Git 设置”，加载或读取失败时也能打开。可绑定已有 leader、设置主干与稳定分支、编辑合并规则；保存不唤醒 AI，职责变化随下次正常对话传达。停用仅解除绑定，不关闭原来的组。 Git 页会保留本工作区标签内的快照和阅读位置，切回来直接显示，不再每 30 秒扫描；顶部显示读取时间，按需点“更新视图”或“拉取远端”。设置或目录变更会使旧快照失效，关闭标签或刷新网页后重新读取。

Git 的中间状态只更新台账，普通成功留待阅，不额外开启 AI 对话。请求方确有合入后的工作才标记 `--blocking` / `data.blocking=true`，成功后与拒绝阻塞按原身份接续。旧的未标记结果保留记录，不主动唤醒；用户答复和真实阻塞照常投递。

每个工作区有独立标签页，切换时保留聊天草稿、当前 Leader、滚动和页面状态。重复打开同一工作区会切到已有标签。`Ctrl+T` 返回工作区选择，`Ctrl+Tab` / `Ctrl+Shift+Tab` 切换标签；`Ctrl+W` 关闭当前项目标签，不停止该工作区的 AI。只剩首页时 `Ctrl+W` 隐藏窗口。菜单还提供 `Ctrl+N` 新建、`Ctrl+O` 打开、`Alt+Left` 返回以及刷新当前标签。

组内关系树的“添加”菜单可直接新建组员，也保留收编入口。设置页“AI 大脑 → leader 召唤组员”配置全局默认；每组标题下的“组员大脑”可单独覆盖，未设置时跟随全局规则，最终跟随所属 leader。手动新建会预填生效的大脑，也可临时改选；填写任务并点击“创建并开始”才执行。leader 与组员改名统一使用带身份说明的弹窗，失败保留输入，工作区改名仍在工作区设置。

首页每个工作区右侧的设置按钮、当前工作区菜单和项目左上角均可打开“工作区设置”。修改显示名不改变内部身份。更改目录先停止现有运行、暂停未完成目标，再将已有 leader 和成员的执行目录一起更新；聊天与目标历史保留，不搬磁盘文件，不自动开始任务。新目录需要事先存在，原工作区下的相对子目录也需要在新位置存在。

“从 Crew 移除”先停止该工作区执行，再从列表移除登记，保留磁盘文件和全部记录。以后重新打开同一目录会恢复原工作区。

打开工作区时可选择升级旧历史或只读查看，启动应用不会自动扫描并导入所有旧目录。首页和工作区菜单可导出完整 `.crewpack`；导入时选择恢复原身份的只读历史或复制为独立工作区，再明确绑定本机目录。导入不恢复旧电脑的执行权限，也不自动启动 AI。历史页可查看消息、附件、报告与保存的文件；项目资料统一从任务内的“交付与文档”进入，不设置侧栏文档总览。详见[本地历史与接续](../docs/storage-v2/implementation.md)。

窗口和托盘会标明“独立数据”或“隔离测试”，便于区分使用的 HOME。历史库接口与“存储与清理”独立；后者继续提供占用、清理预览、回收与恢复。完整历史页隔离验收：`node desktop/scripts/smoke-history.cjs`。

“设置 → 消息渠道”可同时配置 QQ 与 Lark / 飞书。按页面步骤创建机器人、保存凭证、建立连接，再用自己的账号私聊机器人发送绑定码；无需编辑 JSON 或重启 Crew。页面提供重连、断开、重新绑定与测试通知，已保存的 Secret 不回显。Lark 当前支持私聊文字和引用拍板答复，完整权限与事件订阅说明见[消息渠道连接流程](../docs/REMOTE_CHANNELS.md)。

关闭窗口会隐藏到托盘，AI 和服务继续运行；托盘或菜单中的「退出 Crew」才退出应用。窗口保留原生最小化、最大化、关闭与可拖动标题栏；应用菜单收在标题栏的省略号中。Crew 图标用于窗口、任务栏、托盘、安装器和快捷方式。

真正退出后，托盘、菜单和已排队的异步界面操作不再访问窗口。只读版本检查及独立的要点卡整理会取消并回收自有进程；各普通会话和目标执行按原有归档与进程期限并行收尾。已开始的安装、更新、目录迁移仍等待完成，借用后台继续由原窗口管理。

首页侧栏和应用菜单中的「工具与更新」显示 Claude Code / Codex 的当前版本、最新版本和更新结果。自动更新默认关闭；正常启动后会检查版本，手动点击可再次检查。正在使用某个工具的会话会让更新排队，空闲后再试，也可以取消排队。自动更新需用户主动开启；无法确定安装来源或运行状态时会保留说明，不强行替换。更新失败可查看本地日志；退出应用会等待已开始的更新完成。

0.1.2 另有 Crew 卡片：手动检查并统一到**当前桌面自带版本**，确认空闲后备份、同步已安装插件、重启自有后台并在原端口重连，标签与草稿保留。它不下载互联网新版，也不受 CLI 自动更新开关控制；借用或不支持更新准备的旧后台会说明需先退出原 Crew。已有 Claude 会话需重载插件或重开。流程与限制见 [Crew 更新说明](../docs/DESKTOP_UPDATES.md)。

启动时探测指定 home 的 `web.json`，健康的 Crew 服务直接复用。退出应用不关闭复用的服务。只有登记不存在或连接被明确拒绝时，桌面版才启动自己的 `plugin/crew_desktop.py`，使用系统分配的端口，并通过专用 stdin 通道优雅关闭自己创建的后端。无法判断的超时、HTTP 错误或不兼容服务会明确报错。同一 home 的后台持有 OS 文件锁；多个界面同时首启时只有一个创建服务，另一个等待后复用。不会为了应用启动而重启当前服务。原有服务的版本由原服务决定。

已有桌面版正在托管任务时，可以用独立界面配置显式连接它：

```powershell
$crewVersion = (Get-Content package.json -Raw | ConvertFrom-Json).version
& ".\dist\Crew-V2-$crewVersion-portable.exe" --home C:\Temp\crew-private --service-url=http://127.0.0.1:7122 --profile ui-private
```

显式地址必须是健康的本机 Crew 服务，失败直接退出，不另起后台。界面默认数据位于 `home/desktop/ui`，`--profile` 使用 `home/desktop/profiles/<安全名称>`，不会争抢旧版界面的实例锁。后台源码仍位于稳定的 `home/desktop/backend`。菜单「查看日志文件夹」可打开本地 `home/desktop/logs`，其中保留后台最近的 stderr 和启动错误。

「设置 → 存储与清理」使用实际连接的后台 HOME 展示占用和清理预览。旧备份、未引用图片与过期默认导出包移入回收站后保留 7 天，恢复遇到新同名文件时不会覆盖；移动到回收站不会算作已释放空间。自动维护仅清理过期启动日志与到期回收站内容。工作区菜单中的归档清理继续整理工作状态，保留全部历史。保留规则见 [存储管理](../docs/STORAGE_MANAGEMENT.md)，完整隔离交互验收执行 `node desktop/scripts/smoke-storage.cjs`（在仓库根目录）。

Windows 命令行中的服务地址使用上例的 `--service-url=地址` 形式。Electron 会为防止协议启动参数注入而拒绝「独立 URL 参数后还有其他参数」的命令；这发生在应用代码加载之前。也可以在所有应用参数前加 `--`。该行为依据 [Electron 原生参数检查](https://github.com/electron/electron/blob/main/shell/app/command_line_args.cc)，不会通过关闭它来处理。

```powershell
npm start -- --home C:\Temp\crew-desktop-private --port 0
npm test
npm run build
```

构建版本读取本目录 `package.json`，产物包括 `dist/Crew-V2-<version>-win-x64.zip`、`dist/Crew-V2-<version>-portable.exe`、`dist/Crew-V2-<version>-setup.exe`。ZIP 解压一次后直接运行目录中的 `Crew V2.exe`，日常启动无需重复解包；portable 是每次启动时临时解包的单文件程序；setup 是当前用户安装器，提供开始菜单、桌面快捷方式和卸载入口。`dist/win-unpacked/Crew V2.exe` 是同版未压缩目录，日常使用建议 ZIP 目录版或安装版。安装器与应用未做代码签名；EXE 原生图标资源仍正常嵌入。后端源码随应用放在 `resources/backend`，打包版会将后端按内容哈希保留到所选 home 的 `desktop/backend`，保证 portable 解包目录清理后 CLI 和守卫路径仍有效；Python 与 Git 使用本机安装。

验收新版本且保留正在运行的正式实例时，使用独立数据目录。此入口不会复制正式会话、QQ 凭据或自动恢复旧任务：

```powershell
$crewVersion = (Get-Content package.json -Raw | ConvertFrom-Json).version
& ".\dist\Crew-V2-$crewVersion-portable.exe" --home C:\Temp\crew-acceptance --port 0 --isolated-sources
```

“设置 → 后台与运维”可以直接和运维 AI 聊天、查看状态、保存自动修复配置；“运维 AI 大脑”跳到载体、模型与思考强度选择。桌面对话和 AI 设置直接访问本机 HOME，网页后台断线时仍可使用。打开页面不调用模型；发送才启动一次诊断回复，可停止，所有工作区共用历史，每轮仅带近期对话和诊断快照。聊天用于诊断，实际代码修复由独立策略执行。

桌面的“连接与目录”可修改数据目录、后台来源、已有后台地址、固定端口和 Python。数据目录变更可复制现有数据并保留旧目录，也可使用目标目录的数据；空目录从零开始。先检查并展示受影响会话，再明确停止会话、暂停目标、确认自有后台退出后应用和重新打开。借用的后台由其原窗口管理。源码可选随应用更新或指定后台程序目录；页面分别展示所选配置和实际运行来源。启动设置保存在 HOME 外的本机应用数据目录，显式命令行参数优先。坏源码或解释器可在此页修正，自带控制程序不随其切换。

隔离连接迁移验收：`node desktop/scripts/smoke-launch-settings.cjs`（在仓库根目录执行），使用两个临时 HOME 和不可发现 AI CLI 的测试环境，验证真实停止、复制、配置重载及新后台启动。

运维管理员常态使用程序监测。“自动修复”可设置启用开关、Crew 源码 Git 根目录、处理时限、复现与验证命令数组及合入分支，保存到 `home/desktop/admin/operations/policy.json`；打包后的后台目录不是 Git 仓库。开启时核实配置；关闭时允许留空，且不会中止已经执行的修复。已有配置未写 `enabled` 时保持旧行为。修复始终在隔离工作树里进行，应用前还须遵守项目 PR/合并规则并独立复验。portable 缺少已验证的运行中 Git 回退基线时会以 `NoVerifiedBaseline` 拒绝自动激活，保留 commit 和报告；不会把其它源码目录冒充已经运行过的旧版本。

`.npmrc` 将 npm 下载缓存放在本目录，以上环境变量将 Electron 和打包下载也限制在本目录。新的终端构建时同样配置缓存：

```powershell
$env:ELECTRON_CACHE = Join-Path (Get-Location) '.cache/electron'
$env:ELECTRON_BUILDER_CACHE = Join-Path (Get-Location) '.cache/electron-builder'
npm run build
```

隔离 smoke 测试会创建隐藏的真实 BrowserWindow、读取工作区首页、验证 renderer 没有 Node 权限、保存截图，并检查自己创建的后端正常退出。它必须显式使用隔离 home，拒绝正式 `47811` 端口，隔离 Claude/Codex/VS Code 数据源。不会开始模型轮次或接入现有会话。

```powershell
node scripts/smoke.cjs
node scripts/smoke.cjs "dist/win-unpacked/Crew V2.exe"
$crewVersion = (Get-Content package.json -Raw | ConvertFrom-Json).version
node scripts/smoke.cjs "dist/Crew-V2-$crewVersion-portable.exe"
node scripts/smoke.cjs --reuse
node scripts/smoke.cjs --visual
node scripts/smoke-workspaces.cjs
node scripts/smoke-handoff.cjs
node scripts/verify-bundle.cjs
node scripts/verify-icons.cjs "dist/win-unpacked/Crew V2.exe" "dist/Crew-V2-$crewVersion-portable.exe" "dist/Crew-V2-$crewVersion-setup.exe"
```

结果保存在 `smoke-output/source.json` / `packaged.json` / `portable.json` 及同名 PNG。单元测试另外覆盖已有服务复用、Git 根目录解析、新建仓库、已有文件保护与后端端口 0 / 优雅退出。

`smoke-handoff.cjs` 在私有 HOME 用历史记录和假载体守卫验收交接、离线未知结果、真实失败、后续工具状态、折叠与多行草稿保留，以及窄窗深色外观；还核对“在干活”的 leader/下属总数、列表入口、组头人数和空态。证据和截图在 `smoke-output/handoff`。不会向模型发送输入。

启动与退出基准在仓库根目录执行 `node desktop/scripts/benchmark-lifecycle.cjs --label=after`。它使用空 HOME、复用 HOME、已有会话与三个工作区的测试 HOME，另验证借用后台、收起恢复，以及窗口销毁后的排队回调；每次都确认假载体守卫生效且模型启动次数为零。`--desktop=<基线源码中的desktop目录>` 可测另一份冻结源码，`--output=<JSON路径>` 保存结果。“空 HOME”不表示清空操作系统文件缓存，启动就绪与真正进程退出分别计时。

构建后执行 `node desktop/scripts/benchmark-packaged-lifecycle.cjs`，用实际应用版本比对目录程序、单文件 portable，以及 ZIP 解压出的同版程序；外层启动、Electron 创建和 renderer 就绪分段计时，ZIP 的一次解压单独记录。单文件解包发生在 Electron 创建前，不能计作 Crew 界面或后台初始化耗时。结果默认在 `desktop/.cache/packaged-lifecycle-<version>.json`。

`--reuse` 会先创建一个完全隔离的测试服务，再检查真实应用退出后该服务仍存活；随后由测试服务的原始拥有者清理。不会复用生产 home 或正式服务。

Renderer 使用 `contextIsolation: true`、`nodeIntegration: false` 和 sandbox；IPC 仅接受自有工作区首页的主 frame，各 IPC 暴露独立函数。项目使用独立 WebContentsView，没有 preload 或文件系统桥接。项目导航严格限制在对应本机项目，外站点击在系统浏览器打开，外站重定向被拒绝。实现依据 Electron 官方的 [安全指南](https://www.electronjs.org/docs/latest/tutorial/security/)、[WebContentsView](https://www.electronjs.org/docs/latest/api/web-contents-view)、[原生标题栏](https://www.electronjs.org/docs/latest/tutorial/custom-title-bar) 和 [Tray](https://www.electronjs.org/docs/latest/api/tray) 文档。

0.2.6 的 Crew 卡片会独立查询 GitHub 最新正式发布和主干版本；没有 Release 如实显示未发布，网络错误单独呈现。「统一后台与插件」仅应用当前桌面自带版本，与线上查询分开。详见 [桌面更新](../docs/DESKTOP_UPDATES.md)。

桌面页面权限按功能处理：Crew 首页和工作区复制内容直接允许；读取剪贴板、通知、摄像头和麦克风、全屏在使用时弹出原生申请，允许结果仅保留到本页关闭或应用退出。文件和目录继续通过原生选择框授权。外部页面和子框架不获得这些权限。绑定命令复制失败时会自动选中，可按 Ctrl+C，状态刷新不取消选中。隔离回归：`electron desktop/test/clipboard-smoke.cjs`（在根目录使用 desktop/node_modules/.bin/electron）。测试查询真实权限，但模拟复制投递，不读写用户系统剪贴板。

运维聊天发送前会显示载体尚未配置或不可用的原因，并提供“选择运维 AI”入口；选择默认大脑或单独配置“运维 AI”后保存，返回即可继续发送，聊天草稿保留。运维的独立选择不要求同时设置全局默认大脑；只有一个可用载体时仍可自动使用。

“消息渠道 → 主动通知发到哪里”可选仅 QQ、仅 Lark / 飞书、两边都发或关闭，保存后立即用于离开时的主动通知；私聊回复留在发起渠道，测试通知不受此选择限制。默认沿用两边都发，不会在所选渠道离线时自动切换。若仍在复用旧后台，页面会明确提示先在“工具与更新”统一后台版本。

工作区首页会检测原目录是否可访问。目录改名或移动后，点击工作区重新定位；直接打开新位置时，也会先询问是否关联失效的原工作区。确认后沿用已有的停止与迁移流程，保留身份、目标和对话，不搬动磁盘文件，不自动启动 AI。误建的重复工作区可先从 Crew 移除（记录保留），再将原工作区关联到该位置；活跃工作区不能抢占同一目录。改显示名后，页面、项目列表、桌面标签、窗口标题和远程项目列表同步，内部标识保持稳定。

AI 大脑的任务分配包含“新建 leader”，可单独选择载体、模型和思考强度，或跟随默认。保存只影响之后新建的 leader；已有 leader/成员保持各自设置。新用途单独保存，已有后台读取的默认设置格式保留。目录与改名隔离验收：`node desktop/test/workspace-recovery-smoke.cjs`，测试 HOME 与真实服务独立，测试进程 PATH 不含 AI CLI。

0.2.11 汇合工作区改名/重定位、通知渠道选择与独立 leader 大脑，以及部门压缩、工作区清理和交流区全体点名；保留重新定位提示和清理预览两个独立入口。


### 跨工作区动态

项目标签保留项目名，用状态灯、待处理角标和未读小点传达进展；悬停约 280 毫秒展开动态卡片，可查看当前工作、拍板、人工待办、原生权限确认和最近交付，再打开对应项目。卡片为独立原生浮层，可覆盖 WebContentsView，不重载工作区。键盘聚焦标签也可预览，Esc 收起。

桌面主进程每 5 秒批量读取已打开工作区的 `/api/workspaces/activity`，不依赖后台页面是否被 Chromium 节流。读取使用现有数据库与日志，不扫描来源、不唤醒 AI、不标记已读或清除事项。状态未知与连接中断明确区分，断连保留上次记录并标为过期。关闭标签移出查询范围，退出应用释放轮询和浮层。
