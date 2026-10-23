# Crew

让一个人同时指挥很多个 Claude Code 会话。

- 你先在 VS Code 里和会话聊需求；聊清楚了，这个会话就负责一块范围（雷达、视觉、声纹……），自己派 Codex 干活、自己验收。
- 用 **leader** 管一组会话，像公司里的 CEO：下属有事报给它，它当中转站——能定的自己定，需要别人知道的转给相关的人，剩下的攒成待拍板等你。leader 可以同时有好几个，各管一组；leader 自己不写代码。
- 所有会话共用一块**白板**：谁在做什么、谁在等你、设备谁借着、文件归谁、待拍板、交接、日报、用量。
- **守卫**在任何权限模式下都会拦三类事：黑名单上的操作、别人借着的设备、别人宣布归属的文件。所以可以放心开全放行，夜里不会卡在确认框上。
- **网页**主要用来和 leader 聊天：左栏是 leader 列表，中间是对话（待拍板卡片置顶），右栏是这一组的架构图，点下属看它的任务卡。左栏"交流区"是所有 leader 和用户的公共频道；下属直接去 VS Code 聊。
- **QQ**：离开电脑时待拍板推到 QQ 私聊，用 `/ls`、`/1` 这样的命令切换 leader 直接聊。
- **用量分析**：把 Claude 会话、子 agent、Codex 的 token 用量按 leader → 下属 → Codex、天、模型汇总，并按 API 价折算成金额。

设计细节和取舍见 [docs/DESIGN.md](docs/DESIGN.md)，完整页面流程和状态说明见 [docs/WEB_UX.md](docs/WEB_UX.md)。

## 安装

需要：Windows、Python 3.11 以上（在 PATH 里叫 `python`）、支持 `agents` 和 `stream-json` 的 Claude Code（本机以 2.1.306 验收）。需要在 VS Code 接续时，另需安装 Claude Code 扩展，并由用户信任会话所在的目录；扩展不支持 Restricted Mode。

1. 拿到代码：`git clone https://github.com/akitokunnn/Crew.git C:\Tools\Crew`
2. 装插件（用户级，所有项目生效）：
   - `claude plugin marketplace add C:\Tools\Crew`
   - `claude plugin install crew@crew --scope user`
3. 新开一个 Claude 会话。会话启动时会自动在 `~/.crew/` 下生成启动器、默认黑名单和价格表。已经开着的会话执行 `/reload-plugins`，或者重开。
4. 让终端能直接敲 `crew`：把 `%USERPROFILE%\.crew\bin` 加进用户 PATH。加完以后新开的终端才生效；在那之前用 `python %USERPROFILE%\.crew\crew.py`。
5. 在项目里启用：在项目根目录执行 `crew init`，再登记共享资源，例如：
   - `crew resource add 舱体.127 --kind device --desc "主力舱体" --guard "10\.245\.1\.127|f9d7400653b03b50"`
   - `crew resource add 主目录 --kind dir --desc "主工作区 develop"`
   - `crew resource add UE编辑器 --kind app`
6. 建议所有协作的会话统一用全放行模式：在会话里说"开权限"，或执行 `/crew:trust`。

## 日常怎么用

| 想做的事 | 怎么做 |
|---|---|
| 打开网页 | `crew web --open`：按当前目录打开这个项目的页面（一个项目一个标签页），后台已有服务时直接复用 |
| 起一个 leader | 网页左栏"起 leader"：起名 + 一句它要管什么 + 模型 + effort；命令行 `crew leader start --name 感知 --task "<这一组的目标>" [--model <模型ID>] [--effort <档位>]` |
| 把 VS Code 里的会话收编进组 | 在那个会话里说"收编到感知"，它会 `crew join` 写好任务卡并通知 leader；或者在网页"待收编"里点它、起名、选 leader |
| 和 leader 聊 | 网页点左栏的 leader；离开电脑时在 QQ 私聊里 `/ls` 看编号、`/1` 切过去直接说 |
| 在公共交流区发言 | 网页左栏"交流区"，可点选本项目 leader 点名；leader 用 `crew say "<内容>" [--to <leader名字> ...]`。需要对方做事、回答才点名，点名后它空闲时收到通知；普通发言在 leader 下一轮附摘要 |
| 看交流区全文 | 网页"交流区"往上加载更早的发言；leader 和组员都可用 `crew channel [--limit N]`，默认最近 20 条 |
| 回答待拍板 | 网页对话顶部的卡片、"全部 → 等你拍板"，或 QQ 里点按钮、引用那条拍板消息回复；答完 leader 会去叫醒提问的下属 |
| 挂起待拍板 | 网页卡片点"挂起"，留到托盘底部默认折叠的"挂起 · N"区或全部页挂起分组；可直接答或取消挂起，挂起题不计待拍板数、不推 QQ |
| 看某个下属 | 网页右栏点它：负责范围、在做什么、最近进展、Codex、设备和文件；要聊就点"在 VS Code 打开" |
| 开一个新下属 | 让 leader 开（它不用你批准）；或命令行 `crew spawn --name 姿势 --group 感知 --task "<任务>"`，需要独立 worktree 时加 `--new-worktree feature/pose` |
| 找人、叫醒离线下属 | 同项目任何 leader 或用户执行 `crew wake 姿势 "<接着做什么>"`，后台续跑原会话，之后按 crew 名 SendMessage；leader 也可用它找别的 leader，排进通知等它空闲时送达。跨组请求要改组员计划时，先找它自己的 leader |
| 下属跑歪了 | 卡片上"停下这一轮"（只对网页后台运行的下属有效；开在 VS Code 里的去那边停） |
| 收尾 | 在 VS Code 里归档会话，Crew 跟着归档；或者卡片上"归档"（可撤销）。leader 发现下属做完了也会问你要不要归档 |
| 看谁在干什么 | 网页"全部"，或 `crew status` |
| 看用量 | 网页"用量"，或 `crew usage summary`、`crew usage sessions`、`crew usage codex` |
| 看上下文、手动压缩 | 上下文占用达到 50% 显示橙圈，80% 变红；悬停看已用与上限。后台空闲会话点圈旁“压缩”并确认，leader 模型菜单任何占比都能压缩；VS Code 会话去那边输入 `/compact` |

