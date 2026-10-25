# Crew

让一个人同时指挥多个 Claude Code / Codex 会话。用 Windows 桌面应用或浏览器打开工作文件夹，让 leader 直接推进目标、必要时协调成员，共用一块白板。

## 能做什么

- **桌面工作区**：选择普通文件夹或 Git 仓库，也可新建空目录；不会自动初始化 Git。多个项目各有标签，切换保留草稿和页面状态。关闭窗口隐藏到托盘，后台任务继续运行。
- **系统管理员**：整个 Crew 只有一个，独立守护进程检查连接、记录异常，后台退出时恢复同一端口。常态不启动模型；配置可信复现与验证命令后，具体事故可触发有限的隔离修复，保留 commit，经项目合并规则通过才申请应用。人工暂停不自动恢复。
- **目标与交付**：普通问答直接聊天。明确执行目标保存原始要求、验收、轮次和独立原生历史；每轮交付由程序生成带证据和具体 example 的 HTML，未达标如实结束。暂停先持久化，再等待成员和原生执行实际停止。
- **动态上下文**：只增量装载当前目标相关材料，可查看来源、纠正与实际应用状态；原生历史独立计量并在超限时换代，保留用户原话与确认约束。字符数和原生 token 分开显示，未知用量不当成零。
- **任务资料**：每个任务收录自己的交付报告、文档和重要汇总。宽窗口右侧常驻本组关系树；需求、交付物和上下文按需打开，聊天顶部只保留紧凑任务条。
- **工具与更新**：检查 Claude Code / Codex CLI 当前和最新版本，支持更新排队与取消；工具空闲并确认安装来源后才更新，自动更新默认关闭。
- **统一设置**：默认 AI、交流区摘要、Git 管理员和运维可分别配置载体、模型与 effort；内部任务也可跟随默认 AI，留空模型和 effort 使用原生默认。保存设置不启动任务，不改变已有会话。
- **执行权限**：设置页明确选择 Codex 的只读 / 项目沙箱 / 完全访问，或 Claude 的原生默认 / 允许编辑 / 完全访问；下一次启动载体生效，不改工具全局配置。摘要与后台修复保持各自隔离策略，不随对话权限扩大。
- **两种执行工具**：创建 leader、成员时选择 Claude Code 或 Codex，可以混编在同一组。保留已有 Claude 会话，也能从 VS Code 收编进来。
- **中断与停止**：任务输入区可中断当前回复；“停止此人”会结束其托管执行，并阻止排队消息自动复活。leader 负责的任务一并暂停，必须明确恢复。同一 Crew 身份由 Crew 托管一份原生会话，历史分别保存。
- **原生权限审批**：Codex 请求越过沙箱执行命令时，页面显示命令、目录与原因，由用户允许这一次或拒绝。审批只属于当前原生轮次，不改默认权限；中断、退出或任务失效会取消审批。
- **leader 和白板**：leader 可以直接实施，成员优先复用。可直接与 leader 或成员对话，忙时插话进入当前执行；内部有效结果批量送达，ACK 只存档。设备借用、文件归属和 Git 规则继续生效。
- **拍板和人工待办**：先通知这里需要你，点击听说明、聊清背景后再展示完整选项和后果；要你亲手做的事进入“我的待办”。答复与完成记录回到原目标及提出者，旧轮次不能据此复工。
- **信任模式**：每个 leader 单独开关和填写目标，所属成员继承；保存不主动唤醒，下次对话再传达。保留取舍、待办、报告、验证和 Git 提交。
- **横排总览与评论**：“全部”页每组一列，可横向滚动；组和成员可加评论。leader 可标第 1～3 优先，并调整普通组的顺序。
- **Git 管理员**：在独立 worktree 中统一定分支规则、检查合并请求、推送、开 PR、等 CI 和合进 base；支持 Codex 或 Claude，网页可选载体和模型。
- **守卫**：检查黑名单、设备借用、文件归属和 Git 管理规则。Claude 使用插件 hooks，Codex 接入原生 shell / apply_patch 执行前 hook，覆盖边界见 [Codex 接入说明](docs/CODEX_INTEGRATION.md)。
- **远程与用量**：QQ 私聊可按项目和 leader 继续对话、答复拍板、查看待办；用量页按组、会话、日期和模型汇总 Claude / Codex token，并按价格表折算参考金额。

