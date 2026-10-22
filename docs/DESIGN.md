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
      spawn.py                        后台会话启动计划、任务文件、CLI 转网页服务
      chat.py                         后台成员/leader CLI、流式协议、聊天记录与恢复
      web.py  web/                    工作区网页和 leader 聊天，含本地 Markdown 库、HarmonyOS Sans 字体、logo
      vscode_sessions.py              复现 VS Code 会话列表与归档名单（只读），供待收编和归档同步
      presentation.py session_ops.py  待处理事项（QQ 推送用）与可撤销会话整理
      qq.py notifications.py          QQ 官方机器人传输、收发与落库
      claude_limits.py autostart.py   官方本地额度快照、当前用户登录自启
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
| `spawn/<id>.json`、`spawn/<id>.md` | 开新会话时的参数和任务正文，成员和网页 leader 共用 |
| `chat/<会话ID>.jsonl` | 按 seq 追加的网页聊天事件，含文本、工具调用/结果和状态，新事件带 `at`；不存 thinking |
| `chat/files/<会话ID>/` | 发给 leader 的截图，journal 只记文件名 |
| `web.json` | 正在运行的本机网页服务地址，供 CLI 连接；服务退出时移除自己的地址 |
| `codex_runs.jsonl` | 派活脚本记的派活开始与结束（§9） |
| `claude-limits.json` | 网页 Claude 的额度事件快照，按窗口保留采集时间 |
| `qq.json` | 用户维护的 QQ 机器人 app_id / app_secret；只读，不存在时不启动收发线程 |
| `notify.json` | Crew 维护 owner_openid、since、manual_away、current_leader_session_id、leader_numbers；加锁直接写 |
| `web-autostart.log` | 登录自启入口及后台服务的输出 |

所有函数显式接收 `home` 参数；命令行有全局参数 `--home DIR`（默认 `store.default_home()`），测试一律传临时目录。**不用环境变量改 HOME。**

拍板存储：

| 表 | 内容 |
|---|---|
| `decisions` | 题目、选项、建议、答复与状态；挂起时 `status` 仍为 `open` |
| `decision_holds` | `decision_id PRIMARY KEY REFERENCES decisions(id)`、`held_at TEXT NOT NULL`；只记录挂起标记，答复或撤回时删除 |

`decision_holds` 由 `schema.sql` 的 `CREATE TABLE IF NOT EXISTS` 在连接数据库时创建，不改变现有表结构、不需要迁移。`board.decisions` 返回 `held` 布尔值和可空的 `held_at`，默认仍列出全部 open 题，供下属侧 `crew` 沿用未答逻辑。

## 2. 身份和项目

- **会话 ID**：钩子从输入 JSON 的 `session_id` 取；命令行从环境变量 `CLAUDE_CODE_SESSION_ID` 取（Claude Code 会给工具命令设这个变量）。命令行也接受 `--session ID`。需要会话身份的命令拿不到 ID 时报错退出（exit 1），并说明"请在 Claude 会话里运行，或加 --session"。不需要身份的命令（status、decisions、answer 等）在普通终端里也能用，此时操作人记作 `user`。
- **crew 名**：人起的稳定名字（雷达、视觉）。同一项目里，未离线（status 不是 offline / closed）的会话不能重名，重名时 join 报冲突（exit 3）。
- **cc_name**：Claude Code 里显示的会话名，也是 SendMessage 的地址。由 `crew join` 和 `crew who` 调 `claude agents --json`（返回数组，元素有 sessionId、name、status、pid、cwd、kind），按 sessionId 匹配取 name。调用失败时 cc_name 留空，并打印一行原因。
- **网页收编**：候选和 VS Code 会话列表一致（`vscode_sessions`）：项目根目录及其下 git worktree 的会话记录，按扩展规则排除程序会话、sidechain 等，减去 VS Code 的 `hiddenSessionIds`（只读 `state.vscdb`，读失败保留上次名单并报 `vscode_error`）；再并入还没有会话记录的活进程。分三组：`running`（在运行、未归组）、`others`（没在运行、未归档）、`left`（有 crew 名但不在任何组）；leader、已入组、Crew 已归档或移出、status=closed 的不列。收编时调用 `board.register_session`（已有名字的走改组和改名），进有 leader 的组时给 leader 排通知。也可以在会话里由用户说"收编到某某"，会话自己 `crew join --group 某某 --scope … --done-when …`。
- **spawn 带来的身份**：crew 开的会话启动时带环境变量 `CREW_NAME`、`CREW_GROUP`、`CREW_ROLE`、`CREW_SPAWN_ID`。SessionStart 钩子看到这些变量就直接登记，不用再 join。
- **项目判定**：会话的 cwd 落在某个已 init 项目的 root 下（`store.is_under`），就属于这个项目；多个项目嵌套时取 root 最长的。不属于任何项目的会话：钩子只做守卫的黑名单检查，其它什么都不写。
- **worktree / branch**：cwd 所在 git 仓库的 toplevel 和当前分支（`git -C cwd rev-parse --show-toplevel`、`git -C cwd branch --show-current`），在 SessionStart、join 和网页收编时用 `hooks.git_details` 取。