网页"待收编"和 VS Code 的会话列表一致：正在运行的直接列，没在运行也没归档的折叠在"展开 N 个"里，退了组的单独一组。在 VS Code 里取消归档，Crew 不跟着恢复，要恢复就在网页上操作。

有新的未挂起拍板或 leader 新回复时，网页会响一声短提示音，标签标题显示未挂起的待拍板数；挂起、取消挂起本身不响。锁屏或打开"我已离开"时，未挂起的待拍板推到 QQ；`/q` 只列未挂起题，末尾说明挂起数。挂起时 leader 会收到通知，转告提问者：不卡流程的按推荐继续，卡流程的先做别的、先别再问这道题。左下角显示 QQ 连接、绑定和拒收状态。QQ 的命令和配置见 [docs/QQ.md](docs/QQ.md)；leader 巡查目前只按需（你问它才去查），候选场景见 [docs/PATROL.md](docs/PATROL.md)。

交流区新发言不响提示音，也不推 QQ。左栏角标显示用户未读数，打开交流区或滚到底时记为已读；不点名就不叫醒 leader，下一轮摘要每次最多 10 条、每条正文第一行最多 120 字，全文按需查看。

会话里的守则在技能里：成员是 `/crew:member`，leader 是 `/crew:leader`。会话启动时 Crew 会告诉它是谁、归哪个 leader、怎么调命令；还没收编的会话不会被要求自己登记。Crew 记录每个会话读过的守则内容摘要；守则更新后，在下一条消息或 resume / compact 时提醒已收编会话先重读，调用对应技能后停止提醒。

## 命令速查

| 命令 | 作用 |
|---|---|
| `crew init` / `crew projects` | 启用项目 / 列项目 |
| `crew resource add\|list\|rm` | 共享资源，`--guard` 正则让守卫拦住非借用人 |
| `crew join <名字> [--group G] [--scope S] [--done-when D]` / `crew whoami` / `crew task "<一句话>"` / `crew card --scope S --done-when D` | 收编进组并写任务卡 / 我是谁 / 现在在做什么 / 改负责范围和完成标准 |
| `crew status` / `crew who <名字>` | 总览 / 某个会话的详情和发消息地址 |
| `crew lease take <资源> --for "<用途>"` / `crew lease give <资源> --note "<现状>"` / `crew lease list` | 借 / 还 / 列表 |
| `crew claim <路径>` / `crew unclaim <路径>` / `crew claims` | 宣布文件归属（目录以 `/` 结尾） |
| `crew ask "<问题>" --option A --option B --recommend A` / `crew decisions` / `crew answer <编号> "<答复>"` | 待拍板 |
| `crew handoff --to <名字\|分组\|*> "<内容>"` / `crew handoffs` | 交接 |
| `crew report "<内容>"` / `crew reports` | 日报 |
| `crew say "<内容>" [--to <leader名字> ...]` | leader 在本项目交流区发言；可重复点名多人，点名才排通知叫醒 |
| `crew channel [--limit N]` | leader 和组员查看交流区全文，默认最近 20 条，按时间正序并推进已读位置 |
| `crew group list` / `crew roster add\|remove\|move` | 分组和花名册 |
| `crew leader start` / `crew leader close` | 通过正在运行的网页服务开 leader / 收一个分组；`start --dry-run` 只看计划 |
| `crew spawn` | 通过正在运行的网页服务开后台成员；`--dry-run` 只看计划 |
| `crew wake <名字> "<消息>"` | 同项目任何 leader 或用户叫醒离线组员；leader 找别的 leader 时排通知，用户找 leader 直接在网页聊；服务未运行或组员进程状态未知时报错 |
| `crew open <名字>` | 两步在 VS Code 接续同一会话，停止空闲的原运行端前须明确确认 |
| `crew web --open` | 按当前目录打开工作区，已有服务时复用 |
| `crew autostart install\|status\|uninstall` | 当前用户登录后隐藏运行，不弹浏览器 |
| `crew usage scan\|summary\|sessions\|daily\|codex\|agents\|models\|export` | 用量 |
| `crew events` / `crew doctor` | 事件流水 / 自检 |

