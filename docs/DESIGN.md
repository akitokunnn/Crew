# Crew 设计

Crew 让一个人同时指挥多个 Claude Code 会话：每个会话管一个领域、自己派 Codex 干活；leader 会话管一组会话；所有人共用一块白板（谁在干什么、设备谁借着、文件归谁、等人拍板的事、交接、日报、用量）。

本文是施工依据。表结构以 [plugin/crew/schema.sql](../plugin/crew/schema.sql) 为准，存储入口以 [plugin/crew/store.py](../plugin/crew/store.py) 为准，这两个文件只由主控改。

## 1. 结构

```
Crew/
  .claude-plugin/marketplace.json     本仓库同时是一个插件市场，只有一个插件 crew
  plugin/                             插件根目录（运行时的 ${CLAUDE_PLUGIN_ROOT}）
    .claude-plugin/plugin.json
    hooks/hooks.json                  钩子注册（主控写）
    agents/leader.md                  leader 角色（主控写）
    skills/member|leader|trust/       守则和 /crew:trust（主控写）
    crew_hook.py                      钩子入口：python crew_hook.py <事件>
    crew_cli.py                       命令行入口：python crew_cli.py <子命令>
    crew/                             Python 包，只用标准库（Python 3.11+）
      store.py  schema.sql            存储（主控）
      board.py                        白板读写（项目、会话、分组、资源、借还、归属、拍板、交接、日报、事件）
      cli.py                          命令行解析和输出
      hooks.py                        各钩子事件
      guard.py                        守卫
      install.py                      写 ~/.crew 下的启动器、PATH 垫片、默认文件
      usage.py  usage_cli.py          用量扫描、汇总、命令行
      spawn.py                        开新会话、Windows Terminal（第三单）
      web.py  web/                    看板网页（第三单）
    defaults/blacklist.txt  defaults/prices.json
    tests/                            unittest，cd plugin && python -m unittest discover -s tests
  docs/DESIGN.md
  README.md
```

用户机器上的数据都在 `~/.crew/`（下文写作 HOME）：

| 文件 | 内容 |
|---|---|
| `crew.db` | 白板（SQLite，WAL） |
| `blacklist.txt` | 守卫黑名单，用户自己维护；不存在时由 install 从 defaults 复制 |
| `prices.json` | 按 API 价折算用的价格表；不存在时由 install 从 defaults 复制 |
| `runtime.json` | `{"plugin_root": "...", "python": "...", "updated_at": "..."}`，每次会话启动时刷新 |
| `crew.py` | 启动器：读 runtime.json，把 plugin_root 加进 sys.path，调用 `crew.cli.main(sys.argv[1:])` |
| `bin/crew.cmd` | PATH 垫片：`@python "%USERPROFILE%\.crew\crew.py" %*` |
| `spawn/<id>.json`、`spawn/<id>.md` | 开新会话时的参数和任务正文（第三单） |
| `codex_runs.jsonl` | 派活脚本记的派活记录（§9） |

所有函数显式接收 `home` 参数；命令行有全局参数 `--home DIR`（默认 `store.default_home()`），测试一律传临时目录。**不用环境变量改 HOME。**

## 2. 身份和项目

- **会话 ID**：钩子从输入 JSON 的 `session_id` 取；命令行从环境变量 `CLAUDE_CODE_SESSION_ID` 取（Claude Code 会给工具命令设这个变量）。命令行也接受 `--session ID`。需要会话身份的命令拿不到 ID 时报错退出（exit 1），并说明"请在 Claude 会话里运行，或加 --session"。不需要身份的命令（status、decisions、answer 等）在普通终端里也能用，此时操作人记作 `akito`。
- **crew 名**：人起的稳定名字（雷达、视觉）。同一项目里，未离线（status 不是 offline / closed）的会话不能重名，重名时 join 报冲突（exit 3）。
- **cc_name**：Claude Code 里显示的会话名，也是 SendMessage 的地址。由 `crew join` 和 `crew who` 调 `claude agents --json`（返回数组，元素有 sessionId、name、status、pid、cwd、kind），按 sessionId 匹配取 name。调用失败时 cc_name 留空，并打印一行原因。
- **spawn 带来的身份**：crew 开的会话启动时带环境变量 `CREW_NAME`、`CREW_GROUP`、`CREW_ROLE`、`CREW_SPAWN_ID`。SessionStart 钩子看到这些变量就直接登记，不用再 join。
- **项目判定**：会话的 cwd 落在某个已 init 项目的 root 下（`store.is_under`），就属于这个项目；多个项目嵌套时取 root 最长的。不属于任何项目的会话：钩子只做守卫的黑名单检查，其它什么都不写。
- **worktree / branch**：cwd 所在 git 仓库的 toplevel 和当前分支（`git -C cwd rev-parse --show-toplevel`、`git -C cwd branch --show-current`），只在 SessionStart 和 join 时取。