## 3. 状态

| status | 含义 | 谁改 |
|---|---|---|
| working | 正在干活 | UserPromptSubmit；PostToolUse（提问框 / 计划审批结束后） |
| waiting | 等人（status_note 写原因） | Notification；PreToolUse 的 AskUserQuestion / ExitPlanMode |
| idle | 一轮做完，空着 | SessionStart；Stop |
| offline | 会话退出了 | SessionEnd |
| closed | 已归档或移出 Crew；不表示进程已停止 | 会话操作，或 `crew leader close --with-members` |

`claude agents --json` 里没有这个会话的进程时，网页和 `crew status` 显示为离线，但不改库；closed 始终保留为已归档。

当前进程还没有 Crew 钩子记录的会话，显示状态取 agents 的 busy → working、idle → idle；钩子生效后保留 waiting 等细分状态。网页服务自己管理的会话还会根据发送、流式输出、result 和进程退出更新聊天状态。真实 hook 的 waiting 不会被日志里的旧 idle 覆盖。

## 4. 命令行

全局：`crew [--home DIR] <子命令> ...`。输出给人看，用中文；带 `--json` 的命令输出 JSON（UTF-8）。退出码：0 成功；1 出错；2 参数错（argparse）；3 冲突（被借着、重名、归属冲突）。