详细用法见 [工作流程](docs/WORKFLOWS.md) 和 [页面说明](docs/WEB_UX.md)，实现设计见 [docs/DESIGN.md](docs/DESIGN.md)，0.1.1 变更与验证见 [交付记录](docs/DESKTOP_0.1.1.md)。

## 安装与启动

需要 Windows、Python 3.11 以上（在 PATH 中可运行 `python`），以及已安装并登录的 Claude Code 或 Codex CLI。普通文件夹工作区不需要 Git；分支、提交、合并和独立 worktree 等 Git 功能才需要安装 Git 并使用仓库。两种 AI 工具按需安装；仅安装 Codex 也能使用全部内建 AI 入口，无需 Claude 插件。从源码运行桌面应用还需要 Node.js / npm。已有验证使用 Claude Code 2.1.310、Codex CLI 0.164.0，具体模型和 effort 以本机执行工具返回的列表为准。

先取得源码：

```powershell
git clone https://github.com/akitokunnn/Crew.git C:\Tools\Crew
```

### 桌面应用

```powershell
cd C:\Tools\Crew\desktop
$env:ELECTRON_CACHE = Join-Path (Get-Location) '.cache/electron'
$env:ELECTRON_BUILDER_CACHE = Join-Path (Get-Location) '.cache/electron-builder'
npm install
npm start
```

首页选择已有文件夹，或填写名称、选择保存位置来新建普通工作区。进入项目后，点“起 leader”，选择执行工具、模型和任务即可开始。设置的 AI 大脑页面会检测实际模型清单，模型与思考强度可下拉选择，也保留自定义输入。`Ctrl+T` 返回工作区首页，`Ctrl+Tab` 切换标签，`Ctrl+W` 关闭当前项目标签。

关闭窗口会隐藏到托盘；“退出 Crew”才退出应用。桌面版优先复用当前 home 中已登记的健康服务，复用时继续使用该服务的版本；自行启动的后端在退出应用时关闭，借用的服务保持运行。

`npm run build` 生成 `desktop/dist/Crew-0.2.4-portable.exe` 和 `Crew-0.2.4-setup.exe`，直接运行仍需本机 Python 和所选模型 CLI。构建、独立数据目录和隔离验收见 [桌面版说明](desktop/README.md)。

### Claude 插件与 VS Code

需要从 Claude 会话收编成员、使用插件 hooks 或在 VS Code 接续 Claude 会话时，再安装插件：

```powershell
claude plugin marketplace add C:\Tools\Crew
claude plugin install crew@crew --scope user
```

新开 Claude 会话后，会在 `~/.crew/` 下生成命令行启动器、默认黑名单和价格表；已有会话执行 `/reload-plugins` 或重开。VS Code 接续还需要 Claude Code 扩展，并信任项目目录。Codex 会话在 Crew 中继续，暂不支持转交到 Claude 编辑器。

### 命令行、浏览器与右键入口

桌面首次启动或 Claude 插件初始化后，把 `%USERPROFILE%\.crew\bin` 加进用户 PATH，新终端即可使用 `crew`。配置 PATH 前，可在 PowerShell 使用 `python "$env:USERPROFILE\.crew\crew.py" <命令>`。

使用浏览器和登录自启时，执行 `crew autostart install`；需要文件夹右键入口时，再执行 `crew menu install`。之后在工作文件夹里运行 `crew start`，或右键选择“在这里启动 Crew”，会自动启用项目并打开网页。已有服务时用 `crew web --open` 打开当前项目。

Claude 协作会话建议统一权限模式；`/crew:trust` 用于 Claude 的工具权限。下面的 `crew trust set on --leader ... --goal ...` 用于单个 leader 组的自主决策目标，两者分别配置。

## 日常怎么用