## 3. 状态

| status | 含义 | 谁改 |
|---|---|---|
| working | 正在干活 | UserPromptSubmit；PostToolUse（提问框 / 计划审批结束后） |
| waiting | 等人（status_note 写原因） | Notification；PreToolUse 的 AskUserQuestion / ExitPlanMode |
| idle | 一轮做完，空着 | SessionStart；Stop |
| offline | 会话退出了 | SessionEnd |
| closed | 所在分组被收掉了 | `crew leader close --with-members` |

`claude agents --json` 里没有这个会话的进程时，网页和 `crew status` 显示为离线，但不改库。

## 4. 命令行

全局：`crew [--home DIR] <子命令> ...`。输出给人看，用中文；带 `--json` 的命令输出 JSON（UTF-8）。退出码：0 成功；1 出错；2 参数错（argparse）；3 冲突（被借着、重名、归属冲突）。

| 命令 | 作用 |
|---|---|
| `install` | 写 HOME 下的 runtime.json、crew.py、bin/crew.cmd；blacklist.txt、prices.json 不存在时从 defaults 复制。可重复执行 |
| `init [--name N] [--root DIR]` | 启用项目。root 默认是当前目录的 git toplevel，name 默认取 root 的目录名 |
| `projects` | 列出项目 |
| `resource add NAME [--kind device\|dir\|app\|other] [--desc T] [--guard REGEX]` / `resource list` / `resource rm NAME` | 资源。guard 正则先 `re.compile` 校验，非法就报错 |
| `join NAME [--task T] [--role member\|leader] [--group G]` | 当前会话认领 crew 名。role=leader 时同时建（或接管）同名分组 |
| `whoami [--json]` | 我是谁、属于哪个项目和分组、我的 leader 是谁（含 leader 的 cc_name） |
| `task TEXT` | 更新"在做什么" |
| `status [--project P] [--json]` | 总览：按分组列会话（状态、在做什么、最后活动时间）、未分组的会话、借出的资源、文件归属、未答的待拍板条数 |
| `who NAME [--json]` | 某个会话的详情和它现在的 SendMessage 地址 |
| `lease take RES --for T [--until HH:MM] [--overwrites T]` | 借。已被别人借着就打印借用人、用途、借出时间、预计归还，exit 3。自己已借着就更新用途 |
| `lease give RES [--note T]` | 还。只能还自己借的；`--force --reason T` 可以还别人的（leader 或 akito 用），记事件 |
| `lease list [--json]` | 借出中的资源 |
| `claim PATTERN [--note T]` / `unclaim PATTERN\|--all` / `claims [--json]` | 文件归属（§6） |
| `ask QUESTION [--option T]... [--recommend T] [--blocking]` | 记一条待拍板，打印编号 |
| `decisions [--all] [--json]` | 待拍板（默认只列 open） |
| `answer ID TEXT` | 答复；answered_by 为当前会话 ID，普通终端里为 `akito` |
| `withdraw ID` | 撤回自己的问题 |
| `handoff --to NAME\|GROUP\|* TEXT` / `handoffs [--json]` | 交接。`handoffs` 列出发给我（我的名字、我的分组、*）还没送达我的 |
| `report TEXT` / `reports [--since YYYY-MM-DD] [--group G] [--json]` | 日报 |
| `group list [--json]` | 分组和成员 |
| `roster add GROUP NAME...` / `roster remove GROUP NAME...` / `roster move NAME --to GROUP` | 花名册。一个会话只在一个分组里 |
| `leader close GROUP [--with-members]` | 收掉分组：closed_at 置上；成员的 group_name 清空，带 `--with-members` 时成员 status 改为 closed。不杀进程 |
| `events [--session NAME] [--limit N]` | 事件流水 |
| `doctor` | 检查：数据库能打开、blacklist.txt 每行合法、runtime.json 的 plugin_root 存在、当前目录属于哪个项目 |
| `usage ...` | 转给 `crew.usage_cli.main(argv, home)`（§8） |
| `spawn ...`、`leader start ...`、`web` | 第三单加 |