| 命令 | 作用 |
|---|---|
| `install` | 写 HOME 下的 runtime.json、crew.py、bin/crew.cmd；blacklist.txt、prices.json 不存在时从 defaults 复制。可重复执行 |
| `init [--name N] [--root DIR]` | 启用项目。root 默认是当前目录的 git toplevel，name 默认取 root 的目录名 |
| `projects` | 列出项目 |
| `resource add NAME [--kind device\|dir\|app\|other] [--desc T] [--guard REGEX]` / `resource list` / `resource rm NAME` | 资源。guard 正则先 `re.compile` 校验，非法就报错 |
| `join NAME [--task T] [--scope S] [--done-when D] [--role member\|leader] [--group G]` | 当前会话认领 crew 名，可同时写任务卡；省略 --task 保留原任务。role=leader 时同时建（或接管）同名分组；进了有 leader 的组时给 leader 排通知 |
| `card [--scope S] [--done-when D] [--json]` | 更新当前会话任务卡（只改给出的字段） |
| `whoami [--json]` | 我是谁、属于哪个项目和分组、我的 leader 是谁（含 leader 的 cc_name）、任务卡 |
| `task TEXT` | 更新"在做什么" |
| `status [--project P] [--json]` | 总览：按分组列会话（状态、在做什么、最后活动时间）、未分组的会话、借出的资源、文件归属、未答的待拍板条数 |
| `who NAME [--json]` | 某个会话的详情和它现在的 SendMessage 地址 |
| `lease take RES --for T [--until HH:MM] [--overwrites T]` | 借。已被别人借着就打印借用人、用途、借出时间、预计归还，exit 3。自己已借着就更新用途 |
| `lease give RES [--note T]` | 还。只能还自己借的；`--force --reason T` 可以还别人的（leader 或用户本人用），记事件 |
| `lease list [--json]` | 借出中的资源 |
| `claim PATTERN [--note T]` / `unclaim PATTERN\|--all` / `claims [--json]` | 文件归属（§6） |
| `ask QUESTION [--option T]... [--recommend T] [--blocking]` | 记一条待拍板，打印编号 |
| `decisions [--all] [--json]` | 待拍板（默认只列 open） |
| `answer ID TEXT` | 答复；answered_by 为当前会话 ID，普通终端里为 `user` |
| `withdraw ID` | 撤回自己的问题 |
| `handoff --to NAME\|GROUP\|* TEXT` / `handoffs [--json]` | 交接。`handoffs` 列出发给我（我的名字、我的分组、*）还没送达我的 |
| `report TEXT` / `reports [--since YYYY-MM-DD] [--group G] [--json]` | 日报 |
| `group list [--json]` | 分组和成员 |
| `roster add GROUP NAME...` / `roster remove GROUP NAME...` / `roster move NAME --to GROUP` | 花名册。一个会话只在一个分组里 |
| `leader close GROUP [--with-members]` | 收掉分组：closed_at 置上；成员的 group_name 清空，带 `--with-members` 时成员 status 改为 closed。不杀进程 |
| `events [--session NAME] [--limit N]` | 事件流水 |
| `doctor` | 检查：数据库能打开、blacklist.txt 每行合法、runtime.json 的 plugin_root 存在、当前目录属于哪个项目 |
| `usage ...` | 转给 `crew.usage_cli.main(argv, home)`（§8） |
| `spawn ...` | 通过运行中的网页服务开后台成员会话；dry-run 只看计划（§10） |
| `leader start --name N [--members A,B] [--task T] [--dry-run]` | 调用运行中的网页服务开 leader；服务没开时报错，dry-run 只看计划（§10） |
| `web [--port 47811] [--open]` | 本机工作区网页；--open 按 cwd 绑定工作区并复用已有服务（§10） |
| `autostart install\|status\|uninstall` | 当前用户登录自启，不立即启动、停止服务或弹浏览器 |

白板写操作同时写一条 events；聊天输出另记在 `chat/<会话ID>.jsonl`。

## 5. 钩子

入口 `python "${CLAUDE_PLUGIN_ROOT}/crew_hook.py" <事件>`，从 stdin 读 UTF-8 JSON（字段：session_id、transcript_path、cwd、hook_event_name、permission_mode，以及各事件自己的字段）。注册见 [plugin/hooks/hooks.json](../plugin/hooks/hooks.json)。

| 参数 | 事件 | 做什么 | 输出 |
|---|---|---|---|
| `session-start` | SessionStart | **不管属不属于项目都先跑 install**（刷新 runtime.json；黑名单不存在时复制默认的，守卫在所有会话里都要用到它）。属于某项目时登记 / 更新会话（带 CREW_* 变量就按它登记；已登记过的会话保留 crew 名）；status=idle。cwd 不是 git 仓库时 worktree / branch 记为空 | 属于项目时输出 `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}`，中文告诉会话：项目名、我的 crew 名（没收编时写明不要自己 crew join，等用户说收编）、分组和 leader（含 leader 的 cc_name）、命令行怎么调（`python "<HOME>/crew.py" ...`）、守则 `/crew:member`（leader 是 `/crew:leader`）、发给我还没送达的交接 |
| `user-prompt-submit` | UserPromptSubmit | status=working，last_prompt。查我提的、已答复但没送达（delivered_at 为空）的待拍板，和没送达我的交接 | 有内容时输出 UserPromptSubmit 的 additionalContext，并标记送达 |
| `stop` | Stop | status=idle，last_message 取输入里的 `last_assistant_message` 前 300 字 | 无 |
| `notification` | Notification | status=waiting，status_note 取输入里的 `message` | 无 |
| `pre-ask` | PreToolUse（AskUserQuestion、ExitPlanMode） | status=waiting，note 写"等你回答提问"或"等你审批计划" | 无 |
| `post-ask` | PostToolUse（同上） | status=working | 无 |
| `session-end` | SessionEnd | status=offline，ended_at | 无 |
| `guard` | PreToolUse（所有工具） | §7 | 拦截时 stderr + exit 2 |