| 想做的事 | 怎么做 |
|---|---|
| 在另一个 git 项目打开 Crew | 文件夹或文件夹内空白处右键“在这里启动 Crew”；命令行 `crew start`，也可 `crew start --cwd "C:\项目目录" --name 项目名`。首次自动启用，已有项目直接打开；服务未运行时调用已安装的登录自启任务，最多等 20 秒 |
| 打开网页 | `crew web --open`：按当前目录打开这个项目的页面（一个项目一个标签页），后台已有服务时直接复用 |
| 起一个 leader | 左栏“起 leader”：起名、填写目标、选择执行工具和模型；命令行 `crew leader start --name 感知 --task "<这一组的目标>" --runtime codex`，也可选 `claude`，另有 `--model` / `--effort` |
| 把 VS Code 里的会话收编进组 | 在那个会话里说"收编到感知"，它会 `crew join` 写好任务卡并通知 leader；或者在网页"待收编"里点它、起名、选 leader |
| 和 leader 聊 | 网页点左栏的 leader；离开电脑时在 QQ 私聊里 `/leader` 看编号、`/1` 切过去直接说 |
| 在公共交流区发言 | 网页左栏"交流区"，可点选本项目 leader 点名；leader 用 `crew say "<内容>" [--to <leader名字> ...]`。需要对方做事、回答才点名，点名后它空闲时收到通知；普通发言在 leader 下一轮附摘要 |
| 看交流区全文 | 网页"交流区"往上加载更早的发言；leader 和组员都可用 `crew channel [--limit N]`，默认最近 20 条 |
| 回答待拍板 | 对话顶部的卡片、“全部 → 等你拍板”，或 QQ 按钮 / 引用回复；先看决定事项、推荐理由和后果。旧题看不懂时点“让 leader 说明白”；答复后由 leader 协调继续 |
| 处理人工待办 | 左栏“我的待办”，阻塞 AI 的事项排在前面；展开看步骤和验收方法，可点“做完了”“不做了”或“去问它”，误操作可撤销；有待办时，项目默认进入此页 |
| 开启信任模式 | leader 对话头部或菜单里填写本组目标；普通终端用 `crew trust set on --leader 名字 --goal "<目标>"`，保存不唤醒；该 leader 的报告也在同一入口 |
| 挂起待拍板 | 网页卡片点"挂起"，留到托盘底部默认折叠的"挂起 · N"区或全部页挂起分组；可直接答或取消挂起，挂起题不计待拍板数、不推 QQ |
| 和某个下属聊 | 总览或本组关系树点本人进入对话；资料与操作中查看范围、记录和载体。已由外部工具运行的会话需先结束外部进程再接管 |
| 给成员更换执行工具 | 卡片或菜单点“更换…”，选择 CC/Codex 和兼容模型，检查交接内容及用量提示；确认后下次发消息/唤醒才启动，原生历史分别保留 |
| 开一个新下属 | 让 leader 开；或命令行 `crew spawn --name 姿势 --group 感知 --task "<任务>" --runtime codex`，需要独立 worktree 时加 `--new-worktree feature/pose`；不写执行工具则继承 leader |
| 跨工具发消息 | 已加入 Crew 的会话用 `crew send <名字> "<内容>"`，Codex 也有 `crew_send_message` 动态工具；忙时排队，外部 Claude 会话在下一次 hook 收取 |
| 找人、叫醒离线下属 | `crew wake 姿势 "<接着做什么>"` 后台续跑原会话。leader 可联系同项目其他 leader，成员可给自己的 leader 排唤醒通知；跨组改计划先找对方 leader |
| 下属跑歪了 | 任务输入区“中断本轮”保留会话；菜单“停止此人”确认托管进程退出，排队消息不能复活。外部 VS Code 会话去原工具停止 |
| 收尾 | 目标每轮生成最终报告；成员与历史保留，后续可复用。明确不再需要时手动归档（可撤销），VS Code 的归档仍同步 |
| 看谁在干什么、留评论 | “全部”页每组一列，横向滚动查看，组或成员可加评论并通知所属 leader；命令行 `crew status` |
| 找 AI 交付的文档 | 打开对应任务的“交付与文档”，查看原始文件、重要汇总和报告；文件只按真实任务绑定收录，不按 leader 猜归属 |
| 验收任务 | 待验收时可“验收通过”或填写原因“需要修改”；保留 AI 原报告与用户验收记录，退回后可明确再开一轮 |
| 同时打开多个工作区 | 桌面首页选择项目，会分别保留在顶部标签中；`Ctrl+Tab` 切换，关闭标签不停止后台任务 |
| 检查 Claude Code / Codex 更新 | 桌面首页或应用菜单打开“工具与更新”；忙时可排队，工具空闲并通过安装来源检查后才更新；自动更新默认关闭 |
| 调整 leader 优先级 | 右键 leader，可标第 1～3 优先；优先组置顶，普通组可调整顺序 |
| 看 Git 分支和提交关系 | 网页左栏"Git"；首次选择 base / stable 后启用，可查看提交树、分支检查、worktree、负责会话与 GitHub PR / CI，手动"刷新（fetch）"更新远端引用 |
| 把提交交给 Git 管理员 | 开工前 `crew git policy` 看规则，做完提交自己的分支后 `crew git request "<做了什么、测过什么、有没有要用户亲手验收的>"`；Git 页面看请求状态，点“和 git 管理员聊”进对话 |
| 看用量 | 网页"用量"，或 `crew usage summary`、`crew usage sessions`、`crew usage codex` |
| 看上下文、手动压缩 | 上下文占用达到 50% 显示橙圈，80% 变红；悬停看已用与上限。后台空闲会话点圈旁“压缩”并确认，leader 模型菜单任何占比都能压缩；VS Code 会话去那边输入 `/compact` |