所有写操作同时写一条 events。

## 5. 钩子

入口 `python "${CLAUDE_PLUGIN_ROOT}/crew_hook.py" <事件>`，从 stdin 读 UTF-8 JSON（字段：session_id、transcript_path、cwd、hook_event_name、permission_mode，以及各事件自己的字段）。注册见 [plugin/hooks/hooks.json](../plugin/hooks/hooks.json)。

| 参数 | 事件 | 做什么 | 输出 |
|---|---|---|---|
| `session-start` | SessionStart | 先跑 install（刷新 runtime.json）。属于某项目时登记 / 更新会话（带 CREW_* 变量就按它登记；已登记过的会话保留 crew 名）；status=idle | 属于项目时输出 `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}`，中文告诉会话：项目名、我的 crew 名（没认领就提示 `crew join <名字>`）、分组和 leader（含 leader 的 cc_name）、命令行怎么调（`python "<HOME>/crew.py" ...`）、守则 `/crew:member`（leader 是 `/crew:leader`）、发给我还没送达的交接 |
| `user-prompt-submit` | UserPromptSubmit | status=working，last_prompt。查我提的、已答复但没送达（delivered_at 为空）的待拍板，和没送达我的交接 | 有内容时输出 UserPromptSubmit 的 additionalContext，并标记送达 |
| `stop` | Stop | status=idle，last_message 取输入里的 `last_assistant_message` 前 300 字 | 无 |
| `notification` | Notification | status=waiting，status_note 取输入里的 `message` | 无 |
| `pre-ask` | PreToolUse（AskUserQuestion、ExitPlanMode） | status=waiting，note 写"等你回答提问"或"等你审批计划" | 无 |
| `post-ask` | PostToolUse（同上） | status=working | 无 |
| `session-end` | SessionEnd | status=offline，ended_at | 无 |
| `guard` | PreToolUse（所有工具） | §7 | 拦截时 stderr + exit 2 |

- 每个事件写一条 events，detail 里记输入 JSON 的键名列表和该事件关心的字段（不存 transcript 全文、不存 tool_input 全文，命令截前 200 字）。
- 打卡类事件出错不能影响会话：打一行原因到 stderr，exit 1。**除 guard 外任何事件都不能 exit 2。**
- 会话的 crew 名、cc_name 不在钩子里调 `claude agents`（太慢）。

## 6. 文件归属

- `claim PATTERN`：PATTERN 是相对项目 root 的路径或绝对路径。以 `/` 结尾表示整个目录；否则按 glob（`fnmatch`，`**` 当作 `*`）。存库前转成 `store.norm_path` 形式的绝对路径（目录保留结尾 `/`）。
- 同一路径已被别的会话 claim 时（新 pattern 和已有 pattern 任一方向能匹配上对方的前缀或 glob），打印对方是谁，exit 3。
- 会话 offline / closed 后它的 claim 自动不生效（查询时过滤），不用删。

## 7. 守卫

PreToolUse 对所有工具运行。按顺序检查，命中任何一条就 stderr 写原因、exit 2，否则 exit 0：

1. **黑名单**：`HOME/blacklist.txt`，格式见 defaults/blacklist.txt 文件头（`工具 :: 正则 :: 原因`，不区分大小写）。检查对象：Bash / PowerShell 用 command；有 file_path / notebook_path 的工具用路径；其它工具用 tool_input 的 JSON。任何一行非法 → 拦截所有调用，只放行目标是 blacklist.txt 本身的 Edit / Write / Read。对所有会话生效，不管属不属于项目。
2. **设备借用**（只对属于项目的会话、只查 Bash / PowerShell）：项目里每个有 guard_regex 的资源，如果有未归还的借用、借用人不是本会话、command 匹配 guard_regex → 拦截，原因写借用人（crew 名或会话 ID 前 8 位）、用途、借出时间、预计归还，并提示"等对方归还，或用 SendMessage 找对方 / leader 协商"。没人借着就放行。
3. **文件归属**（只对属于项目的会话、只查 Edit / Write / NotebookEdit）：路径命中别的在线会话的 claim → 拦截，原因写对方和 note。
4. **白板读不了**（数据库打不开、锁超时）：拦截 Bash / PowerShell / Edit / Write / NotebookEdit，原因写真实错误；但命令里含 `crew.py`、`crew_cli.py` 或以 `crew ` 开头的放行（留出查问题的路）。