- 每个打卡事件写一条 events，detail 里记输入 JSON 的键名列表和该事件关心的字段（不存 transcript 全文、不存 tool_input 全文，命令截前 200 字）。守卫只在拦截时写事件，放行的调用不记（否则每次工具调用都写一行）。
- 插件装上或项目启用之前就开着的会话没有 SessionStart 记录：它的第一个打卡事件会先补登记再改状态。
- 打卡类事件出错不能影响会话：打一行原因到 stderr，exit 1。**除 guard 外任何事件都不能 exit 2。**
- 会话的 crew 名、cc_name 不在钩子里调 `claude agents`（太慢）。

显式 session_controls 的归档/移出高于上述 hook 状态，closed 不因启动、Stop 或 SessionEnd 改回活跃。compact 只续上下文，保留既有身份、分组与状态，不重放旧 CREW_*。没有显式控制记录的旧 close_group 仍保留主动启动后重开的既有行为。

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
- Codex 行：`session_meta`（id、timestamp、cwd、originator、source、cli_version）；第一个 `turn_context`（model、collaboration_mode.settings.reasoning_effort、sandbox_policy.type）；最后一个 `event_msg/token_count` 的 `info.total_token_usage`（input_tokens 含缓存部分）；每个带 rate_limits 的 token_count 写一行 codex_rate_limits（主键 rollout_id + ts；primary.used_percent、window_minutes、resets_at 转 ISO、credits.balance、plan_type）；first_user_text 取用户消息里第一个不以 `<` 开头、也不以 `# AGENTS.md instructions` 开头的 input_text 前 200 字（Codex 会先塞 AGENTS.md 和环境信息）。session_meta.source 对 Codex 自己的子 agent 是对象，存成 JSON 字符串。usage 里的细分字段（cache_creation、output_tokens_details、server_tool_use）在老记录里可能是 null，按 0 计。
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

`usage [--project NAME] scan`；`usage summary [--period P]`；`usage sessions [--period P]`；`usage daily [--period P]`；`usage codex [--period P]`；`usage agents`；`usage models`；`usage export --what sessions|daily|codex|requests --out FILE.csv [--period P]`。默认 period=week，CLI 默认全机，`--project NAME` 按实际工作目录筛选（包括未收编会话）；网页默认当前工作区。额度始终账户范围。表格用等宽对齐的纯文本；都支持 `--json`。

## 9. 派活记录

`codex-dispatch` 的 run_codex.ps1 每次派活在 Codex 退出后往 `HOME/codex_runs.jsonl` 追加一行（UTF-8）：

`{"run_id": "<guid>", "claude_session_id": "<CLAUDE_CODE_SESSION_ID 或 null>", "task_file": "...", "workdir": "...", "sandbox": "...", "model": null, "effort": "medium", "started_at": "...Z", "ended_at": "...Z", "exit_code": 0}`

扫描时用它匹配 codex_sessions：originator 是 `codex_exec`、cwd 与 workdir 的 norm_path 相同、started_at 在 [run.started_at - 60s, run.ended_at] 之间，取最早的一个；匹配不上 rollout_id 留空。

## 10. 开新会话和网页

