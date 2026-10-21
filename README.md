# Crew

让一个人同时指挥很多个 Claude Code 会话。

- 每个会话管一个领域（雷达、视觉、声纹……），自己当主控、派 Codex 干活、自己验收提交。
- **leader** 会话管一个分组：派活、转达、仲裁设备和文件归属、收集要你拍板的事、汇总进展。leader 可以同时有好几个，各管一组。
- 所有会话共用一块**白板**：谁在做什么、谁在等你、设备谁借着、文件归谁、待拍板、交接、日报、用量。
- **守卫**在任何权限模式下都会拦三类事：黑名单上的操作、别人借着的设备、别人宣布归属的文件。所以可以放心开全放行，夜里不会卡在确认框上。
- **看板网页**一屏看全，直接收编项目里已经开着的会话；和 leader 在网页里聊天，看流式回复、工具调用和组员状态。`crew spawn` 开的成员仍在同一个 Windows Terminal 窗口里，每个会话一个彩色标签页。
- **用量分析**：把 Claude 会话、子 agent、Codex 的 token 用量按会话、分组、天、模型汇总，并按 API 价折算成金额。

设计细节和取舍见 [docs/DESIGN.md](docs/DESIGN.md)。

## 安装

需要：Windows、Python 3.11 以上（在 PATH 里叫 `python`）、Claude Code 2.1 以上、Windows Terminal。

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
| 让已经开着的会话加入 | 网页看板的“发现的会话”里点“收进组”，起名字、选分组；也可以在那个会话里执行 `crew join 雷达 --task "..."` |
| 开一个 leader 管一组 | 先运行 `crew web --open`，在网页点“开 leader”，可混选已登记成员和发现的会话；命令行也可用 `crew leader start --name 感知 --members 雷达,视觉 --task "<这一组的目标>"` |
| 开一个新会话干活 | `crew spawn --name 姿势 --group 感知 --task "<任务>"`，需要独立 worktree 时加 `--new-worktree feature/pose` |
| 一屏看全 | `crew web --open` |
| 看谁在干什么 | `crew status` |
| 回答待拍板 | 网页上直接答，或 `crew decisions` 再 `crew answer <编号> "<答复>"`；提问的会话下一轮开始时会收到 |
| 看用量 | `crew usage summary`、`crew usage sessions`、`crew usage codex`，或网页的用量页 |
| 收掉一组 | `crew leader close 感知 --with-members` |

`crew status` 和网页只列在线的会话和一天内才离线的会话；已收的组不显示，已收、未命名、离线超过一天的会话只计个数（网页上收在折叠条里）。

“发现的会话”只列当前项目下尚未登记的活会话，标题取 Claude 会话记录里最新的 `ai-title`。收编不必重开会话；还没加载 Crew 钩子的会话，状态先按 Claude 的 busy / idle 显示，SendMessage 地址也直接取活会话列表。

网页服务会在后台保持 leader 的 Claude CLI 进程。浏览器刷新只重读聊天记录，不会重启 leader；网页服务重启后，下一次发言会恢复原会话 ID。若这个 ID 已在 VS Code 或其他进程中运行，网页会拒绝双开。leader 回复时先等这一轮结束再发送；组员消息会自动进入对话。网页服务退出时只关闭自己启动的 leader 的输入，让进程正常结束。

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
| `crew spawn` | 在 crew 窗口开新会话 |
| `crew web` | 看板网页 |
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
| `~/.crew/web.json` | 正在运行的网页服务地址，供 `leader start` 连接，支持自定义端口 |
| `~/.crew/codex_runs.jsonl` | 派活记录 |

## 更新和开发

- 从本地目录装的插件，**运行时直接用仓库里 `plugin/` 这份代码**（不另拷一份）。改 Python 代码对之后的每次钩子调用立刻生效；改了钩子注册、技能、角色定义，要在会话里 `/reload-plugins` 或重开会话。
- 所以**不要在主检出里直接改代码**：在 `Temp/wt/<名字>` 开 worktree 改，测试通过再合进 main。主检出里的代码一旦出错，所有会话的钩子都会跟着出错。
- 跑测试：`cd plugin` 后执行 `python -m unittest discover -s tests -v`。
- Python 后端只用标准库；网页是原生 JS，Markdown 用仓库内的 markdown-it 15.0.2，不需要构建步骤。
- 成员和网页 leader 都用 PATH 里的 `claude`（npm 全局安装的命令行版），它的版本可能比 VS Code 扩展里带的旧。旧版本可能缺跨会话消息等功能，要定期 `npm install -g @anthropic-ai/claude-code@latest`。

## 已知限制

- 待拍板的答复在提问的会话**下一轮开始时**才送到；会话空着时不会被网页叫醒。要立刻生效，就让 leader 用 SendMessage 通知它。
- 已经开着的会话要 `/reload-plugins` 或重开，才会加载插件的钩子和守卫；网页收编会登记身份，不会替它加载插件。
- 两个会话的权限模式不一样时（比如一个全放行、一个逐条确认），SendMessage 发过去会被扣住，等对方那边的人点批准，没人批过一阵就作废。协作的会话统一用全放行（`/crew:trust`）。`crew spawn` / `crew leader start` 开的会话都是全放行。
- 飞书 / QQ 通知还没做（下一版）。