### 拍板、待办与信任模式

拍板只问一个需要你决定的取舍。页面先显示简短通知，点击“听它说明”进入对话；聊清后点“给我选项”，说明不全时要求原位补齐。最终卡片展示为什么需要你、推荐理由和每个选项的后果，QQ 的过期选项仍要求刷新。

人工待办用于上机验收、登录账号、操作编辑器或设备等必须亲手做的事。AI 登记事项、步骤、确认方法和阻塞状态；完成或取消后，结果送回所属 leader，由它协调提出者继续。点“做完了”代表你已操作，AI 仍需验证结果。首次上线会通知现有 leader 盘点未完成的人工事项。

信任模式按 leader 独立生效：你给本组目标，AI 在授权范围内推进、记录推荐与取舍；需要你动手的事项继续进入待办，Crew 守卫继续生效。设置只保存，下次正常对话传达；详细报告只汇总本组，leader 仍须补齐测试证据、产物、未验证项和 commit SHA。

### 会话、主题与通知

“待收编”读取本机 Claude / VS Code 会话来源：正在运行的直接列，没在运行也没归档的折叠在“展开 N 个”里，退了组的单独一组。在 VS Code 里取消归档，Crew 不跟着恢复，要恢复就在页面上操作。

每个项目会保存一套主题色，新项目优先分到还没被用的主题，用满六套后轮流分配。左下角齿轮的“主题”可随时切换，按钮、选中态、链接、logo 和标签页图标一起变化；裸地址的项目列表也显示各自配色。浅色 / 深色 / 跟随系统仍由旁边的外观按钮独立切换。

有新的未挂起拍板或 leader 新回复时，网页会响一声短提示音，标签标题显示未挂起的待拍板数；挂起、取消挂起本身不响。锁屏或打开"我已离开"时，未挂起的待拍板推到 QQ；`/decisions` 只列未挂起题，末尾说明挂起数。挂起时 leader 会收到通知，转告提问者：不卡流程的按推荐继续，卡流程的先做别的、先别再问这道题。左下角显示 QQ 连接、绑定和拒收状态。QQ 的命令和配置见 [docs/QQ.md](docs/QQ.md)；leader 巡查目前只按需（你问它才去查），候选场景见 [docs/PATROL.md](docs/PATROL.md)。

QQ 命令不分大小写、忽略首尾空白：`/help`（`/h`）帮助，`/projects`（`/p`）列项目，`/project 项目名` 选项目，`/leader 名字` 选该项目的 leader，`/where`（`/w`）确认收件人；原有 `/leader 2`（`/l 2`、`/2`）编号保持可用。`/decisions`（`/d`）查看拍板，`/todo`（`/t`）查看人工待办，`/back`（`/b`）我回来了。引用旧消息会回到原 leader，不改变普通聊天的当前选择；人工待办在离开电脑时不主动推送。远程路由与 Lark 状态见 [docs/REMOTE_CHANNELS.md](docs/REMOTE_CHANNELS.md)。