- `spawn --name N --task T [--group G] [--cwd DIR | --new-worktree BRANCH]` 默认开后台成员，调用网页服务的 `/api/spawn`。任务正文写 `HOME/spawn/<id>.md`，参数写 `<id>.json`；复用 leader 的持久 CLI、聊天记录和恢复逻辑。新会话不再开终端窗口。
- `web [--port 47811] [--open]` 只监听 127.0.0.1，运行期间把地址写入 `HOME/web.json`。一个项目一个标签页（`/?project=P`），裸网址列项目入口，页内不切项目。页面以和 leader 聊天为主：左栏 leader 列表、中间对话（待拍板托盘置顶）、右栏本组架构图与下属卡片，另有全部、待收编、用量页；下属不在网页发言。页面结构和交互见 [WEB_UX.md](WEB_UX.md)。前端用原生 JS 和本地 markdown-it 15.0.2，无构建流程；Python 后端仍只用标准库。
- `leader start --name N [--members A,B] [--task T]` 调本机网页服务的 `/api/leader`。`spawn` 和 `leader start` 都先要求运行 `crew web --open`，服务未运行时清楚报错；`--dry-run` 只生成启动计划，不连服务、不写白板、不启动进程。
- 成员和 leader 都复用 `spawn.prepare` 和任务文件；leader 额外建分组、收编/挪入选定成员。后台进程是 PATH 里的 Claude CLI，通用参数为 `-p --input-format stream-json --output-format stream-json --verbose --include-partial-messages --replay-user-messages --permission-mode bypassPermissions --name N`；仅 leader 追加 `--agent crew:leader`。保持 stdin 打开，不加 `--bare`，保留跨会话收件箱和插件钩子/守卫。任务 brief 指定相应的 `/crew:member` 或 `/crew:leader` 守则，CREW_ROLE 也按角色设置；未分组成员不设置 CREW_GROUP。
- 启动计划预分配 UUID，首次用 `--session-id`，把会话的角色、分组、cwd、spawn_id 记进白板；重启网页服务后，等用户下一次发言再用 `--resume` 恢复同 ID，并使用白板里当前的角色和分组。恢复前直接查活进程，同 ID 已在 VS Code 或其他进程里运行时拒绝双开。
- 浏览器刷新只重放 `HOME/chat/<会话ID>.jsonl`，不会启动新进程。聊天事件不含 thinking；assistant 的同 ID 分块合并，完整块替换对应流式文本，重复的 human replay 不再显示。组员发来的消息取 peer origin 的 body 和 name，不需要网页另投一次 stdin。
- 会话正在处理时，用户发送返回冲突，让这一轮结束；result 只代表一轮结束，CLI 仍保持在线。服务退出只关闭自己子进程的 stdin，让它正常结束。恢复前先等旧进程输出读完，避免旧状态覆盖新一轮。
- Windows 的 npm `claude.cmd` 会另起原生子进程；托管会话直接启动该安装目录的 `bin/claude.exe`，让 Manager 持有的 PID 与 `claude agents` 一致，交接时可以确认进程归属。
- 网页作为自己的交互宿主，给子进程设置 `CLAUDE_CODE_ENTRYPOINT=crew-web`。本机 VS Code 扩展会过滤 `sdk-cli` 等程序会话；使用自己的宿主标记后，原 ID 和历史可以被扩展发现。协议仍是 `-p/stream-json`，不修改 transcript 或扩展；交接按 Manager 实际持有的 PID 核对归属。
- `crew open NAME` 与网页使用相同的两步流程：先 `code <cwd>` 打开正确目录；用户确认窗口已经就绪后，再打开 `vscode://anthropic.claude-code/open?session=<ID>`。第一步不关闭会话，取消保留原运行端。第二步重新查询 agents 和 PID 注册文件；忙会话拒绝交接，网页进程须属于当前 Manager，确认后仅关闭该进程的 stdin 并等正常退出。遗留终端会话只有明确确认且空闲才关闭，并核对 PID 创建时间；VS Code 进程永远不关闭。
- 有网页聊天记录的 `crew open` 通过 `HOME/web.json` 调同一服务，确保释放的是 Manager 管理的进程。在 VS Code 里继续时网页保留历史并禁用发言，避免同时恢复两份。

