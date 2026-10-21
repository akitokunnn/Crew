# Crew

让一个人同时指挥很多个 Claude Code 会话。

- 每个会话管一个领域（雷达、视觉、声纹……），自己当主控、派 Codex 干活、自己验收提交。
- **leader** 会话管一个分组：派活、转达、仲裁设备和文件归属、收集要你拍板的事、汇总进展。leader 可以同时有好几个，各管一组。
- 所有会话共用一块**白板**：谁在做什么、谁在等你、设备谁借着、文件归谁、待拍板、交接、日报、用量。
- **守卫**在任何权限模式下都会拦三类事：黑名单上的操作、别人借着的设备、别人宣布归属的文件。所以可以放心开全放行，夜里不会卡在确认框上。
- **网页工作区**先列待处理，再看 leader、成员和 Codex 的关系图。导航只有总览、会话、用量；点会话继续聊天，报告、交接、借还和派活放在同一条活动时间线。新成员和 leader 都在网页后台运行，需要时再转到 VS Code。
- **用量分析**：把 Claude 会话、子 agent、Codex 的 token 用量按会话、分组、天、模型汇总，并按 API 价折算成金额。

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
| 让已经开着的会话加入 | 网页“会话”的“可加入”列表里点“加入 Crew”，起名字、选分组；也可以在那个会话里执行 `crew join 雷达 --task "..."` |
| 开一个 leader 管一组 | 先运行 `crew web --open`，在网页点“组建协作组”，可混选已登记成员和发现的会话；命令行也可用 `crew leader start --name 感知 --members 雷达,视觉 --task "<这一组的目标>"` |
| 开一个新会话干活 | 网页点“新组员”；也可在网页服务运行时用 `crew spawn --name 姿势 --group 感知 --task "<任务>"`，需要独立 worktree 时加 `--new-worktree feature/pose` |
| 在 VS Code 接着聊 | 会话详情或对话页点“VS Code”，也可用 `crew open <名字>`；先开对应目录，确认窗口就绪后再继续打开会话 |
| 进入网页工作区 | `crew web --open`；按当前目录打开固定工作区网页，后台已有服务时直接复用 |
| 看谁在干什么 | `crew status` |
| 回答待拍板 | 网页上直接答，或 `crew decisions` 再 `crew answer <编号> "<答复>"`；提问的会话下一轮开始时会收到 |
| 看用量 | `crew usage summary`、`crew usage sessions`、`crew usage codex`，或网页的用量页 |
| 收掉一组 | 网页“管理协作组”选择归档组和会话，或解散分组并保留会话；命令行可用 `crew leader close 感知 --with-members` |

工作区侧栏保留当前项目未归档的会话，包括可恢复的网页会话；“会话管理”可按待收编、网页、VS Code、已归档或全部筛选。归档后的历史仍可查看，输入改为只读。`crew status` 的终端摘要只展开在线和近期离线会话，其余计数。

“待收编”包含当前项目下尚未登记、或已经由钩子登记但还没有协作名字的活会话，标题取 Claude 会话记录里最新的 `ai-title`。收编不必重开会话；还没加载 Crew 钩子的会话，状态先按 Claude 的 busy / idle 显示，SendMessage 地址也直接取活会话列表。

一个工作区一个固定网页，标签标题与顶部都写清名字；页面内不切项目，切换工作区另开标签页。各会话的输入草稿与阅读位置会在本机浏览器保留，切换会话或刷新后可继续。大屏同时显示对话和组内上下文，窄屏通过导航和“组员与拍板”抽屉查看。

网页服务会在后台保持新成员和 leader 的 Claude CLI 进程。浏览器刷新只重读聊天记录，不会重启会话；网页服务重启后，下一次发言会恢复原会话 ID。若这个 ID 已在 VS Code 或其他进程中运行，网页会拒绝双开。会话回复时先等这一轮结束再发送；组员消息会自动进入对话。网页服务退出时只关闭自己启动的会话的输入，让进程正常结束。

转到 VS Code 分两步：先打开会话所在的项目目录，等窗口就绪并确认目录正确，再点“窗口已就绪，继续打开”。这时才结束空闲的网页或终端会话，续上原来的会话 ID；取消会保留原会话。正在干活时不能转移，VS Code 里的会话不会被关闭。