拦截信息末尾统一加一句："Do not retry or work around it; tell the user."

## 8. 用量

### 扫描（`crew usage scan`，其它 usage 命令先自动扫一遍）

数据源，默认路径由参数传入（测试用假目录）：

| 源 | 默认位置 | 取什么 |
|---|---|---|
| Claude 主会话 | `~/.claude/projects/*/*.jsonl`（文件名 = 会话 ID） | 见下 |
| Claude 子 agent | `~/.claude/projects/*/<会话ID>/subagents/**/agent-*.jsonl`，同名 `.meta.json` 的 `agentType` 是类型 | 同主会话，记 agent_id 和 agent_type，session_id 记父会话 |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` | 见下 |
| 派活记录 | `HOME/codex_runs.jsonl` | §9 |

- 增量：`usage_files` 记每个文件已读到的字节偏移（只停在完整行的末尾）。文件变小了就从头重扫，并先删掉这个文件产生的行（要能按文件删，按需要在扫描时记住来源；schema 里没有来源列时，按 session_id / rollout_id 删）。
- Claude 行：
  - `type == "assistant"` 且有 `message.usage`：一个请求一行，键 = `message.id`，没有就用 `requestId`；同一个键出现多次只记一次。字段：model、`effort`（行顶层）、usage 里的 input_tokens、cache_creation_input_tokens、cache_creation.ephemeral_5m_input_tokens / ephemeral_1h_input_tokens、cache_read_input_tokens、output_tokens、output_tokens_details.thinking_tokens、server_tool_use.web_search_requests / web_fetch_requests、service_tier、speed。output_tokens 已包含思考，thinking_tokens 只是细分。
  - 同一行 message.content 里的 `tool_use` 块 → claude_tool_calls。
  - `type == "user"`、不是 isMeta、不是 isCompactSummary、content 是字符串或含 text 块且不含 tool_result → claude_prompts。kind 按行上的 `origin.kind`：`human` → user，`peer` → peer，`task-notification` → task；没有 origin 字段的老记录记 user。prompts 数只算 user，干活时长按所有 kind 的轮次算。
  - `type == "system"` 且 `subtype == "compact_boundary"` → claude_compactions。
  - `type == "ai-title"` → claude_sessions.ai_title（取最后一次）。cwd、entrypoint、version 取最后一次出现的值；first_ts / last_ts 按行的 timestamp。
- Codex 行：`session_meta`（id、timestamp、cwd、originator、source、cli_version）；第一个 `turn_context`（model、collaboration_mode.settings.reasoning_effort、sandbox_policy.type）；最后一个 `event_msg/token_count` 的 `info.total_token_usage`（input_tokens 含缓存部分）；每个带 rate_limits 的 token_count 写一行 codex_rate_limits（primary.used_percent、window_minutes、resets_at 转 ISO、credits.balance、plan_type）；第一个用户消息文本前 200 字 → first_user_text。
- 解析错误：坏行跳过并计数，scan 结束时打印"跳过 N 行（文件:行号 前 3 个）"。

### 汇总（`crew/usage.py` 的函数，网页和命令行共用）

时间段 `period(name, now)` → (start, end)，按本机时区：`today`；`week` = 汇报周，上一个周四 00:00 到下一个周四 00:00；`all`；`YYYY-MM-DD..YYYY-MM-DD`。函数都返回 list[dict] 或 dict，键名用英文：

| 函数 | 内容 |
|---|---|
| `summary(conn, start, end, prices)` | Claude 各类 token 合计和折算金额；Codex token 合计、运行次数；最新的 Codex 额度快照 |
| `by_session(conn, start, end, prices)` | 每个主会话一行：显示名（crew 名 > ai_title > 会话 ID 前 8 位）、项目、分组、token 各列、金额、prompts 数、干活秒数、缓存命中率 = cache_read / (input + cache_write + cache_read)、子 agent 用量、派出的 Codex 次数和 token |
| `daily(conn, start, end, prices)` | 每天 × 会话的 token 和金额 |
| `codex_tasks(conn, start, end)` | 每次 Codex 运行：任务名（task_file 文件名，没有派活记录时用 first_user_text）、派活会话显示名、开始、用时、token、退出码、模型、强度 |
| `by_agent_type(conn, start, end, prices)` / `by_model(conn, start, end, prices)` | 分布 |

- 干活秒数：每个 prompt（任何 kind）到下一个 prompt 之前，最后一个请求的时间减 prompt 时间，求和。
- 金额：`prices.json` 形如 `{"claude-opus-5-5": {"input": 5, "output": 25, "cache_write_5m": 6.25, "cache_write_1h": 10, "cache_read": 0.5}}`，单位美元 / 百万 token。模型不在表里时金额为 null，汇总时单列"未计价 token"。
- 会话 → 分组：LEFT JOIN sessions。没加入 crew 的会话照样统计。

### 命令行（`crew/usage_cli.py`：`main(argv, home)`）

`usage scan`；`usage summary [--period P]`；`usage sessions [--period P]`；`usage daily [--period P]`；`usage codex [--period P]`；`usage agents`；`usage models`；`usage export --what sessions|daily|codex|requests --out FILE.csv [--period P]`。默认 period=week。表格用等宽对齐的纯文本；都支持 `--json`。

## 9. 派活记录

`codex-dispatch` 的 run_codex.ps1 每次派活在 Codex 退出后往 `HOME/codex_runs.jsonl` 追加一行（UTF-8）：

`{"run_id": "<guid>", "claude_session_id": "<CLAUDE_CODE_SESSION_ID 或 null>", "task_file": "...", "workdir": "...", "sandbox": "...", "model": null, "effort": "medium", "started_at": "...Z", "ended_at": "...Z", "exit_code": 0}`

扫描时用它匹配 codex_sessions：originator 是 `codex_exec`、cwd 与 workdir 的 norm_path 相同、started_at 在 [run.started_at - 60s, run.ended_at] 之间，取最早的一个；匹配不上 rollout_id 留空。

## 10. 开新会话和网页（第三单，届时补全）

- `spawn --name N --task T [--group G] [--role member|leader] [--cwd DIR | --new-worktree BRANCH]`：任务正文写 `HOME/spawn/<id>.md`，参数写 `<id>.json`；`wt.exe -w crew new-tab --title N --tabColor <色> powershell -NoProfile -ExecutionPolicy Bypass -File <plugin>/launch.ps1 -Spec <json>`。launch.ps1 设 CREW_* 环境变量后启动 `claude -n N --permission-mode bypassPermissions [--agent crew:leader] "<读取 spawn 文件并照做的英文指令>"`。会话退出时标签页随之关闭。
- `leader start --name N [--members A,B] [--task T]` = 建分组 + spawn(role=leader)。
- `web [--port 47811] [--open]`：只监听 127.0.0.1。页面：分组卡片、资源借还、文件归属、待拍板（可答复）、交接、日报、用量（§8 的各张表和按天堆叠图）、开新会话 / 开 leader 的表单。

## 11. 错误处理

1. 关键步骤之前出错就直接失败，带真实原因；不许"缺什么就不带什么"继续。
2. 不许静默兜底、不许隐式回退、不加兼容开关。
3. 打卡、写事件失败不影响会话（§5）。
4. 低层如实报错，由调用方决定。

## 12. 决定和没选的方案

| 决定 | 理由 | 没选 |
|---|---|---|
| 做成 Claude Code 插件 + 命令行 | 装一次所有项目可用；有版本可回退；飞书通道要求以插件形式提供 | 独立桌面程序 / Agent SDK 自写调度器：会话变成后台托管，用户不能直接点进会话；以后网页不够用再包桌面壳 |
| 白板用 SQLite，全机一份 | 多会话同时写；借设备要先到先得 | 每项目一堆 JSON 文件：并发写会互相覆盖 |
| leader 是普通会话 + 角色定义，一个 leader 管一个分组，可以并存 | 用户要能直接点进每个会话；分组按"一个部分"划分 | 官方 Agent teams：队友只能由 leader 新生，不能收编已有会话，恢复后队友丢失，Windows 只能挤在一个终端 |
| 全放行模式 + 守卫黑名单 | 夜里不卡确认框；模式一致后跨会话消息不用批准 | 逐条确认 + 白名单：写 `.claude` 目录在除全放行外的所有模式都会弹框 |
| 新会话开在 Windows Terminal | 命令行版能带名字和任务直接开跑；飞书通道只能挂在命令行版上 | VS Code 面板：外部打开的会话不会自动开跑（未完全验证） |