网页接口：

| 接口 | 内容 |
|---|---|
| `GET /api/state?project=P` | `project`、`projects`（只有名字）、`leaders`（状态、未读、`read_seq`、本组未挂起的待拍板数 `pending`、待发通知数、成员）、`members`（有名字的非 leader：所在位置 bg / vscode / offline、任务、任务卡、最近 5 条进展、运行中和 24 小时内的 Codex、借用与文件归属）、`decisions`（只含未答，带提问者、其 leader、`held` 布尔值、可空 `held_at`）、`candidates`（三组）、`limits`（Claude 两个窗口、Codex，拿不到为 null）、`attention`（QQ 推送用，跳过挂起题）、`notification`（含 channel 状态与 detail）、`agents_error`、`vscode_error`。journal 按文件签名缓存 |
| `POST /api/project/init` | name、root；启用已有本机目录 |
| `POST /api/roster/adopt` | session_id、name、可选 group；没在运行的候选也能收编；进有 leader 的组时排 leader 通知 |
| `POST /api/roster/move` / `POST /api/roster/remove` | 换组 / 退组，按 session_id |
| `POST /api/group/close` | group、with_members；均不停止进程 |
| `POST /api/answer` | id、text；保存后给提问者的 leader 排通知（来源 web），返回 `routed_to` |
| `POST /api/decision/hold` | id；挂起未答题，首次挂起时给提问者的 leader 排通知（来源 web，无 leader 时不排），重复挂起不重复通知 |
| `POST /api/decision/unhold` | id；取消挂起，放回待拍板，不排通知 |
| `POST /api/spawn` | name、task、可选 group / role / cwd / new_worktree / base |
| `POST /api/leader` | name、task（成员可选）；起 leader |
| `GET /api/chat/history?project=P&session_id=S` | session_id、events、running、busy、read_seq |
| `GET /api/chat/events?project=P&session_id=S&after=N` | SSE 重放 seq 大于 N 的事件，兼读 Last-Event-ID |
| `POST /api/chat/send` | session_id、text、可选 images（最多 5 张、单张 ≤ 5 MB）；只允许 leader |
| `POST /api/chat/read` | session_id、seq；已读位置只前进 |
| `GET /api/chat/file?project=P&session_id=S&name=N` | 读截图，只允许 journal 里出现过的文件名 |
| `POST /api/session/stop` | session_id；向本服务托管且正在干活的会话写 stream-json `control_request`（subtype=interrupt） |
| `POST /api/session/rename\|archive\|remove\|restore\|undo` | 精确 session_id；返回 `session` 与供撤销的 `before` |
| `POST /api/open` / `POST /api/open/continue` | 两步转到 VS Code |
| `POST /api/notifications/away`、`POST /api/attention/dismiss` | 手动离开、收起待处理 |
| `GET /api/usage` / `GET /api/usage/export` | 用量（含 leader → 下属 → Codex 的 `tree`）与 CSV |

静态文件只有白名单路径；`/logo.svg` 和两个字体带一周缓存，其余 `no-store`。

### 会话整理、工作区视图与通知