每个命令都有 `--help`；大多数支持 `--json`。

## 守卫和黑名单

黑名单在 `~/.crew/blacklist.txt`，一行一条：`工具 :: 正则 :: 原因`，保存后马上生效。只放"任何时候都不能做、你点头也不行"的事；需要你先点头的操作写在 CLAUDE.md 之类的规矩里。

守卫另外两条检查不用配置：资源被别人借着时，命令里出现它的守卫正则就拦；文件被别人 `crew claim` 了，用 Edit / Write 改它就拦。

## 用量分析

数据来源都在本机，只读：

- Claude 会话记录 `~/.claude/projects/**`（含子 agent）
- Codex 会话记录 `~/.codex/sessions/**`（含周额度百分比和点数余额）
- 派活记录 `~/.crew/codex_runs.jsonl`：codex-dispatch 的派活脚本每次派 Codex 时写一行，这样 Codex 的用量能算到派活的会话头上

金额按 `~/.crew/prices.json` 折算（美元 / 百万 token，初值是 Anthropic 官方 API 价）。用订阅时这只是参考，不是实际账单。价格表里没有的模型只显示 token。

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
| `~/.crew/codex_runs.jsonl` | 派活记录 |

## 更新和开发

- 从本地目录装的插件，**运行时直接用仓库里 `plugin/` 这份代码**（不另拷一份）。改 Python 代码对之后的每次钩子调用立刻生效；改了钩子注册、技能、角色定义，要在会话里 `/reload-plugins` 或重开会话。
- 所以**不要在主检出里直接改代码**：在 `Temp/wt/<名字>` 开 worktree 改，测试通过再合进 main。主检出里的代码一旦出错，所有会话的钩子都会跟着出错。
- 这是单人本机工具，优先保持代码直白、少配置；测试用临时白板，不随意关闭用户正在使用的会话或窗口。提交信息用中文说明改了什么、为什么，不加署名行。
- 跑测试：`cd plugin` 后执行 `python -m unittest discover -s tests -v`。在 Claude 会话里跑时先清掉环境变量 `CLAUDE_CODE_SESSION_ID`（PowerShell：`Remove-Item Env:CLAUDE_CODE_SESSION_ID`），否则两个命令行测试会拿到外层会话的 ID 而失败。
- Python 后端只用标准库；网页是原生 JS，Markdown 用仓库内的 markdown-it 15.0.2，不需要构建步骤。
- 成员和网页 leader 都用 PATH 里的 `claude`（npm 全局安装的命令行版），它的版本可能比 VS Code 扩展里带的旧。旧版本可能缺跨会话消息等功能，要定期 `npm install -g @anthropic-ai/claude-code@latest`。

## 已知限制

- 开在 VS Code 里的下属，网页只能看状态和卡片，不能发消息、也停不下它正在跑的一轮。
- leader 用 SendMessage 联系在线下属；离线时用 `crew wake <名字> "<要它做的事>"` 在后台续跑原会话，再按 crew 名联系。VS Code、终端或后台仍有活进程时拒绝叫醒，进程状态未知时也拒绝启动。
- **被叫醒的会话如果 VS Code 里还留着旧标签，不要在旧标签里直接打字**，否则可能两个进程同时写一个会话；要接过去请用网页卡片上的“在 VS Code 打开”。
- 服务启动后会自动续跑聊天日志最后状态仍是 working 的未归档会话；如果原进程还活着，等它退出再续跑，最多等 30 分钟。idle / offline 的不自动续跑。
- 两个在线会话的权限模式不一样时（比如一个全放行、一个逐条确认），SendMessage 可能等人批准；协作会话统一用全放行（`/crew:trust`）。后台首次启动和所有后台续跑一律全放行，保留 Crew 守卫。
- leader 起动时可选模型和 effort（low / medium / high / xhigh / max），默认跟账号设置走；新下属不写 `--model` / `--effort` 就继承所在组 leader 对应设置。Haiku 4.5 不支持 effort。对话头部显示进程回报的实际“模型 · effort”，菜单可切换，toast 提示“下一步起用 …”；运行中 effort 只能切具体档位，恢复账号默认需在停止后设置。后台下属卡片只读显示这两项。忙时对话底部和后台下属卡片显示阶段、计时与本轮输出 token。
- 已经开着的会话要 `/reload-plugins` 或重开，才会加载插件的钩子和守卫；网页收编会登记身份，不会替它加载插件。
- QQ 长回复按段落分段，`TEXT_LIMIT = 2000` 是为了手机好读；10-23 主控实测 5000 字能发。

## 字体

网页界面使用华为 HarmonyOS Sans SC 字体（常规、中等两个字重），按其许可证原样随插件分发，许可证在 `plugin/crew/web/fonts/HarmonyOS_Sans_LICENSE.txt`。