交流区新发言不响提示音、不推 QQ；左栏显示未读数，打开或滚到底记为已读。不点名的发言在 leader 下一轮附摘要，网页可手动“整理”，新增满 20 条也会自动生成置顶要点卡；原文默认折叠。`crew channel` 先看卡再看卡后的新发言，`crew channel --raw` 查看原文。

会话里的守则在 `plugin/skills/`：Claude 成员是 `/crew:member`，leader 是 `/crew:leader`，Git 管理员是 `/crew:git`；Codex 由宿主加载同一角色守则，绑定 Crew 身份和动态白板工具。启动时 Crew 会说明身份、所属组和命令入口；还没收编的会话不会被要求自己登记。启用 Git 管理的项目还会附管理员提示和分支规则前 6 行，全文用 `crew git policy` 看。守则更新后，在下一条消息或 resume / compact 时提醒已收编会话重读。

## 命令速查

| 命令 | 作用 |
|---|---|
| `crew init` / `crew projects` | 启用项目 / 列项目 |
| `crew start [--cwd DIR] [--name N]` | 找到 git 根目录、首次启用、通过登录自启任务拉起服务并打开项目；非 git 目录或同名项目冲突时明确报错 |
| `crew menu install\|status\|uninstall` | 安装 / 查看 / 移除当前用户的资源管理器右键入口，无需管理员权限；右键启动不弹终端，失败会弹窗说明原因 |
| `crew resource add\|list\|rm` | 共享资源，`--guard` 正则让守卫拦住非借用人 |
| `crew join <名字> [--group G] [--scope S] [--done-when D]` / `crew whoami` / `crew task "<一句话>"` / `crew card --scope S --done-when D` | 收编进组并写任务卡 / 我是谁 / 现在在做什么 / 改负责范围和完成标准 |
| `crew status` / `crew who <名字>` | 总览 / 某个会话的详情和发消息地址，含上下文占比；拿不到时显示未知和原因 |
| `crew lease take <资源> --for "<用途>"` / `crew lease give <资源> --note "<现状>"` / `crew lease list` | 借 / 还 / 列表 |
| `crew claim <路径>` / `crew unclaim <路径>` / `crew claims` | 宣布文件归属（目录以 `/` 结尾） |
| `crew ask "<问题>" --title "<短标题>" --why-user "<为什么要用户决定>" --recommend-reason "<推荐理由>" --option "<行动>" --consequence "<后果>" --option "<另一行动>" --consequence "<后果>" --recommend "<行动>"` | 登记结构化拍板，每个选项对应一个后果；完整示例见工作流程 |
| `crew decisions` / `crew answer <编号> "<答复>"` / `crew decision revise <编号> ...` | 查看、答复、原位补写拍板说明 |
| `crew todo add "<事项>" --steps "<操作步骤>" --verify "<确认方法>" [--blocking]` | AI 登记需要用户亲手做的事项 |
| `crew todo list [--all]` / `crew todo withdraw <编号> --reason "<原因>"` | 查看人工待办 / AI 撤回不再需要的事项；用户完成、取消和撤销在页面操作 |
| `crew trust set on --leader 名字 --goal "<目标>"` / `crew trust set off --leader 名字` / `crew trust status --leader 名字` / `crew trust report --leader 名字` | 设置指定 leader 的信任模式、查看本组状态与报告草稿；已绑定会话缺省用自己的组 |
| `crew handoff --to <名字\|分组\|*> "<内容>"` / `crew handoffs` | 交接 |
| `crew report "<内容>"` / `crew reports` | 日报 |
| `crew doc add <路径> --title "<标题>" --summary "<摘要>" [--kind summary\|report\|guide\|other] [--important]` / `crew doc list [--leader 名字]` / `crew doc refresh` | 提交交付文档 / 查看列表 / 从历史会话增量整理；标题和摘要必填，原文件留在项目或提交者工作树 |
| `crew say "<内容>" [--to <leader名字> ...]` | leader 在本项目交流区发言；可重复点名多人，点名才排通知叫醒 |
| `crew channel [--limit N] [--raw]` | 默认先看要点卡，再看卡后最近 N 条全文（默认 20 条）；`--raw` 看最近 N 条原文。无卡时直接看原文，按时间正序并推进已读位置 |
| `crew group list` / `crew roster add\|remove\|move` | 分组和花名册 |
| `crew leader start --name <名字> --task "<目标>" [--runtime claude\|codex]` / `crew leader close <组名>` | 通过正在运行的服务开 leader / 收一个分组；`start --dry-run` 只看计划 |
| `crew spawn --name <名字> --group <组名> --task "<任务>" [--runtime claude\|codex]` | 开后台成员；同工具时继承 leader 的模型设置，跨工具重新选择；`--dry-run` 只看计划 |
| `crew send <名字> "<消息>"` | Crew 会话按名字或 Crew 会话 ID 投递，支持 Claude / Codex 混合协作 |
| `crew wake <名字> "<消息>"` | leader 或用户叫醒离线组员；leader 找别的 leader、组员找自己的 leader 时排通知，空闲时送达；用户找 leader 直接在页面聊；服务未运行或组员进程状态未知时报错 |
| `crew compact [<名字>]` | 通过网页服务压缩自己的组员，用户也可指定会话；网页 leader 不带名字或指定自己时，等这一轮说完后压缩 |
| `crew stop <名字或会话ID>` | 停止自己或本组成员的 Crew 托管执行；用户可指定项目内人员。确认退出并保留停止门槛，队列不能自动恢复 |
| `crew open <名字>` | 两步在 VS Code 接续同一会话，停止空闲的原运行端前须明确确认 |
| `crew web --open` | 按当前目录打开工作区，已有服务时复用 |
| `crew git enable [--base 分支] [--stable 分支] [--runtime codex\|claude]` / `crew git disable` / `crew git status` | 启用并自动开 Git 管理员 / 收掉管理员的组并停用 / 查看配置；base 自动依次选 develop、dev、main、master，stable 选与 base 不同的 master 或 main |
| `crew git policy` / `crew git policy --set "<正文>"` | 看分支规则全文 / Git 管理员设置规则 |
| `crew git request [--branch 分支] "<说明>"` / `crew git requests [--all]` | 本项目任何会话（含没收编的）提交合并请求（默认当前分支） / 看活动队列或全部请求 |
| `crew git resolve <编号> <merged\|rejected\|waiting_user\|merging> --note "<说明>" [--pr <号>]` | Git 管理员更新请求状态并给提出者排消息 |
| `crew autostart install\|status\|uninstall` | 当前用户登录后隐藏运行，不弹浏览器 |
| `crew usage scan\|summary\|sessions\|daily\|codex\|agents\|models\|export` | 用量 |
| `crew events` / `crew doctor` | 事件流水 / 自检 |