- `session_controls` 保存显式归档、移出和改名。移出不删会话与历史，hook 不会自动收编回来；主动 join 可重新加入。leader 改名同步组与成员归属，pending handoff 跟随；撤销恢复操作前的记录，不操作进程。
- **leader 通知**：Crew 自己不发会话间消息。要告诉下属的事（网页 / QQ 答完拍板、网页挂起拍板、收编进组、`crew join` 入组）写进 `leader_notices`；网页服务每 2 秒把待发通知合并成一条 `[Crew]` 消息写进空闲的 leader（journal 记 source=crew），由 leader 用 SendMessage 转告下属。挂起通知附题号、提问者名字与 cc_name，要求转告"不卡流程的按推荐继续；卡流程的先做别的，先别再问这道题"；没有 leader 时不排。leader 对挂起题不催用户，汇报时单独列出。leader 在别的进程里运行或进程状态未知时不投递。
- **拍板挂起**：托盘底部的"挂起 · N"区默认折叠，全部页等你拍板下单独分组；可直接作答或取消挂起。leader 角标、左栏待拍板数、全部页统计与标签标题只数未挂起题；新题提示音只响未挂起的新题，挂起或取消挂起本身不响。取消挂起不通知任何人。
- **归档同步**：现有 10 秒轮询里读 VS Code 归档名单，已收编会话被 VS Code 归档就 `session_ops.archive`，记 `vscode:archived`；VS Code 取消归档不跟随。
- **已读与任务卡**：`read_cursors` 记用户读到 leader journal 的哪条，算未读和"离开后"分隔线；`session_cards` 记下属的负责范围和完成标准，`sessions.task` 仍是"现在在做什么"。
- `POST /api/session/rename|archive|remove|restore|undo` 用精确 session_id；返回 `session` 与供撤销的 `before`。`POST /api/attention/dismiss` 可撤销标记，检查事项所属工作区。归档/移出会话的未答题保留记录，但不继续催办。
- 用量接口及 CSV 接受 `project=P&scope=workspace|machine`。报告函数末尾可选 `project=None`，按 cwd 最长根目录匹配；跨目录 Codex 按任务工作目录统计。`summary.claude_limits` 来自官方本地缓存与 stream-json `rate_limit_event`，不轮询私有 OAuth 接口，不启动模型来查额度。
- 派活脚本仓库副本在 `integrations/codex-dispatch/run_codex.ps1`：同 run_id 先写开始、再写结束，scanner upsert。只有未关联的 rollout 时状态为 unknown，最后日志时间不当完成时间。
- QQ 由 `qq.Client(app_id, app_secret)` 通过 WebSocket 收事件、HTTPS 主动发送；重连交给传输层。`run` 抛 `QQError` 时保留真实原因并停止收发，不在通知层重试。首次私聊绑定主人，此后其他人的消息和按钮只记日志。
- `notification_messages` 保存发送结果的 `id` / `ref_idx` 与问题、leader 的关系；`notification_inbound` 按 message_id / interaction_id 去重；`notification_replies` 排队并记录每条 QQ 来信的回复边界。凭证和回调临时票据不入库。发送失败保留明确错误，不自动重发；拒收时提示在手机 QQ 打开“允许主动发送”，下一次发送成功恢复通道状态。
- 离开期间 leader 每跑完一轮，最后一段话立即发到 QQ（以进入离开时各 leader 的对话位置为起点，QQ 发起的轮次除外，按 `turn:<会话>:<idle 序号>` 记录防重发）。
- 通知只在手动离开或桌面锁屏时主动发送：手动点“我已离开”时当下待推的立即发出（用户 10-23：手动应该秒推）；锁屏和离开期间新出现的事项约 30 秒合批；每道未挂起的未答拍板单独发 Markdown 和单选回调按钮，推荐项在前，无选项或超过 5×5 容量时提示引用回复，其余事项合成一条文字。期间已处理或挂起的事项不发送，发送前再次读取挂起标记，已推送的事项不再推。锁屏来自已有桌面代理的带 token POST `/api/status`，不启动代理、不操作桌面；代理故障单独提示。
- QQ 私聊用命令集（`/h`、`/ls`、`/1`、`/q`、`/back`），普通文字发给当前 leader。拍板通过按钮或引用该条拍板消息答复；按钮先应答 interaction，再校验题号、选项和题目状态。保存答复的 actor 与给 leader 排的通知 source 均为 `qq`。`/q` 只重发未挂起题，最多 10 题并说明剩余数，末尾有挂起题时说明"另有 N 道挂起"；`/ls` 的待拍板数同样排除挂起题。
- 网页 leader 的 QQ 消息进入同一 input stream，journal 标记 source=qq；马上回执已转交或忙时排队，回复截至这条消息之后第一个 idle/offline，结束后发回最后一段文字。长回复按段落切分，`TEXT_LIMIT = 2000` 待主控实测；没有文字、启动失败等都给明确答复，不在网页后台运行的 leader 明确拒绝。截图复用网页图片校验和保存（最多 5 张、单张 ≤ 5 MB），语音用平台识别文字，视频和文件明确拒绝。详见 [QQ.md](QQ.md)。
- 网页 `notification.channel` 提供 unconfigured、unbound、connecting、connected、reconnecting、rejected、error 和真实 detail；左下角显示 QQ 状态与绑定、拒收操作提示，无通道开关。
- `autostart` 用当前用户 InteractiveToken 计划任务，普通权限、登录触发、隐藏运行，不保存密码。任务指向主检出；默认端口已有 Crew 时保留原服务，不重复启动。不在睡眠、休眠或注销期间维持收信。
- leader 巡查只按需：用户问时 leader 再去查，不做定时巡查。

