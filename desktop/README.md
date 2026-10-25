# Crew 桌面版

原生 Electron 窗口、工作区首页和系统托盘。需要本机已有 Python 3，以及实际要使用的 Claude Code / Codex；普通工作文件夹无需 Git，后端继续使用 Python 标准库。

```powershell
cd desktop
$env:electron_config_cache = Join-Path (Get-Location) '.cache/electron'
$env:ELECTRON_CACHE = $env:electron_config_cache
$env:ELECTRON_BUILDER_CACHE = Join-Path (Get-Location) '.cache/electron-builder'
npm install
npm start
```

首页可用系统文件夹对话框打开普通目录；已有 Git 项目的子目录仍会解析到真实仓库根目录，已登记的工作区优先复用。新建工作区只创建空文件夹，不自动初始化 Git，已有目录不会被覆盖。工作区注册到 Crew 后，在原生窗口打开对应项目。分支、提交和独立 worktree 等功能仅在 Git 仓库中使用。

每个工作区有独立标签页，切换时保留聊天草稿、当前 Leader、滚动和页面状态。重复打开同一工作区会切到已有标签。`Ctrl+T` 返回工作区选择，`Ctrl+Tab` / `Ctrl+Shift+Tab` 切换标签；`Ctrl+W` 关闭当前项目标签，不停止该工作区的 AI。只剩首页时 `Ctrl+W` 隐藏窗口。菜单还提供 `Ctrl+N` 新建、`Ctrl+O` 打开、`Alt+Left` 返回以及刷新当前标签。

首页每个工作区右侧的设置按钮、当前工作区菜单和项目左上角均可打开“工作区设置”。修改显示名不改变内部身份。更改目录先停止现有运行、暂停未完成目标，再将已有 leader 和成员的执行目录一起更新；聊天与目标历史保留，不搬磁盘文件，不自动开始任务。新目录需要事先存在，原工作区下的相对子目录也需要在新位置存在。

“从 Crew 移除”先停止该工作区执行，再从列表移除登记，保留磁盘文件和全部记录。以后重新打开同一目录会恢复原工作区。

“设置 → 消息渠道”可同时配置 QQ 与 Lark / 飞书。按页面步骤创建机器人、保存凭证、建立连接，再用自己的账号私聊机器人发送绑定码；无需编辑 JSON 或重启 Crew。页面提供重连、断开、重新绑定与测试通知，已保存的 Secret 不回显。Lark 当前支持私聊文字和引用拍板答复，完整权限与事件订阅说明见[消息渠道连接流程](../docs/REMOTE_CHANNELS.md)。

关闭窗口会隐藏到托盘，AI 和服务继续运行；托盘或菜单中的「退出 Crew」才退出应用。窗口保留原生最小化、最大化、关闭与可拖动标题栏；应用菜单收在标题栏的省略号中。Crew 图标用于窗口、任务栏、托盘、安装器和快捷方式。

首页侧栏和应用菜单中的「工具与更新」显示 Claude Code / Codex 的当前版本、最新版本和更新结果。自动更新默认关闭；正常启动后会检查版本，手动点击可再次检查。正在使用某个工具的会话会让更新排队，空闲后再试，也可以取消排队。自动更新需用户主动开启；无法确定安装来源或运行状态时会保留说明，不强行替换。更新失败可查看本地日志；退出应用会等待已开始的更新完成。

0.1.2 另有 Crew 卡片：手动检查并统一到**当前桌面自带版本**，确认空闲后备份、同步已安装插件、重启自有后台并在原端口重连，标签与草稿保留。它不下载互联网新版，也不受 CLI 自动更新开关控制；借用或不支持更新准备的旧后台会说明需先退出原 Crew。已有 Claude 会话需重载插件或重开。流程与限制见 [Crew 更新说明](../docs/DESKTOP_UPDATES.md)。

启动时探测指定 home 的 `web.json`，健康的 Crew 服务直接复用。退出应用不关闭复用的服务。只有登记不存在或连接被明确拒绝时，桌面版才启动自己的 `plugin/crew_desktop.py`，使用系统分配的端口，并通过专用 stdin 通道优雅关闭自己创建的后端。无法判断的超时、HTTP 错误或不兼容服务会明确报错。同一 home 的后台持有 OS 文件锁；多个界面同时首启时只有一个创建服务，另一个等待后复用。不会为了应用启动而重启当前服务。原有服务的版本由原服务决定。