每个命令都有 `--help`；大多数支持 `--json`。

## 守卫和黑名单

黑名单在 `~/.crew/blacklist.txt`，一行一条：`工具 :: 正则 :: 原因`，保存后马上生效。只放"任何时候都不能做、你点头也不行"的事；需要你先点头的操作写在 CLAUDE.md 之类的规矩里。

守卫另外两条检查不用配置：资源被别人借着时，命令里出现它的守卫正则就拦；文件被别人 `crew claim` 了，Claude 的 Edit / Write 和已接入的 Codex apply_patch 会检查归属。

项目启用 Git 管理时，已收编会话在 Bash / PowerShell 执行 `git push`、`gh pr create`、`gh pr merge` 会被拦，按提示用 `crew git request` 交给 Git 管理员。Git 全局参数、可执行文件路径和连接命令同样检查；帮助命令与引号中的普通文本放行。Git 管理员可执行这些操作；未收编会话只在启动时看到协作提示。

Git 管理员可以自行定分支规则、推功能分支、开 PR、CI 绿后合进 base、安排合并顺序、删已合并分支。合进 stable 或发版、force push 或改写已推送历史、删未合并分支、需要用户在编辑器或设备亲手验收、行为冲突要取舍，仍须 `crew ask` 登记拍板。Crew 后台每 5 分钟检查 base 最新提交失败的 CI 和请求对应 PR 的 CI 完成变化，排通知叫醒管理员，同一次运行只通知一次；GitHub 不可用时 Git 页面显示原因。

## 用量分析

数据来源都在本机，只读：

- Claude 会话记录 `~/.claude/projects/**`（含子 agent）
- Codex 会话记录 `~/.codex/sessions/**`（含周额度百分比和点数余额）
- 派活记录 `~/.crew/codex_runs.jsonl`：codex-dispatch 的派活脚本每次派 Codex 时写一行，这样 Codex 的用量能算到派活的会话头上