## 11. 错误处理

1. 关键步骤之前出错就直接失败，带真实原因；不许"缺什么就不带什么"继续。
2. 不许静默兜底、不许隐式回退、不加兼容开关。
3. 打卡、写事件失败不影响会话（§5）。
4. 低层如实报错，由调用方决定。

## 12. 决定和没选的方案

| 决定 | 理由 | 没选 |
|---|---|---|
| 做成 Claude Code 插件 + 命令行 + 本机网页 | 装一次所有项目可用；复用钩子、守卫和角色定义；网页负责看板和会话对话 | 独立桌面壳：目前本机网页已能承载工作流 |
| 白板用 SQLite，全机一份 | 多会话同时写；借设备要先到先得 | 每项目一堆 JSON 文件：并发写会互相覆盖 |
| leader 是普通会话 + 角色定义，一个 leader 管一个分组，可以并存 | 用户要能直接点进每个会话；分组按"一个部分"划分 | 官方 Agent teams：队友只能由 leader 新生，不能收编已有会话，恢复后队友丢失，Windows 只能挤在一个终端 |
| 全放行模式 + 守卫黑名单 | 夜里不卡确认框；模式一致后跨会话消息不用批准 | 逐条确认 + 白名单：写 `.claude` 目录在除全放行外的所有模式都会弹框 |
| 成员和 leader 统一用常驻 CLI，网页收发 stream-json | 用户默认在网页里看对话和工具，需要时转到 VS Code；CLI 保留跨会话收件箱、transcript 和原会话 ID，可恢复 | 新成员开终端窗口：已按用户确定的工作流改为默认后台；Agent SDK：当前 CLI 已满足需求，无需增加 Python 依赖 |
| 网页只和 leader 聊，下属在 VS Code 里聊；leader 当中转站（2026-10-22 用户拍板，参照 Paperclip 的 CEO） | 用户先在 VS Code 里和会话聊出需求，收编后仍习惯在那里直接说；集中处理进展和拍板靠 leader | 网页上也和下属聊：同一会话同时只能在一个进程里跑，VS Code 里的会话收不到可靠的网页消息 |
| Crew 不发会话间消息，要告诉下属的事写 leader 通知，由 leader 转告 | 只有会话自己能调用 SendMessage；leader 本来就在网页托管，能直接写进它的输入 | Crew 自己做一套发消息机制：官方跨会话收件入口没有可靠送达确认 |
| 待收编按 VS Code 会话列表口径，VS Code 归档单向同步 | 用户在 VS Code 侧栏里管理会话，两边看到的应一致 | 只列活进程（漏掉没在运行的会话）；跟随取消归档（少见，代码量约三倍） |