已有桌面版正在托管任务时，可以用独立界面配置显式连接它：

```powershell
$crewVersion = (Get-Content package.json -Raw | ConvertFrom-Json).version
& ".\dist\Crew-$crewVersion-portable.exe" --home C:\Temp\crew-private --service-url=http://127.0.0.1:7122 --profile ui-private
```

显式地址必须是健康的本机 Crew 服务，失败直接退出，不另起后台。界面默认数据位于 `home/desktop/ui`，`--profile` 使用 `home/desktop/profiles/<安全名称>`，不会争抢旧版界面的实例锁。后台源码仍位于稳定的 `home/desktop/backend`。菜单「查看日志文件夹」可打开本地 `home/desktop/logs`，其中保留后台最近的 stderr 和启动错误。

Windows 命令行中的服务地址使用上例的 `--service-url=地址` 形式。Electron 会为防止协议启动参数注入而拒绝「独立 URL 参数后还有其他参数」的命令；这发生在应用代码加载之前。也可以在所有应用参数前加 `--`。该行为依据 [Electron 原生参数检查](https://github.com/electron/electron/blob/main/shell/app/command_line_args.cc)，不会通过关闭它来处理。

```powershell
npm start -- --home C:\Temp\crew-desktop-private --port 0
npm test
npm run build
```

构建版本读取本目录 `package.json`，产物为 `dist/Crew-<version>-portable.exe`、`dist/Crew-<version>-setup.exe`；前者可直接运行，后者是当前用户安装器，提供开始菜单、桌面快捷方式和卸载入口。`dist/win-unpacked/Crew.exe` 是未压缩版本。安装器与应用未做代码签名；EXE 原生图标资源仍正常嵌入。后端源码随应用放在 `resources/backend`，打包版会将后端按内容哈希保留到所选 home 的 `desktop/backend`，保证 portable 解包目录清理后 CLI 和守卫路径仍有效；Python 与 Git 使用本机安装。

验收新版本且保留正在运行的正式实例时，使用独立数据目录。此入口不会复制正式会话、QQ 凭据或自动恢复旧任务：

```powershell
$crewVersion = (Get-Content package.json -Raw | ConvertFrom-Json).version
& ".\dist\Crew-$crewVersion-portable.exe" --home C:\Temp\crew-acceptance --port 0 --isolated-sources
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
node scripts/smoke.cjs dist/win-unpacked/Crew.exe
$crewVersion = (Get-Content package.json -Raw | ConvertFrom-Json).version
node scripts/smoke.cjs "dist/Crew-$crewVersion-portable.exe"
node scripts/smoke.cjs --reuse
node scripts/smoke.cjs --visual
node scripts/smoke-workspaces.cjs
node scripts/verify-icons.cjs dist/win-unpacked/Crew.exe "dist/Crew-$crewVersion-portable.exe" "dist/Crew-$crewVersion-setup.exe"
```

结果保存在 `smoke-output/source.json` / `packaged.json` / `portable.json` 及同名 PNG。单元测试另外覆盖已有服务复用、Git 根目录解析、新建仓库、已有文件保护与后端端口 0 / 优雅退出。

`--reuse` 会先创建一个完全隔离的测试服务，再检查真实应用退出后该服务仍存活；随后由测试服务的原始拥有者清理。不会复用生产 home 或正式服务。

Renderer 使用 `contextIsolation: true`、`nodeIntegration: false` 和 sandbox；IPC 仅接受自有工作区首页的主 frame，各 IPC 暴露独立函数。项目使用独立 WebContentsView，没有 preload 或文件系统桥接。项目导航严格限制在对应本机项目，外站点击在系统浏览器打开，外站重定向被拒绝。实现依据 Electron 官方的 [安全指南](https://www.electronjs.org/docs/latest/tutorial/security/)、[WebContentsView](https://www.electronjs.org/docs/latest/api/web-contents-view)、[原生标题栏](https://www.electronjs.org/docs/latest/tutorial/custom-title-bar) 和 [Tray](https://www.electronjs.org/docs/latest/api/tray) 文档。

0.2.6 的 Crew 卡片会独立查询 GitHub 最新正式发布和主干版本；没有 Release 如实显示未发布，网络错误单独呈现。「统一后台与插件」仅应用当前桌面自带版本，与线上查询分开。详见 [桌面更新](../docs/DESKTOP_UPDATES.md)。