金额按 `~/.crew/prices.json` 折算（美元 / 百万 token，初值是 Anthropic 官方 API 价）。用订阅时这只是参考，不是实际账单。价格表里没有的模型只显示 token。

网页 Claude 每周额度下显示“其中 Crew ≈ X%”：按当前 Claude 七天窗口汇总所有项目中 leader、进组成员及其子 agent 的 API 价折合金额，再按固定每 1% ≈ $20 换算（社区实测 $16～27，不自动校准），不计 Codex。窗口未采集、过期或请求含未定价模型时不显示。会话间 SendMessage 即使标记 isMeta，也记为新的 peer 轮次，干活时长按全部轮次分段。

## 文件都在哪

| 位置 | 内容 |
|---|---|
| `~/.crew/crew.db` | 白板（SQLite） |
| `~/.crew/blacklist.txt`、`~/.crew/prices.json` | 黑名单、价格表（你自己维护） |
| `~/.crew/crew.py`、`~/.crew/bin/crew.cmd` | 命令行启动器（每次会话启动时刷新） |
| `~/.crew/spawn/` | crew 开会话时的任务文件 |
| `~/.crew/chat/<会话ID>.jsonl` | 网页聊天记录，刷新和重启后重放；不保存思考内容 |
| `~/.crew/chat/files/<会话ID>/` | 发给 leader 的截图 |
| `~/.crew/qq.json` | QQ 机器人 app_id / app_secret，用户自己维护 |
| `~/.crew/notify.json` | 主人 openid、通知起点、离开状态、当前 leader 与 leader 编号，Crew 维护 |
| `~/.crew/web.json` | 正在运行的网页服务地址，供 `spawn`、`leader start` 和 `wake` 连接，支持自定义端口 |
| `~/.crew/runtime.json` | 命令行启动器当前使用的 Python 和后端路径 |
| `~/.crew/desktop/backend/` | 打包桌面版保留的后端源码，供 CLI 和 Codex 守卫继续使用 |
| `~/.crew/codex_runs.jsonl` | 派活记录 |

`~/.crew` 是默认数据目录；桌面 `--home <目录>` 和 CLI `crew --home <目录> ...` 可使用独立目录，适合隔离测试。

## 更新和开发

- 从本地目录装的插件，**运行时直接用仓库里 `plugin/` 这份代码**（不另拷一份）。改 Python 代码对之后的每次钩子调用立刻生效；改了钩子注册、技能、角色定义，要在会话里 `/reload-plugins` 或重开会话。
- 所以**不要在主检出里直接改代码**：在 `Temp/wt/<名字>` 开 worktree 改，测试通过再合进 main。主检出里的代码一旦出错，所有会话的钩子都会跟着出错。
- 本仓库的远程 `main` 要求通过 PR 合并，至少 1 个有写入权限的账号批准。当前审核账号为 `qianlong-ai` 和 `akitokunnn`，任意一个批准即可；本机 AI 可以使用用户已授权的账号代为审核。GitHub 不允许 PR 发起账号批准自己的 PR，因此由这两个账号交叉审核；推送新提交后，原审核批准失效，需要重新审核。
- 这是单人本机工具，优先保持代码直白、少配置；测试用临时白板，不随意关闭用户正在使用的会话或窗口。提交信息用中文说明改了什么、为什么，不加署名行。
- 跑后端测试：`cd plugin` 后执行 `python -m unittest discover -s tests -v`；网页行为回归是 `node tests/test_runtime_ui.js`。在 Crew / Claude 会话里跑测试前，清掉继承的 `CREW_SESSION_ID`、`CLAUDE_CODE_SESSION_ID`，避免把外层会话身份带入临时白板。桌面测试在 `desktop` 下执行 `npm test`；打包与真实窗口 smoke 见桌面说明。
- Python 后端只用标准库；网页是原生 JS，Markdown 用仓库内的 markdown-it 15.0.2，不需要构建步骤。
- Claude 会话使用 PATH 中的 `claude`，Codex 会话使用本机 Codex CLI；分别维护安装与登录。Claude CLI 版本可能与 VS Code 扩展内置版本不同，模型和 effort 清单随执行工具变化。