会话行和标题旁的操作菜单可直接改名、归档、移出 Crew、退组和恢复。可恢复的整理操作提供撤销；移出后不会被后续打卡自动加回。`Ctrl+K` 快速找会话。用量默认当前工作区，可明确切全机；Claude 5 小时 / 每周与 Codex 每周额度并排显示，标注账户范围和采集时间。

飞书通知、文字答复和登录自启见 [docs/FEISHU.md](docs/FEISHU.md)。leader 巡查仍只讨论，候选场景见 [docs/PATROL.md](docs/PATROL.md)。卡片按钮需要应用启用对应回调；不会自动替用户开公司权限。

会话里的守则在技能里：成员是 `/crew:member`，leader 是 `/crew:leader`。会话启动时 Crew 会告诉它是谁、归哪个 leader、怎么调命令。

## 命令速查

| 命令 | 作用 |
|---|---|
| `crew init` / `crew projects` | 启用项目 / 列项目 |
| `crew resource add\|list\|rm` | 共享资源，`--guard` 正则让守卫拦住非借用人 |
| `crew join <名字>` / `crew whoami` / `crew task "<一句话>"` | 认领名字 / 我是谁 / 在做什么 |
| `crew status` / `crew who <名字>` | 总览 / 某个会话的详情和发消息地址 |
| `crew lease take <资源> --for "<用途>"` / `crew lease give <资源> --note "<现状>"` / `crew lease list` | 借 / 还 / 列表 |
| `crew claim <路径>` / `crew unclaim <路径>` / `crew claims` | 宣布文件归属（目录以 `/` 结尾） |
| `crew ask "<问题>" --option A --option B --recommend A` / `crew decisions` / `crew answer <编号> "<答复>"` | 待拍板 |
| `crew handoff --to <名字\|分组\|*> "<内容>"` / `crew handoffs` | 交接 |
| `crew report "<内容>"` / `crew reports` | 日报 |
| `crew group list` / `crew roster add\|remove\|move` | 分组和花名册 |
| `crew leader start` / `crew leader close` | 通过正在运行的网页服务开 leader / 收一个分组；`start --dry-run` 只看计划 |
| `crew spawn` | 通过正在运行的网页服务开后台成员；`--dry-run` 只看计划 |
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
| `~/.crew/web.json` | 正在运行的网页服务地址，供 `spawn` 和 `leader start` 连接，支持自定义端口 |
| `~/.crew/codex_runs.jsonl` | 派活记录 |

## 更新和开发

- 从本地目录装的插件，**运行时直接用仓库里 `plugin/` 这份代码**（不另拷一份）。改 Python 代码对之后的每次钩子调用立刻生效；改了钩子注册、技能、角色定义，要在会话里 `/reload-plugins` 或重开会话。
- 所以**不要在主检出里直接改代码**：在 `Temp/wt/<名字>` 开 worktree 改，测试通过再合进 main。主检出里的代码一旦出错，所有会话的钩子都会跟着出错。
- 这是单人本机工具，优先保持代码直白、少配置；测试用临时白板，不随意关闭用户正在使用的会话或窗口。提交信息用中文说明改了什么、为什么，不加署名行。
- 跑测试：`cd plugin` 后执行 `python -m unittest discover -s tests -v`。
- Python 后端只用标准库；网页是原生 JS，Markdown 用仓库内的 markdown-it 15.0.2，不需要构建步骤。
- 成员和网页 leader 都用 PATH 里的 `claude`（npm 全局安装的命令行版），它的版本可能比 VS Code 扩展里带的旧。旧版本可能缺跨会话消息等功能，要定期 `npm install -g @anthropic-ai/claude-code@latest`。

## 已知限制

- 待拍板的答复在提问的会话**下一轮开始时**才送到；会话空着时不会被网页叫醒。要立刻生效，就让 leader 用 SendMessage 通知它。
- 已经开着的会话要 `/reload-plugins` 或重开，才会加载插件的钩子和守卫；网页收编会登记身份，不会替它加载插件。
- 两个会话的权限模式不一样时（比如一个全放行、一个逐条确认），SendMessage 发过去会被扣住，等对方那边的人点批准，没人批过一阵就作废。协作的会话统一用全放行（`/crew:trust`）。`crew spawn` / `crew leader start` 开的会话都是全放行。
- 飞书卡片按钮尚需在开放平台启用回调；VS Code / 终端 leader 的文字来信先以交接送达下一轮。QQ 暂未接入，优先级最低。