## 已知限制

- 开在 VS Code 里的下属，网页只能看状态和卡片，不能发消息、也停不下它正在跑的一轮。
- 混合工具消息使用 `crew send` / `crew_send_message`；Claude 原生 SendMessage 不能直接寻址 Codex。离线成员用 `crew wake <名字> "<要它做的事>"` 恢复原会话；VS Code、终端或后台仍有活进程时拒绝重复启动，进程状态未知时也拒绝启动。
- **被叫醒的会话如果 VS Code 里还留着旧标签，不要在旧标签里直接打字**，否则可能两个进程同时写一个会话；要接过去请用网页卡片上的“在 VS Code 打开”。
- 服务启动后会续跑聊天日志最后状态仍是 working 的未归档会话；用户停止或手动暂停的不会自动恢复。如果原进程还活着，等它退出再续跑，最多等 30 分钟。idle / offline 的不自动续跑。
- Claude 在线会话的权限模式不同时，原生 SendMessage 可能等人批准；后台会话使用设置页选择的原生权限模式，并保留 Crew 守卫。项目的信任目标不会替代执行工具的权限配置。
- leader 起动时可选模型和 effort，默认跟账号设置走；同工具的新下属可继承 leader 设置，跨工具不继承不兼容模型。Haiku 4.5 不支持 effort，Codex 的可选模型与档位来自本机原生列表。对话头部显示进程回报的实际“模型 · effort”，菜单可切换；后台下属卡片只读显示。忙时显示阶段、计时与本轮输出 token。
- 已经开着的会话要 `/reload-plugins` 或重开，才会加载插件的钩子和守卫；网页收编会登记身份，不会替它加载插件。
- 旧 Claude 会话不会因为更改字段就变成 Codex 历史；Codex 暂不支持转交到 VS Code 的 Claude 编辑器。
- 新建 leader、无分组成员和内部 AI 任务使用设置页的默认或专项配置；成员优先继承所属 leader。未配置时仅装一个工具可自动采用它；两个都安装时请先选默认 AI，不偏向任一载体。执行失败不会自动换工具，已有会话需通过会话菜单显式切换。
- Windows Codex 启动与恢复使用磁盘真实路径大小写，避免沙箱把同一目录当作不同写入范围。项目沙箱仍保护 Git 元数据；0.2.2 的可见单次审批已在独立 worktree 中实测完成 Git 提交，默认权限保持不变。此项不代表所有 elevated 沙箱、外部 Python 安装或网络缓存路径均已验证。
- Codex 守卫覆盖已接入的 shell / apply_patch；已启动命令的 `write_stdin` 不重新触发执行前 hook，其他原生工具或 MCP 需单独适配。hook 超时或崩溃后的停会话措施不能保证阻止所有副作用。
- Lark 尚未接通，当前远程入口是 QQ。“全部”采用横排列组，尚未实现无限蓝图画布与拖动连线。信任报告是记录汇总草稿，实际验证和提交仍须由负责的 leader 补齐。

## 桌面版 0.1.1

本版功能与实际验证记录见 [DESKTOP_0.1.1.md](docs/DESKTOP_0.1.1.md)。使用时以实际打包产物和已启动服务为准：

- 桌面多标签工作区：同一窗口打开多个项目，切换保留聊天草稿、所选 leader 和滚动位置；关闭标签保留后台任务。
- 桌面外壳与安装入口：细标题栏、原生窗口按钮、统一应用 / 托盘图标，以及安装版。
- “工具与更新”：检查 Claude Code / Codex 的本机与最新版本，提供更新、忙时排队、取消和自动更新开关。
- “文档中心”：集中整理 AI 交付的文档、重要汇总、摘要与来源，在对话旁提供本组文档入口。
- VS Code 历史会话发现、后台扫描重试及桌面服务复用的稳定性修复。
- 成员 CC / Codex 往返切换、交接预览，以及每个 leader 独立且不主动唤醒的信任模式。

## 字体

网页界面使用华为 HarmonyOS Sans SC 字体（常规、中等两个字重），按其许可证原样随插件分发，许可证在 `plugin/crew/web/fonts/HarmonyOS_Sans_LICENSE.txt`。
