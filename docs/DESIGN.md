# Crew 设计

Crew 让一个人同时指挥多个 Claude Code / Codex 会话：每个会话管一个领域，leader 管一组会话；所有人共用白板（任务、设备、文件归属、拍板、人工待办、交接、日报、用量）。执行工具由创建时选择，组织概念保持统一。

本文是施工依据。表结构以 [plugin/crew/schema.sql](../plugin/crew/schema.sql) 为准，存储入口以 [plugin/crew/store.py](../plugin/crew/store.py) 为准，这两个文件只由主控改。

## 0. 2026-10-24 更新

- `session_runtimes` 保存 Crew ID → 执行工具 / 原生 ID；缺记录的旧会话仍是 Claude。`chat.Manager` 保持统一入口，Codex 由独立 driver 处理协议、状态、线程恢复与生命周期。
- `peer_messages` 保存混合工具消息；消息身份来自宿主，原生 thread ID 不充当 Crew 权限。忙时排队，托管空闲时投递，外部 Claude 由下次 hook 收取。
- `todos` 是需要用户动手的事项；处理、审计与 leader 通知同事务，版本防止错误撤销。`todo_backfills` 去重首次盘点通知。
- `decision_briefs` 保存显式拍板摘要，`decision_reviews` 去重旧题补写；不自动猜测旧题意思。题目修订后远程旧按钮必须失效。
- `trust_settings` 按项目保存目标与模式，`trust_decisions` 保存自主取舍理由。报告汇总现有记录，不冒充独立验收。
- `workspace_comments` 绑定项目与 Crew 会话；横排总览可给组或成员留言，并通过既有 leader 通知链回流。
- `desktop/` 是独立 Electron 壳及工作区首页；Python 后端仍只用标准库。只退出自己启动的服务，不关闭借用服务。

当前用户流程以 [WORKFLOWS.md](WORKFLOWS.md) 和 [CODEX_INTEGRATION.md](CODEX_INTEGRATION.md) 为准；下文保留原有 Claude 工作流与既有功能细节。

## 1. 结构

```
Crew/
  .claude-plugin/marketplace.json     本仓库同时是一个插件市场，只有一个插件 crew
  plugin/                             插件根目录（运行时的 ${CLAUDE_PLUGIN_ROOT}）
    .claude-plugin/plugin.json
    hooks/hooks.json                  钩子注册（主控写）
    agents/leader.md  agents/git.md   leader 与 Git 管理员角色
    skills/member|leader|git|trust/   守则和 /crew:trust
    crew_hook.py                      钩子入口：python crew_hook.py <事件>
    crew_cli.py                       命令行入口：python crew_cli.py <子命令>
    crew/                             Python 包，只用标准库（Python 3.11+）
      store.py  schema.sql            存储（主控）
      board.py                        白板读写（项目、会话、分组、资源、借还、归属、拍板、交接、日报、事件）
      cli.py                          命令行解析和输出
      startup.py menu.py              git 项目快速启动、当前用户右键菜单与 pythonw 入口
      themes.py                       项目主题目录及浅 / 深色 token
      hooks.py                        各钩子事件
      guard.py                        守卫
      install.py                      写 ~/.crew 下的启动器、PATH 垫片、默认文件
      usage.py  usage_cli.py          用量扫描、汇总、命令行
      spawn.py                        后台会话启动计划、任务文件、CLI 转网页服务
      chat.py                         后台成员/leader CLI、流式协议、聊天记录与恢复
      channel.py                      项目交流区、点名通知与每轮未读摘要
      channel_digest.py               Haiku 累积要点卡与后台整理互斥
      models.py                       模型 / effort 唯一清单与会话设置
      git_view.py                     Git 管理配置、分支检查、提交树、GitHub PR / CI 与缓存
      git_admin.py  git_ci.py          Git 管理员生命周期与后台 CI 通知
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

拍板、会话设置与守则阅读记录：

| 表 | 内容 |
|---|---|
| `decisions` | 题目、选项、建议、答复与状态；挂起时 `status` 仍为 `open` |
| `decision_holds` | `decision_id PRIMARY KEY REFERENCES decisions(id)`、`held_at TEXT NOT NULL`；只记录挂起标记，答复或撤回时删除 |
| `session_options` | `session_id TEXT PRIMARY KEY REFERENCES sessions(session_id)`、`model TEXT`、`effort TEXT`、`set_at TEXT NOT NULL`；两项都为 null 时删行，无记录表示账号默认 |
| `rule_reads` | `session_id TEXT PRIMARY KEY REFERENCES sessions(session_id)`、`role TEXT NOT NULL`、`digest TEXT NOT NULL`、`read_at TEXT NOT NULL`；记录会话读过的角色守则 sha256 |
| `git_management` | `project TEXT PRIMARY KEY REFERENCES projects(name)`、`enabled_at TEXT NOT NULL`、`base TEXT NOT NULL`、`stable TEXT`、`admin_session_id TEXT REFERENCES sessions(session_id)`、`branch_rules TEXT`；每项目一行，记录 Git 管理员与分支规则 |
| `git_merge_requests` | 项目、提出会话、分支、说明、状态、PR 号、结果说明、创建与更新时间；详见 §13 |
| `git_ci_state` | 按项目、检查对象与 CI 运行记录状态和通知去重；详见 §13 |
| `git_result_handoffs` | Git 结果与 handoff、接收组员及发送管理员的关联，沿用 handoff_deliveries 记账；详见 §13 |
| `leader_order` | `project TEXT NOT NULL REFERENCES projects(name)`、`session_id TEXT NOT NULL REFERENCES sessions(session_id)`、`position INTEGER NOT NULL`；联合主键 `(project, session_id)`，每项目独立保存左栏顺序，新 leader 排在已保存位置后并保持原相对顺序；连接数据库时通过 `CREATE TABLE IF NOT EXISTS` 创建 |
| `leader_priority` | `project TEXT NOT NULL REFERENCES projects(name)`、`session_id TEXT NOT NULL REFERENCES sessions(session_id)`、`position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 2)`；主键 `(project, session_id)`，唯一 `(project, position)`。按项目保存最多三位优先 leader 的顺序；不可见项保留在表中，展示时只对仍显示的 leader 连续编号，归档撤销后自然恢复。连接数据库时通过 `CREATE TABLE IF NOT EXISTS` 创建 |
| `project_themes` | `project TEXT PRIMARY KEY REFERENCES projects(name)`、`theme TEXT NOT NULL`、`assigned_at TEXT NOT NULL`；每项目一套主题，独立新表，不改变 `projects` |

`decision_holds` 由 `schema.sql` 的 `CREATE TABLE IF NOT EXISTS` 在连接数据库时创建，不改变现有表结构、不需要迁移。`board.decisions` 返回 `held` 布尔值和可空的 `held_at`，默认仍列出全部 open 题，供下属侧 `crew` 沿用未答逻辑。

`schema.sql` 删除旧的单项模型设置表，再用 `CREATE TABLE IF NOT EXISTS` 建立 `session_options` 和 `rule_reads`。模型与 effort 清单只在 `crew/models.py` 定义，`/api/state.models` 带出 id、显示名和上下文上限 `context_window`，`efforts` 带出 low / medium / high / xhigh / max，前端从这份数据生成选项。可选 Opus 5.5、Sonnet 5.5、Fable 5.1、Haiku 4.5；默认跟账号设置走。Haiku 不支持 effort，显式同时指定 Haiku 和非空 effort 报错，切换到 Haiku 会清除原存 effort。

交流区表：

| 表 | 字段与约束 |
|---|---|
| `channel_messages` | `id INTEGER PRIMARY KEY AUTOINCREMENT`、`project TEXT NOT NULL REFERENCES projects(name)`、`speaker_session_id TEXT NOT NULL`（leader 的 session_id 或 `user`）、`speaker_name TEXT NOT NULL`、`body TEXT NOT NULL`、`created_at TEXT NOT NULL`；索引 `channel_messages_project(project, id)` |
| `channel_mentions` | `message_id INTEGER NOT NULL REFERENCES channel_messages(id)`、`session_id TEXT NOT NULL REFERENCES sessions(session_id)`、`name TEXT NOT NULL`、`notice_id INTEGER REFERENCES leader_notices(id)`；联合主键 `(message_id, session_id)` |
| `channel_reads` | `project TEXT NOT NULL REFERENCES projects(name)`、`reader TEXT NOT NULL`（会话 session_id 或 `user`）、`message_id INTEGER NOT NULL`、`updated_at TEXT NOT NULL`；联合主键 `(project, reader)` |
| `channel_digests` | `id INTEGER PRIMARY KEY AUTOINCREMENT`、`project TEXT NOT NULL REFERENCES projects(name)`、`upto_message_id INTEGER NOT NULL REFERENCES channel_messages(id)`、`body TEXT NOT NULL`、`message_count INTEGER NOT NULL`（本次新整理条数）、`trigger TEXT NOT NULL CHECK (trigger IN ('manual', 'auto'))`、`model TEXT NOT NULL`、`cost_usd REAL NOT NULL`、`created_at TEXT NOT NULL`；索引 `channel_digests_project(project, id)` |

发言人名字和点名名字保留发言时的快照。`channel_mentions.notice_id` 关联本次点名的 leader 通知；非空表示已排通知，摘要据此排除该点名发言，不依赖 `leader_notices.sent_at`；自己点自己保留点名但 notice_id 为 null，仍按自己的发言排除。每个 leader、读全文的组员与用户分别维护游标，更新只前进；用户未读数不含自己发的言，空频道最新 id 为 0。连接数据库时用 `CREATE TABLE IF NOT EXISTS` 创建缺失表。每项目 id 最新的一张是当前累积卡，原始发言不删除；返回的 `total_message_count` 按该项目 id ≤ `upto_message_id` 的原文实数计算。上线由主控先建 `channel_digests` 表，再重启网页服务。

## 2. 身份和项目

- **项目主题**：`themes.py` 定义六套主题，state 下发浅 / 深色 token。分配与切换规则见 [WEB_UX.md 的外观](WEB_UX.md#外观)；存储在 `project_themes`，切换记录 `project:theme` 事件。浅 / 深 / 跟随系统是浏览器独立设置。

- **会话 ID**：钩子从输入 JSON 的 `session_id` 取；命令行从环境变量 `CLAUDE_CODE_SESSION_ID` 取（Claude Code 会给工具命令设这个变量）。命令行也接受 `--session ID`。需要会话身份的命令拿不到 ID 时报错退出（exit 1），并说明"请在 Claude 会话里运行，或加 --session"。不需要身份的命令（status、decisions、answer 等）在普通终端里也能用，此时操作人记作 `user`。
- **crew 名**：人起的稳定名字（雷达、视觉）。同一项目里，未离线（status 不是 offline / closed）的会话不能重名，重名时 join 报冲突（exit 3）。
- **cc_name**：Claude Code 里显示的会话名，也是 SendMessage 的地址。由 `crew join` 和 `crew who` 调 `claude agents --json`（返回数组，元素有 sessionId、name、status、pid、cwd、kind），按 sessionId 匹配取 name。调用失败时 cc_name 留空，并打印一行原因。
- **网页收编**：候选和 VS Code 会话列表一致（`vscode_sessions`）：项目根目录及其下 git worktree 的会话记录，按扩展规则排除程序会话、sidechain 等，减去 VS Code 的 `hiddenSessionIds`（只读 `state.vscdb`，读失败保留上次名单并报 `vscode_error`）；再并入还没有会话记录的活进程。分三组：`running`（在运行、未归组）、`others`（没在运行、未归档）、`left`（有 crew 名但不在任何组）；leader、已入组、Crew 已归档或移出、status=closed 的不列。收编时调用 `board.register_session`（已有名字的走改组和改名），进有 leader 的组时给 leader 排通知。也可以在会话里由用户说"收编到某某"，会话自己 `crew join --group 某某 --scope … --done-when …`。
- **spawn 带来的身份**：crew 开的会话启动时带环境变量 `CREW_NAME`、`CREW_GROUP`、`CREW_ROLE`、`CREW_SPAWN_ID`。SessionStart 钩子看到这些变量就直接登记，不用再 join。
- **项目判定**：钩子和守卫优先使用 `sessions` 表中会话登记的项目；未登记时才按 cwd 匹配已 init 项目的 root（`store.is_under`），多个项目嵌套时取 root 最长的。未登记且 cwd 不属于任何项目的会话：钩子只做守卫的黑名单检查，其它什么都不写。
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
| `start [--cwd DIR] [--name N]` | 从当前目录或指定目录找 git 根，未启用时自动启用；确保默认网页服务运行并打开项目页 |
| `menu install\|status\|uninstall` | 管理当前用户的文件夹与空白处右键入口，输出安装状态和实际命令 |
| `projects` | 列出项目 |
| `resource add NAME [--kind device\|dir\|app\|other] [--desc T] [--guard REGEX]` / `resource list` / `resource rm NAME` | 资源。guard 正则先 `re.compile` 校验，非法就报错 |
| `join NAME [--task T] [--scope S] [--done-when D] [--role member\|leader] [--group G]` | 当前会话认领 crew 名，可同时写任务卡；省略 --task 保留原任务。role=leader 时同时建（或接管）同名分组；进了有 leader 的组时给 leader 排通知 |
| `card [--scope S] [--done-when D] [--json]` | 更新当前会话任务卡（只改给出的字段） |
| `whoami [--json]` | 我是谁、属于哪个项目和分组、我的 leader 是谁（含 leader 的 cc_name）、任务卡 |
| `task TEXT` | 更新"在做什么" |
| `status [--project P] [--json]` | 总览：按分组列会话（状态、在做什么、最后活动时间、上下文占比）、未分组的会话、借出的资源、文件归属、未答的待拍板条数 |
| `who NAME [--json]` | 某个会话的详情、上下文占比和它现在的 SendMessage 地址 |
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
| `say TEXT [--to NAME]...` | 本项目 leader 在交流区发言，可重复点名同项目 leader；组员和未收编会话拒绝，点名不存在时报错（§10） |
| `channel [--limit N] [--raw]` | 默认先打印当前要点卡，再按时间正序列卡后最近 N 条全文（默认 20 条），没有新发言显示“要点之后暂无新发言”；无卡时看最近 N 条；`--raw` 跳过卡看最近 N 条原文。推进自己的读到位置；`--json` 保留消息数组，只含所选消息 |
| `group list [--json]` | 分组和成员 |
| `roster add GROUP NAME...` / `roster remove GROUP NAME...` / `roster move NAME --to GROUP` | 花名册。一个会话只在一个分组里 |
| `leader close GROUP [--with-members]` | 转发网页服务关闭分组并归档 leader；成员退组，带 `--with-members` 时一起归档。归档会停止网页托管进程 |
| `events [--session NAME] [--limit N]` | 事件流水 |
| `doctor` | 检查：数据库能打开、blacklist.txt 每行合法、runtime.json 的 plugin_root 存在、当前目录属于哪个项目 |
| `usage ...` | 转给 `crew.usage_cli.main(argv, home)`（§8） |
| `spawn ...` | 通过运行中的网页服务开后台成员会话；dry-run 只看计划（§10） |
| `wake NAME TEXT` | 同项目任何 leader 或用户叫醒离线组员，在后台续跑同 ID；leader 找别的 leader 时排通知，等它空闲送达（§10） |
| `compact [NAME]` | 经网页服务压缩会话；只有它自己的 leader 或用户可调用，网页 leader 省略 NAME 或指定自己时排到本轮结束后（§10） |
| `leader start --name N [--members A,B] [--task T] [--dry-run]` | 调用运行中的网页服务开 leader；服务没开时报错，dry-run 只看计划（§10） |
| `web [--port 47811] [--open]` | 本机工作区网页；--open 按 cwd 绑定工作区并复用已有服务（§10） |
| `autostart install\|status\|uninstall` | 当前用户登录自启，不立即启动、停止服务或弹浏览器 |

`status` / `who` 从网页服务 `/api/state` 的会话 `context` 原值取上下文用量，文本显示“上下文 42%（42.1 万 / 100 万）”；服务未运行时显示“上下文 未知”并写明“网页服务没开”，`context` 为 null 时同样显示未知及原因。`--json` 在每个会话里保留 `context` 的原始 `used` / `max`，并用 `context_reason` 说明未知原因，不另算一套用量。

白板写操作同时写一条 events；聊天输出另记在 `chat/<会话ID>.jsonl`。

## 5. 钩子

入口 `python "${CLAUDE_PLUGIN_ROOT}/crew_hook.py" <事件>`，从 stdin 读 UTF-8 JSON（字段：session_id、transcript_path、cwd、hook_event_name、permission_mode，以及各事件自己的字段）。注册见 [plugin/hooks/hooks.json](../plugin/hooks/hooks.json)。

| 参数 | 事件 | 做什么 | 输出 |
|---|---|---|---|
| `session-start` | SessionStart | **不管属不属于项目都先跑 install**（刷新 runtime.json；黑名单不存在时复制默认的，守卫在所有会话里都要用到它）。属于某项目时登记 / 更新会话（带 CREW_* 变量就按它登记；已登记过的会话保留 crew 名）；status=idle。cwd 不是 git 仓库时 worktree / branch 记为空。source 是 resume / compact 时检查守则版本，startup 不重复提醒 | 属于项目时输出 `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}`，中文告诉会话：项目名、我的 crew 名（没收编时写明不要自己 crew join，等用户说收编）、分组和 leader（含 leader 的 cc_name）、命令行怎么调（`python "<HOME>/crew.py" ...`）、守则 `/crew:member`（普通 leader 是 `/crew:leader`，Git 管理员是 `/crew:git`）、发给我还没送达的交接；需要时附守则更新提醒及 Git 管理提示 |
| `user-prompt-submit` | UserPromptSubmit | status=working，last_prompt。查我提的、已答复但没送达（delivered_at 为空）的待拍板，和没送达我的交接；检查守则版本；leader 读取交流区未读摘要 | 有内容时输出 UserPromptSubmit 的 additionalContext，并标记送达；需要时附守则更新提醒与交流区摘要 |
| `stop` | Stop | status=idle，last_message 取输入里的 `last_assistant_message` 前 300 字 | 无 |
| `notification` | Notification | status=waiting，status_note 取输入里的 `message` | 无 |
| `pre-ask` | PreToolUse（AskUserQuestion、ExitPlanMode） | status=waiting，note 写"等你回答提问"或"等你审批计划" | 无 |
| `post-ask` | PostToolUse（同上） | status=working | 无 |
| `session-end` | SessionEnd | status=offline，ended_at | 无 |
| `guard` | PreToolUse（所有工具） | §7；收到 `Skill` 调用且 `tool_input.skill` 是 `crew:leader` / `crew:member` / `crew:git` 时，记录对应技能文件当前 digest | 拦截时 stderr + exit 2 |

- 每个打卡事件写一条 events，detail 里记输入 JSON 的键名列表和该事件关心的字段（不存 transcript 全文、不存 tool_input 全文，命令截前 200 字）。守卫只在拦截时写事件，放行的调用不记。
- 插件装上或项目启用之前就开着的会话没有 SessionStart 记录：它的第一个打卡事件会先补登记再改状态。
- 打卡类事件出错不能影响会话：打一行原因到 stderr，exit 1。**除 guard 外任何事件都不能 exit 2。**
- 会话的 crew 名、cc_name 不在钩子里调 `claude agents`（太慢）。
- 守则版本是 `plugin/skills/<role>/SKILL.md` 的 sha256；角色取 `sessions.role`，Git 管理员按 `git_management.admin_session_id` 使用 git 守则。已启用项目中已收编、未归档的 leader / member，在 UserPromptSubmit（含会话间消息）和 resume / compact 的 SessionStart 检查 `rule_reads`；无记录或 digest 不同，在 additionalContext 提醒先调用 `/crew:<role>` 重读。调用相应 Skill 后更新 digest，文件再变则重新提醒。其余会话不提醒。热路径只读技能文件，不启动进程或联网。

Git 管理启用时，所有会话的 SessionStart 再附两段：管理员统一推送和合并、做完用 `crew git request` 转交的提示；`branch_rules` 前 6 行与“全文用 crew git policy 看”。规则为空时改为“git 管理员还没写分支规则，开工前先问它”。未收编会话同样收到，Git 管理员启动时的守则指向 `/crew:git`。
- 交流区摘要只在 leader 的 UserPromptSubmit 中追加，与守则提醒共用 additionalContext。按读到位置取本项目新发言，排除自己发的，以及点名当前 leader 且 `channel_mentions.notice_id` 非空的发言；无论通知是否已送达，都不重复进入摘要。当前卡覆盖到 leader 尚未读过的发言时，先附“[Crew 交流区要点]”与卡片正文，只列卡之后的未读发言；推进原有游标，所以卡片只在更新后第一次符合条件时出现。无卡或卡已经读过时沿用原逻辑。没有新发言与未读卡时不附加。新发言摘要第一行为“[Crew 交流区] 你上次看之后有 N 条新发言：”，每条一行 `- HH:MM <名字>（→ @A、@B）：<正文第一行>`，正文超过 120 字截断加“…”；最多 10 条，超过时追加“还有 M 条”，末尾写“全文用 crew channel 看”。读取后把该 leader 的位置推到最新；组员钩子不附交流区摘要。

显式 session_controls 的归档/移出高于上述 hook 状态，closed 不因启动、Stop 或 SessionEnd 改回活跃。compact 只续上下文，保留既有身份、分组与状态，不重放旧 CREW_*。整组关闭同样写入 session_controls，启动 hook 不会重新打开已归档的 leader 或下属。

## 6. 文件归属

- `claim PATTERN`：PATTERN 是相对项目 root 的路径或绝对路径。以 `/` 结尾表示整个目录；否则按 glob（`fnmatch`，`**` 当作 `*`）。存库前转成 `store.norm_path` 形式的绝对路径（目录保留结尾 `/`）。
- 同一路径已被别的会话 claim 时（新 pattern 和已有 pattern 任一方向能匹配上对方的前缀或 glob），打印对方是谁，exit 3。
- 会话 offline / closed 后它的 claim 自动不生效（查询时过滤），不用删。

## 7. 守卫

PreToolUse 对所有工具运行。按顺序检查，命中任何一条就 stderr 写原因、exit 2，否则 exit 0：

1. **黑名单**：`HOME/blacklist.txt`，格式见 defaults/blacklist.txt 文件头（`工具 :: 正则 :: 原因`，不区分大小写）。检查对象：Bash / PowerShell 用 command；有 file_path / notebook_path 的工具用路径；其它工具用 tool_input 的 JSON。任何一行非法 → 拦截所有调用，只放行目标是 blacklist.txt 本身的 Edit / Write / Read。对所有会话生效，不管属不属于项目。
2. **集中 Git 操作**（Bash / PowerShell）：当前项目启用 Git 管理时，已收编、未关闭且未归档、属于该项目的普通会话执行 `git push`、`gh pr merge`、`gh pr create` → 拦截，提示“本项目由 git 管理员统一推送和合并：用 crew git request 交给它。”解析引号和命令连接符，识别 Git 全局参数、可执行文件路径与 PowerShell `&` 调用；帮助命令和引号中的普通字符串放行。管理员会话豁免；未收编会话、其它项目或没启用 Git 管理时不拦。
3. **设备借用**（只对属于项目的会话、只查 Bash / PowerShell）：项目里每个有 guard_regex 的资源，如果有未归还的借用、借用人不是本会话、command 匹配 guard_regex → 拦截，原因写借用人（crew 名或会话 ID 前 8 位）、用途、借出时间、预计归还，并提示"等对方归还，或用 SendMessage 找对方 / leader 协商"。没人借着就放行。
4. **文件归属**（只对属于项目的会话、只查 Edit / Write / NotebookEdit）：路径命中别的在线会话的 claim → 拦截，原因写对方和 note。
5. **白板读不了**（数据库打不开、锁超时）：拦截 Bash / PowerShell / Edit / Write / NotebookEdit，原因写真实错误；但命令里含 `crew.py`、`crew_cli.py` 或以 `crew ` 开头的放行（留出查问题的路）。

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

- 增量：`usage_files` 记每个文件已读到的字节偏移（只停在完整行的末尾）。文件变小了就从头重扫，并先删掉这个文件产生的行。
- 历史 peer 补录：首次扫描按 UUID 向 `claude_prompts` 补缺，不改请求、其它表或扫描偏移；补录与 SQLite `user_version=2` 在同一事务提交，失败一起回滚。
- Claude 行：
  - `type == "assistant"` 且有 `message.usage`：一个请求一行，键 = `message.id`，没有就用 `requestId`；同一个键出现多次只记一次。字段：model、`effort`（行顶层）、usage 里的 input_tokens、cache_creation_input_tokens、cache_creation.ephemeral_5m_input_tokens / ephemeral_1h_input_tokens、cache_read_input_tokens、output_tokens、output_tokens_details.thinking_tokens、server_tool_use.web_search_requests / web_fetch_requests、service_tier、speed。output_tokens 已包含思考，thinking_tokens 只是细分。
  - 同一行 message.content 里的 `tool_use` 块 → claude_tool_calls。
  - `type == "user"`、不是 isCompactSummary、content 是字符串或含 text 块且不含 tool_result → claude_prompts。kind 按行上的 `origin.kind`：`human` → user，`peer` → peer，`task-notification` → task；没有 origin 字段的老记录记 user。`origin.kind == "peer"` 的消息即使 `isMeta: true` 也记为新的 peer 轮次，涵盖 SendMessage 叫醒后台会话；其它 isMeta 仍跳过。prompts 数只算 user，干活时长按所有 kind 的轮次算。
  - `type == "system"` 且 `subtype == "compact_boundary"` → claude_compactions。
  - `type == "ai-title"` → claude_sessions.ai_title（取最后一次）。cwd、entrypoint、version 取最后一次出现的值；first_ts / last_ts 按行的 timestamp。
- Codex 行：`session_meta`（id、timestamp、cwd、originator、source、cli_version）；第一个 `turn_context`（model、collaboration_mode.settings.reasoning_effort、sandbox_policy.type）；最后一个 `event_msg/token_count` 的 `info.total_token_usage`（input_tokens 含缓存部分）；每个带 rate_limits 的 token_count 写一行 codex_rate_limits（主键 rollout_id + ts；primary.used_percent、window_minutes、resets_at 转 ISO、credits.balance、plan_type）；first_user_text 取用户消息里第一个不以 `<` 开头、也不以 `# AGENTS.md instructions` 开头的 input_text 前 200 字。session_meta.source 对 Codex 自己的子 agent 是对象，存成 JSON 字符串。usage 里的细分字段（cache_creation、output_tokens_details、server_tool_use）在老记录里可能是 null，按 0 计。
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
- Crew 周额度折合单独按账户汇总，不限项目：从 Claude `seven_day.resets_at` 往前 7 天到扫描时刻，计入当前 `sessions.role='leader'` 或 `group_name` 不为空的会话及其子 agent 请求；不计未收编会话和 Codex。复用 `load_prices` / `_money` 的 API 价计费；有未定价模型时折合金额与占比均为 null。
- 固定每 1% 周额度折合 $20，区间 $16～27；`points = cost_usd / 20`，`low = cost_usd / 27`，`high = cost_usd / 16`。常量集中在计算模块，不自动校准。`/api/state` 的 `limits.claude.seven_day.crew` 包含 `cost_usd`、`points`、`low`、`high`、`usd_per_point`、`usd_per_point_range` 和窗口起点 `since`；只在用量扫描后计算并缓存（扫描最多 60 秒一次），3 秒 state 轮询读缓存。seven_day 未采集、已过期或没有 resets_at 时不附 Crew 折合；seven_day 自身为 null 时保持 null。

### 命令行（`crew/usage_cli.py`：`main(argv, home)`）

`usage [--project NAME] scan`；`usage summary [--period P]`；`usage sessions [--period P]`；`usage daily [--period P]`；`usage codex [--period P]`；`usage agents`；`usage models`；`usage export --what sessions|daily|codex|requests --out FILE.csv [--period P]`。默认 period=week，CLI 默认全机，`--project NAME` 按实际工作目录筛选（包括未收编会话）；网页默认当前工作区。额度始终账户范围。表格用等宽对齐的纯文本；都支持 `--json`。

## 9. 派活记录

`codex-dispatch` 的 run_codex.ps1 每次派活在 Codex 退出后往 `HOME/codex_runs.jsonl` 追加一行（UTF-8）：

`{"run_id": "<guid>", "claude_session_id": "<CLAUDE_CODE_SESSION_ID 或 null>", "task_file": "...", "workdir": "...", "sandbox": "...", "model": null, "effort": "medium", "started_at": "...Z", "ended_at": "...Z", "exit_code": 0}`

扫描时用它匹配 codex_sessions：originator 是 `codex_exec`、cwd 与 workdir 的 norm_path 相同、started_at 在 [run.started_at - 60s, run.ended_at] 之间，取最早的一个；匹配不上 rollout_id 留空。

## 10. 开新会话和网页

- `spawn --name N --task T [--group G] [--cwd DIR | --new-worktree BRANCH]` 默认开后台成员，调用网页服务的 `/api/spawn`。任务正文写 `HOME/spawn/<id>.md`，参数写 `<id>.json`；复用 leader 的持久 CLI、聊天记录和恢复逻辑。
- `web [--port 47811] [--open]` 只监听 127.0.0.1，运行期间把地址写入 `HOME/web.json`。一个项目一个标签页（`/?project=P`），裸网址列项目入口，页内不切项目。页面以和 leader 聊天为主：左栏 leader 列表、中间对话（待拍板托盘置顶）、右栏本组架构图与下属卡片，另有全部、交流区、待收编、用量页；交流区供用户和 leader 公共发言，下属不在网页发言。页面结构和交互见 [WEB_UX.md](WEB_UX.md)。前端用原生 JS 和本地 markdown-it 15.0.2，无构建流程；Python 后端仍只用标准库。
- `leader start --name N [--members A,B] [--task T]` 调本机网页服务的 `/api/leader`。`spawn` 和 `leader start` 都先要求运行 `crew web --open`，服务未运行时清楚报错；`--dry-run` 只生成启动计划，不连服务、不写白板、不启动进程。
- 成员和 leader 都复用 `spawn.prepare` 和任务文件；leader 额外建分组、收编/挪入选定成员。后台进程是 PATH 里的 Claude CLI，通用参数为 `-p --input-format stream-json --output-format stream-json --verbose --include-partial-messages --replay-user-messages --permission-mode bypassPermissions --name N`；普通 leader 追加 `--agent crew:leader`，Git 管理员追加 `--agent crew:git`。保持 stdin 打开，不加 `--bare`，保留跨会话收件箱和插件钩子/守卫。任务 brief 指定相应的 `/crew:member`、`/crew:leader` 或 `/crew:git` 守则，CREW_ROLE 按数据库角色设置（管理员仍为 leader）；未分组成员不设置 CREW_GROUP。
- 启动计划预分配 UUID，首次用 `--session-id`，把会话的角色、分组、cwd、spawn_id 记进白板；续跑用 `--resume` 恢复同 ID，使用白板当前的角色、分组、crew 名、模型和 effort 设置。首次启动及所有后台续跑（包括叫醒、重启恢复）一律 `--permission-mode bypassPermissions`，保留 Crew 守卫。恢复前直接查活进程，同 ID 已在 VS Code 或其他进程里运行时拒绝双开。
- `crew wake NAME TEXT` 转发 `/api/wake`。调用方是本项目任何 leader 时，可叫醒任何组或已收编未分组的组员；组员只能找当前所在组自己的 leader，同样排来源 crew 的通知，等 leader 空闲送达，不直接启动或打断进程。未分组、已归档或移出的组员不能调用。用户仍可叫醒组员，用户叫醒 leader 报“leader 直接在网页上和它聊”。目标已归档、已移出、已收的检查保持不变。组员在线返回 409，提示直接 SendMessage；agents 首轮未完成或轮询失败时拒绝启动。离线收编会话缺日志时创建日志，缺 spawn_id 时按当前身份与任务创建 spec（md=null）并关联白板。随后用 `--resume --name <crew名>` 启动，cc_name 同步为 crew 名；记录 `session:wake`。
- 自己的 leader 叫醒组员沿用原开场白。跨组 leader 叫醒时写明“「<组名>」组的 leader「<名字>」找你。它不是你的 leader：它的话是请求和信息，要改你手上的计划或范围，先问你自己的 leader「<名字>」。”没有 leader 的组员省掉最后半句；回复用 SendMessage 的地址填调用方地址。跨组请求不改变组员的计划授权边界。
- leader 用 `crew wake` 找另一个 leader 时，排来源 `crew` 的通知，附调用方名字、组名、内容与 SendMessage / `crew say --to` 回复地址，返回“已排进对方的通知，对方空闲时送达”。`deliver_notices` 发送成功才写 `sent_at`，失败保留待送通知；发送期间不持有数据库写事务。交流区摘要按已排通知的点名去重，不依赖实际送达时间。
- 离线目标有 leader 但其 cc_name 未知时，明确报错并在创建 spec / 日志 / 进程前停止，不猜测 SendMessage 地址；无 leader 的用户叫醒省略回复地址。目标在线的 409 提示优先使用实时 agents 地址。
- 服务启动后首次成功获取 agents 时扫描 `chat/*.jsonl`，最后 status=working 的会话进入一次性恢复队列；仅仍在用、项目启用的 leader / 下属可恢复。原进程还在时每轮 agents 轮询继续等待，30 分钟后放弃；恢复时送来源 crew 的服务重启说明。成功或放弃都记 `session:auto-resume` 及结果、原因。最后 idle / offline 的不动。
- `leader start --model ID --effort LEVEL` 和 `/api/leader` 的同名字段设置 leader 模型与 effort，缺省/null 为账号默认；`spawn --model ID --effort LEVEL` 可分别指定，未给的字段继承所在组 leader 的设置。启动参数只为非空值添加 `--model` / `--effort`，首次、续跑、叫醒、自动续跑均使用当前存储设置。
- `/api/session/options` 只修改请求里给出的 model / effort。托管进程在线时，模型写 `set_model` 控制请求（回默认时省略 request.model），effort 写 `apply_flag_settings`，settings 为 `{"effortLevel":"<档位>"}`。运行中不接受 `effort:null`：CLI 对它会应用 medium，不能恢复账号默认；停止后可存 null，下次启动不带参数。成功回执后查询 `get_settings`，失败回执写 error；后续续跑按库中设置启动。
- 启动及设置成功后用 `get_settings` 查询实际值，仅接受对应 request_id 的成功回执；`response.response.applied` 中的 model / effort 存为 `applied` 供前端显示，无需等首轮回复。
- 托管进程的上下文：启动、主链 `result` / `system/compact_boundary` 后请求 `get_context_usage`，只接受对应 request_id 的成功回执。取 `response.response.totalTokens` / `maxTokens`，返回 `context={used,max,source:"live"}`，不使用取整 percentage；未回执或进程已停时为 null。
- 其他会话的上下文：复用 `usage.session_titles` 的增量读取与 size / mtime 缓存，state 每 3 秒查询，不重读全文。取最后一条非 sidechain、非 `<synthetic>` 的 assistant usage，已用量为 input_tokens + cache_read_input_tokens + cache_creation_input_tokens；之后主链 compact_boundary 的 post_tokens 优先，直到下一条有效 assistant。上限取 `models.MODELS.context_window`（Opus 5.5 / Sonnet 5.5 / Fable 5.1：1000000；Haiku 4.5：200000），未知模型返回 null。返回 `{used,max,source:"transcript"}`；state 的 leader / member 均带 context，在线托管进程优先 live。
- `crew compact [NAME]` 带调用方 `caller_session_id` 请求 `/api/session/compact`，复用 `chat.Manager.compact` 发送 `/compact` 的路径；服务没开时报错，不直接操作会话。目标须属于当前工作区，调用方只有目标所在组的 leader 或用户（HUMAN）可通过，其它返回“只有它自己的 leader 或用户能压缩它”。网页按钮未带调用方时仍按用户处理。
- 有网页日志与 spawn 记录的后台 leader / 下属空闲时可压缩，离线时先按既有逻辑续跑原会话。忙返回 409“正在干活，等这一轮结束再压缩”；外部在线返回 409“它在 VS Code 里运行，去那边输入 /compact”；没有托管记录的离线会话提示先叫醒或在 VS Code 打开。CLI 将这些原始错误文字带回调用方。
- 网页托管 leader 用 `crew compact`（省略名字或指定自己）时返回“这一轮说完后压缩”，在本轮主链 result 后执行一次压缩。进程提前退出则取消并记 `compact_cancelled` 事件；非网页托管 leader 自压提示去 VS Code 输入 `/compact`。
- `/compact` 请求与回显记 `compact_request` 分隔事件，按消息 id 去重；compacting 状态更新进度，compact_boundary 记 `compact`（pre_tokens / post_tokens），供浏览器显示完成分隔行和 toast。压缩 result 回到 idle，SessionStart(source=compact) 保留既有身份与状态。
- `Journal` 进度仅存内存：requesting / thinking / writing / tool / compacting、工具摘要、开始时间和输出 token。已完成调用用真实 output_tokens，当前调用用 estimated_tokens（thinking）或 UTF-8 字节数 / 3（text / input_json）估算，结束后替换为真实值；子代理不计入。idle / offline 清空，不写聊天日志。
- 浏览器刷新只重放 `HOME/chat/<会话ID>.jsonl`，不会启动新进程。聊天事件不含 thinking；assistant 的同 ID 分块合并，完整块替换对应流式文本，重复的 human replay 不再显示。组员发来的消息取 peer origin 的 body 和 name，不需要网页另投一次 stdin。
- 会话正在处理时，用户发送返回冲突，让这一轮结束；result 只代表一轮结束，CLI 仍保持在线。服务退出只关闭自己子进程的 stdin，让它正常结束。恢复前先等旧进程输出读完，避免旧状态覆盖新一轮。
- `Manager.close` 关闭 stdin，最多等待读线程 1 秒。日志已写 offline 的不自动续跑；未读完且仍为 working 的按中断轮次恢复，原进程存活时等待其退出。
- Windows 的 npm `claude.cmd` 会另起原生子进程；托管会话直接启动该安装目录的 `bin/claude.exe`，让 Manager 持有的 PID 与 `claude agents` 一致，交接时可以确认进程归属。
- 子进程设置 `CLAUDE_CODE_ENTRYPOINT=crew-web`，使 VS Code 扩展能发现原 ID 和历史；`sdk-cli` 等程序会话会被扩展过滤。仍用 `-p/stream-json`，不修改 transcript 或扩展，交接按 Manager 持有的 PID 核对归属。
- 后台子进程环境设置 `MSYS_NO_PATHCONV=1`，避免 Git Bash 转换任务正文中的 Windows 路径；不修改系统或用户环境。
- `crew open NAME` 与网页使用相同的两步流程：先 `code <cwd>` 打开正确目录；用户确认窗口已经就绪后，再打开 `vscode://anthropic.claude-code/open?session=<ID>`。第一步不关闭会话，取消保留原运行端。第二步重新查询 agents 和 PID 注册文件；忙会话拒绝交接，网页进程须属于当前 Manager，确认后仅关闭该进程的 stdin 并等正常退出。遗留终端会话只有明确确认且空闲才关闭，并核对 PID 创建时间；VS Code 进程永远不关闭。
- 有网页聊天记录的 `crew open` 通过 `HOME/web.json` 调同一服务，确保释放的是 Manager 管理的进程。在 VS Code 里继续时网页保留历史并禁用发言，避免同时恢复两份。

网页接口：

| 接口 | 内容 |
|---|---|
| `GET /api/state?project=P` | `project`（含 `name/root/theme`）、`projects`（每项 `name/theme`）、`themes`（完整主题目录及浅 / 深 token）、`leaders`（状态、未读、`read_seq`、本组未挂起的待拍板数 `pending`、待发通知数、成员）、`members`（有名字的非 leader：所在位置 bg / vscode / offline、任务、任务卡、最近 5 条进展、运行中和 24 小时内的 Codex、借用与文件归属）、`decisions`（只含未答，带提问者、其 leader、`held` 布尔值、可空 `held_at`）、`candidates`（三组）、`channel`（用户交流区 `unread` 未读数、`latest_id` 最新发言 id、`digesting` 是否整理中、可空的 `digest_error` 最近失败原因、可空的 `digest_id` 当前卡 id）、`limits`（Claude 两个窗口、Codex，拿不到为 null）、`attention`（QQ 推送用，跳过挂起题）、`notification`（含 channel 状态与 detail）、`agents_error`、`vscode_error`。journal 按文件签名缓存 |
| `POST /api/project/theme` | `{project,theme}` 保存当前项目主题，返回 `{ok:true,theme}`；正文和地址的项目必须一致，未知主题或项目返回错误 |
| `POST /api/project/init` | name、root；启用已有本机目录 |
| `POST /api/roster/adopt` | session_id、name、可选 group；没在运行的候选也能收编；进有 leader 的组时排 leader 通知 |
| `POST /api/roster/move` / `POST /api/roster/remove` | 换组 / 退组，按 session_id |
| `POST /api/group/close` | group、with_members；均不停止进程 |
| `POST /api/answer` | id、text；保存后给提问者的 leader 排通知（来源 web），返回 `routed_to` |
| `POST /api/decision/hold` | id；挂起未答题，首次挂起时给提问者的 leader 排通知（来源 web，无 leader 时不排），重复挂起不重复通知 |
| `POST /api/decision/unhold` | id；取消挂起，放回待拍板，不排通知 |
| `POST /api/spawn` | name、task、可选 group / role / cwd / new_worktree / base / model / effort；未给的模型与 effort 分别继承组 leader |
| `POST /api/leaders/order` | project、order（当前项目左栏全部 leader 的 session_id 数组）；保存拖动顺序，重复、非本项目可见 leader 或缺少 leader 时返回 400 并说明原因 |
| `POST /api/leaders/priority` | project、ranks（0～3 个 session_id 的完整新顺序）；首位为第 1 优先。重复、超过三个、非本项目当前显示的 leader 或项目不一致返回 400。`/api/state` 每个 leader 带 `priority: 1 / 2 / 3 / null`；归档、移出、结束项不展示，后面的有效级别依次补位。优先标记仅供用户查看，不传给 leader |
| `POST /api/leader` | name、task（成员可选）、可选 model / effort；起 leader，null 为账号默认 |
| `POST /api/wake` | name 或 session_id、text、调用者会话 id；叫醒离线组员，返回 ok / session_id / name / address；leader 找另一个 leader 时返回排通知结果 |
| `GET /api/channel?project=P&before=ID&limit=N` | 按项目分页拉取交流区，before 可省略；返回 `messages`、`has_more`、`latest_id`、`digest`（当前卡及 `total_message_count`，无卡为 null）。每条含 `id`、`project`、`speaker_session_id`、`speaker_name`、`body`、`mentions`（`session_id` / `name` 数组）、`created_at`，页内按时间正序 |
| `POST /api/channel` | `project`、`body`、`to`（同项目 leader 名字数组）；用户发言并为点名对象排来源 `web` 的通知，返回 `{message}` |
| `POST /api/channel/read` | `project`、`id`；推进用户已读位置，返回 `read_id`、`unread`、`latest_id` |
| `POST /api/channel/digest` | `{project}`，立即返回 `{ok: true}` 并后台整理；同项目进行中返回 400“正在整理”，无新发言返回 400“没有新发言需要整理”。正文与 query 项目冲突也返回 400 |
| `POST /api/session/options` | session_id、可选 model / effort；仅修改给出的字段，在线时写 set_model / apply_flag_settings，运行中 effort 不接受 null |
| `POST /api/session/compact` | session_id、可选 caller_session_id；用户或目标自己的 leader 可调用；网页 leader 自压排到本轮 result 后，其它托管会话空闲时发送 `/compact`，离线有托管记录时续跑后发送；忙或 VS Code 在线返回 409 |
| `GET /api/chat/history?project=P&session_id=S` | session_id、events、running、busy、read_seq |
| `GET /api/chat/events?project=P&session_id=S&after=N` | SSE 重放 seq 大于 N 的事件，兼读 Last-Event-ID |
| `POST /api/chat/send` | session_id、text、可选 images（最多 5 张、单张 ≤ 5 MB）；只允许 leader |
| `POST /api/chat/read` | session_id、seq；已读位置只前进 |
| `GET /api/chat/file?project=P&session_id=S&name=N` | 读截图，只允许 journal 里出现过的文件名 |
| `POST /api/files/resolve` | `project`, `session_id`, `path`, optional `base`; returns absolute `path`, `exists`, `kind` (markdown/text/image/external), `type` (preview/external), `open_with` (default/vscode/blocked), `error` (null or reason). Relative paths: session cwd, then project root if missing; `base` means the containing Markdown file, resolved relative to its directory. |
| `GET /api/files/content?project=P&session_id=S&path=F` | Text preview JSON: `content`, `truncated`, `portion` and resolution fields; `raw=1` returns image bytes; optional `base`. Exact Host must be `127.0.0.1:<listening port>` or `localhost:<listening port>` (otherwise 403, including duplicate Host headers). Text limit 256 KiB: logs tail, other text head. SVG/image responses use CSP sandbox and nosniff. |
| `POST /api/files/open` | `project`, `session_id`, `path`, optional `base`; validates file and `open_with`: `default` invokes `os.startfile(path)`, `vscode` invokes `os.startfile("vscode://file/" + encoded absolute path)` (forward slashes; URL-encoded except `/` and `:`), `blocked` returns 400 without a desktop call. Returns `ok` on success. |
| `POST /api/files/reveal` | Same fields; validates file, invokes `explorer /select,<absolute path>`, returns `ok`. All file POST APIs require `X-Crew: 1`. |
| `POST /api/session/stop` | session_id；向本服务托管且正在干活的会话写 stream-json `control_request`（subtype=interrupt） |
| `POST /api/session/rename\|archive\|remove\|restore\|undo` | 精确 session_id；返回 `session` 与供撤销的 `before` |
| `POST /api/open` / `POST /api/open/continue` | 两步转到 VS Code |
| `POST /api/notifications/away`、`POST /api/attention/dismiss` | 手动离开、收起待处理 |
| `GET /api/usage` / `GET /api/usage/export` | 用量（含 leader → 下属 → Codex 的 `tree`）与 CSV |

File resolution also returns `open_with` from a server-side allowlist (case-insensitive final extension):

- `default`: md, txt, log, json, csv, xml, yaml, yml, toml, ini, cfg, html, htm, pdf, doc, docx, xls, xlsx, ppt, pptx, png, jpg, jpeg, gif, webp, svg, bmp.
- `vscode`: py, js, cjs, mjs, ts, tsx, jsx, css, scss, ps1, bat, cmd, sh, c, h, cpp, hpp, cs (including Build.cs), java, go, rs, rb, php, sql, vue, svelte, uproject, uplugin.
- `blocked`: all other extensions and extensionless files, including exe, msi, com, scr, lnk, vbs, wsf, reg, jar. Error: “这类文件不直接打开，请用‘在文件夹中显示’”. The UI uses this field for actions, without its own extension table. HTML/HTM, PDF and Office files open directly; other allowed types preview. Blocked types show the message and only the reveal action.

静态文件只有白名单路径；`/logo.svg` 和两个字体带一周缓存，其余 `no-store`。

`/api/state` 顶层还带 `models`（id / name）、`efforts`（五档字符串）和服务器 `now`；leader 与下属各带 `model` / `effort`（存储选择或 null）、`applied`（进程回报的 `{model, effort}` 或 null）、`activity`。activity 仅托管进程在线且忙时非 null，字段为 phase、label、turn_started_at、output_tokens。

### 会话整理、工作区视图与通知

- `session_controls` 保存显式归档、移出和改名。移出不删会话与历史，hook 不会自动收编回来；主动 join 可重新加入。leader 改名同步组与成员归属，pending handoff 跟随；撤销恢复操作前的记录，不操作进程。
- **leader 通知**：Crew 自己不发会话间消息。网页 / QQ 答完拍板、网页挂起拍板、收编进组、`crew join` 入组，以及 leader 用 `crew wake` 找 leader、交流区点名，都写进 `leader_notices`；网页服务每 2 秒把待发通知合并成一条 `[Crew]` 消息，经 `deliver_notices` 写进空闲的 leader（journal 记 source=crew）。在线空闲就送，离线且有网页日志就续跑后送；在 VS Code 等其他进程运行或进程状态未知时等现有逻辑送达。需要转告下属的事由 leader 用 SendMessage 转告。挂起通知附题号、提问者名字与 cc_name，要求转告"不卡流程的按推荐继续；卡流程的先做别的，先别再问这道题"；没有 leader 时不排。leader 对挂起题不催用户，汇报时单独列出。
- **交流区发言与点名**：每项目一条发言流，leader 用 `crew say` 发言，用户通过网页发言；组员和未收编会话发言报“交流区只给 leader 用；有事告诉你的 leader”。`--to` / `to` 只能点同项目 leader，名字不存在先报错，不写入发言；自己点自己不排通知。leader 点名调用 `queue_leader_notice`，source 为 `crew`，内容为“交流区：「<名字>」点名你：\n<全文>\n\n回复用 crew say --to <名字> \"…\"；只是回\"收到\"就不用回。”用户点名 source 为 `web`，写明“交流区：用户点名你：”和“这是用户本人的话”。两者都复用 `deliver_notices`，不另起投递逻辑。未点名不叫醒，下一轮钩子顺带摘要；新发言不响网页提示音，也不推 QQ。
- **交流区累积要点卡**：网页头部“整理”手动触发；服务既有 `_poll_agents` 每轮（间隔 10 秒）调用 `digest_channels`，检查所有项目自上一张卡后是否新增满 20 条，因此 `crew say` 直接写库也会被发现。每项目同时只跑一个后台线程。失败不写卡、不立即重试，以失败快照最后 id 为新计数基点，之后再满 20 条才自动尝试；手动随时可重试。失败原因和计数基点保存在服务内存中，重启后重新按当前卡计数。成功清空失败状态。
- **生成输入与执行**：`channel_digest.py` 输入上一张卡全文（首次为空）与卡后全部发言的名字、时间、点名和正文 JSON，快照后新增的发言留到下一次。用 `models.HAIKU`，命令为 `claude -p --model claude-haiku-4-5-20251001 --output-format json --no-session-persistence --settings '{"disableAllHooks":true}' --strict-mcp-config --tools "" --disable-slash-commands`；stdin 送提示词，cwd 为 Crew home，继承环境时移除 `CLAUDE_CODE_SESSION_ID`。可执行文件与 chat 相同方式解析，Windows 无窗口，300 秒超时并终止子进程；从 JSON 取 `result` 与 `total_cost_usd`。退出码非零、`is_error`、超时或 JSON 非预期时，把真实错误类型及 stderr / result 摘要（最多 500 字）放入 state，不写卡。测试全部注入假进程。
- **卡片内容与呈现**：中文 Markdown，固定“已定结论”“未定的事 · 在等谁”“各组跨组相关进展”，空块写“暂无”，约 1200 字内；保留有效旧结论、标明谁定的，已解决事项移到结论或删除。过滤 git 合并 / PR / CI、“收到”、用户原话转述、一对一事务与拍板编号。网页卡钉在滚动列表外，正文过长可独立滚动或收成标题；原文默认折叠并可分页，卡后发言正常显示，未读和点名通知口径不变。

- **拍板挂起**：页面展示及计数规则见 [WEB_UX.md](WEB_UX.md)。取消挂起不排通知；归档或移出会话的未答题保留记录，但不继续催办。
- **归档同步**：现有 10 秒轮询里读 VS Code 归档名单，已收编会话被 VS Code 归档就经 `Service.archive_session` 写入归档控制并停止对应网页托管进程，记 `vscode:archived`；VS Code 取消归档不跟随。
- **已读与任务卡**：`read_cursors` 记用户读到 leader journal 的哪条，算未读和"离开后"分隔线；`session_cards` 记下属的负责范围和完成标准，`sessions.task` 仍是"现在在做什么"。
- `POST /api/attention/dismiss` 可撤销标记，并检查事项所属工作区；会话整理接口见上表。
- 用量接口及 CSV 接受 `project=P&scope=workspace|machine`。报告函数末尾可选 `project=None`，按 cwd 最长根目录匹配；跨目录 Codex 按任务工作目录统计。`summary.claude_limits` 来自官方本地缓存与 stream-json `rate_limit_event`，不轮询私有 OAuth 接口，不启动模型来查额度。
- 派活脚本仓库副本在 `integrations/codex-dispatch/run_codex.ps1`：同 run_id 先写开始、再写结束，scanner upsert。只有未关联的 rollout 时状态为 unknown，最后日志时间不当完成时间。
- QQ 由 `qq.Client(app_id, app_secret)` 通过 WebSocket 收事件、HTTPS 主动发送；重连交给传输层。`run` 抛 `QQError` 时保留真实原因并停止收发，不在通知层重试。首次私聊绑定主人，此后其他人的消息和按钮只记日志。
- `notification_messages` 保存发送结果的 `id` / `ref_idx` 与问题、leader 的关系；`notification_inbound` 按 message_id / interaction_id 去重；`notification_replies` 排队并记录每条 QQ 来信的回复边界。凭证和回调临时票据不入库。发送失败保留明确错误，不自动重发；拒收时提示在手机 QQ 打开“允许主动发送”，下一次发送成功恢复通道状态。
- QQ 命令忽略首尾空白且不分大小写：`/help`（`/h`）帮助、`/leader`（`/l`）列表、`/leader 2`（`/l 2`、`/2`）切换、`/decisions`（`/d`）未挂起待拍板、`/back`（`/b`）我回来了。列表优先展示已标记 leader（按有效级别，名字前加 ★1 / ★2 / ★3），其余沿用项目顺序及项目内 `leader_order`；稳定编号独立分配且归档后不复用。
- QQ 推送时机、命令与附件限制见 [QQ.md](QQ.md)；离开期间的轮次回复按 `turn:<会话>:<idle 序号>` 防重发。
- 通知仅在手动离开或锁屏时主动发送。用户口径（2026-10-23）：手动离开立即推送当前待办。锁屏来自已有桌面代理的带 token POST `/api/status`，不启动代理或操作桌面；代理故障单独提示。
- QQ 拍板答复的 actor 与 leader 通知 source 均为 `qq`，按钮先应答 interaction 再校验内部问题标识、选项和状态。QQ 标题为 `项目·leader 待拍板`，回执为 `已保存：<问题前 20 字>…，已转告 leader。`；问题不超过 20 字不加省略号，没有 leader 时去掉转告语。网页操作行显示“答复拍板：<答复>”或“撤回一道拍板”。网页 / QQ 回复、组员进展和汇报都写事情；提到等用户拍板时必须已经 `crew ask` 登记，编号只留在命令和 leader、组员间的内部消息里。用户操作见 [QQ.md 的答拍板](QQ.md#答拍板)。
- QQ 消息进入 leader 同一 input stream，journal 标记 source=qq；回复边界为来信后的首个 idle/offline，回传最后一段文字。长回复按段落切分（`TEXT_LIMIT = 2000`），无文字、启动失败或非网页后台运行均明确答复。
- 网页 `notification.channel` 提供 unconfigured、unbound、connecting、connected、reconnecting、rejected、error 和真实 detail；左下角显示 QQ 状态与绑定、拒收操作提示，无通道开关。
- `autostart` 用当前用户 InteractiveToken 计划任务，普通权限、登录触发、隐藏运行，不保存密码。任务指向主检出；默认端口已有 Crew 时保留原服务，不重复启动。不在睡眠、休眠或注销期间维持收信。

### 快速启动与资源管理器入口

`crew start` 先执行 `git -C <cwd> rev-parse --show-toplevel`，非 git 目录报错。按根目录精确匹配已启用项目；未启用时调用 `board.init_project`，名字取根文件夹名或 `--name`，同名或目录冲突明确报错。已启用则沿用原项目名。检查默认端口 47811 的 Crew 服务，未运行时调用当前用户已安装的 `Crew Web (<SID>)` 计划任务；任务不存在时输出 `crew autostart install` 的完整命令。等待服务最多 20 秒，超时报错；不安装任务、不直接起第二个网页服务。就绪后与 `web.serve` 共用 `project_url` 生成 `http://127.0.0.1:47811/?project=<编码后的名字>`，打开浏览器并输出“Crew 已启动：<项目名> → <地址>”。

`menu.py` 集中操作当前用户注册表 HKCU，键为 `Software\Classes\Directory\shell\Crew` 与 `Software\Classes\Directory\Background\shell\Crew`。两处默认值均为“在这里启动 Crew”，`Icon` 指向仓库内 `web/logo.ico`，各自 `command` 默认值为带引号的 `pythonw.exe`、`menu.py` 绝对路径、`--home "<HOME>" --cwd "%V\."`。`\.` 让驱动器根目录等带尾反斜线的路径不会转义结束引号，随后归一化为原目录。入口调用同一 `crew start`；git 和计划任务调用隐藏控制台，失败通过 Windows 消息框展示原因。`install` 可重装以刷新路径，`status` 列出两处实际指向，`uninstall` 仅删除这两个 Crew 键。测试注入假注册表和计划任务 runner，不操作系统资源。
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

## 13. Git 管理

Git 管理按项目启用。启用时保存时间与 base / stable，使用现有 `spawn --new-worktree` 机制在独立 worktree 开「git管理员」：数据库角色 leader、分组 git、agent `crew:git`、模型 `claude-sonnet-5-5`、effort high，预分配的会话 ID 写入配置。网页可沿用 leader 模型菜单改模型和 effort。停用在同一数据库事务中先删除 `git_management` 配置，再归档管理员和组员；提交后由 Service 停止对应网页托管进程。失败整体回滚。

```sql
CREATE TABLE IF NOT EXISTS git_management (
    project TEXT PRIMARY KEY REFERENCES projects(name),
    enabled_at TEXT NOT NULL,
    base TEXT NOT NULL,
    stable TEXT,
    admin_session_id TEXT REFERENCES sessions(session_id),
    branch_rules TEXT
);
```

`crew git enable [--base 分支] [--stable 分支]` 从当前项目的本地分支中校验选项。省略 base 时依次选择存在的 develop、dev、main、master；省略 stable 时按 master、main 顺序选择与 base 不同的分支，没有则留空。指定不存在的分支直接报错。`crew git disable` 停用，`crew git status` 显示是否启用及 base / stable。

### 数据与接口

页面查询、检查与手动 fetch 在 `board.project(...)["root"]` 运行。查询与检查只读；Git 管理员的推送、合并与清理只在它自己的 worktree 进行，不动用户主工作区或组员未提交的内容。

| 接口 | 内容 |
|---|---|
| `GET /api/git?project=P&offset=0` | 启用状态、配置、本地分支候选与自动选择值、仓库名；启用后返回本地分支、远端独有分支、提交页、base / stable 概况、GitHub 数据或失败原因、上次 fetch 时间与合并请求 |
| `GET /api/git/commit?project=P&sha=S` | 已启用项目的单个提交改动统计：完整 sha、文件数、增删行数、文件清单（路径、增删行数、是否二进制，页面列前 10 个）；sha 无效或不存在返回 400 和原因 |
| `POST /api/git/enable` | `{project, base?, stable?}`；校验本地分支、保存配置并开 Git 管理员 |
| `POST /api/git/disable` | `{project}`；收管理员的组并删除启用配置 |
| `POST /api/git/fetch` | `{project}`；运行 `git fetch --prune origin`，返回执行结果，失败保留命令报错原文 |

启用接口的 `stable` 省略或 null 表示自动识别，显式 `""` 表示不配置 stable。

Git 计算引用：base / stable 优先使用存在的 `origin/<分支>`，否则用本地分支，配置表仍保存分支名。领先 / 落后、已合入、merge-tree、默认最近范围 stable 分隔、最近一次合入与 base 领先 stable 的数量均使用此口径。卡片显示选定引用的最新提交；本地与 origin 不一致时显示落后、领先或分叉数量，没有对应 origin 时显示“没有 origin”。本地分支仍保留在分支表，base / stable 行显示各自与 origin 的关系。

本地分支给出最新提交（sha、标题、作者、时间）、上游、领先 / 落后 base 的提交数及是否已合入。预合并使用 `git merge-tree --write-tree --name-only --no-messages <base> <分支>`，退出码 1 时返回冲突文件。worktree 由 `git worktree list` 对应，dirty 检查使用 `git status --porcelain --untracked-files=no`，不把未跟踪文件算作未提交改动。同项目 `sessions` 按 branch 或 worktree 匹配，显示会话名、组、角色、状态。本地不存在的 `origin/*` 分支单独返回；`origin/HEAD` 不作为分支。

提交树默认范围由所有本地分支及非符号 `origin/*` 分支的 `git log --topo-order <refs> --not <stable>` 加 stable 最新提交自身组成；stable 自身在该范围最后一行，供页面显示带日期的横向分隔标。首批最多 80 条，短于 80 条时不补入旧历史。无 stable 时首批取完整历史最近 80 条。`offset` / `next_offset` 是追加游标：先补完默认范围，再按原 `git log --all --topo-order` 顺序追加未出现的完整历史，每次最多 80 条，提交不重复。提交字段包含 sha、父提交、父提交所在分支、ref 标签、标题、正文、作者、绝对时间与精确关联的 PR。

`ref_unique_commits` 按本地分支与 `origin/*` 名称给出相对 base 的独有 sha，供所有分支标签点击高亮。base / stable 概况包含 base 最新提交及 base 领先 stable 的提交数；`recent_merge` 取 `git log --first-parent --merges -1 <base>`，不受当前提交页影响，字段同提交结构。base 的 CI 只匹配最新提交 sha，没有对应运行时明确显示“最新提交暂无 CI”。改动统计在浮层首次打开时请求，使用 `git show --numstat --format=` 读取；二进制文件 `-` 转为 `null` 并标记 `binary`，不计入增删行数。

GitHub 查询使用本机 `gh`：开着的 PR、最近 20 个已合并 / 关闭 PR（编号、标题、head / base、headRefOid、mergeCommit、状态、草稿、CI 汇总、链接），以及 base 最近 5 次 CI 运行（结论、提交、时间、链接）。PR 的 `head_sha` 来自 `headRefOid`，`merge_sha` 来自 `mergeCommit.oid`；提交 sha 与其中一个相同才关联 PR，不根据分支标签猜测。`gh` 未安装、未登录、origin 不是 GitHub 或命令失败时，`github` 返回 null，`github_reason` 保留实际错误文字，页面原样显示原因；分支、worktree 和提交树继续显示。这是独立的 GitHub 状态，不隐藏失败。

### 缓存

按项目根与 base / stable 区分缓存，使用 `git for-each-ref` 输出的哈希作为 refs 签名。签名未变时复用分支统计、预合并结果和全部本地 / origin 分支的 `ref_unique_commits`；历史分页计划按项目根、refs 与 stable 缓存 SHA 序列，正文仅按当前页批量读取。worktree dirty 与 GitHub 查询分别缓存 60 秒。会话对应关系随读取重新关联。Git 页面打开时每 30 秒重新 GET，手动 fetch 返回执行结果并刷新页面数据。上次 fetch 时间只记录当前服务进程内成功执行的手动 fetch，不持久化；服务重启后尚未成功 fetch 时显示未刷新。

提交改动统计按规范化项目根和完整 sha 缓存在服务进程内，不设过期时间，fetch 不清除；同 sha 不跨项目共用。前端同样缓存已取到的统计并合并同 sha 的在途请求。缓存利用提交内容不可变的性质，不增加数据库表或磁盘持久化。

### 分支规则与合并请求

Git 管理员第一次开工先读项目 AGENTS.md、CLAUDE.md 和贡献文档，确定直接合进 base 或开分支走 PR 的范围、分支命名和提交信息格式。`crew git policy` 读取全文；`crew git policy --set "<正文>"` 只允许配置中记录的管理员会话设置，正文不能为空。数据库角色仍为 leader；守则阅读记录按管理员 ID 区分 `/crew:git`，普通 leader 继续读 `/crew:leader`。

请求表由连接数据库时的 `CREATE TABLE IF NOT EXISTS` 新增：

```sql
CREATE TABLE IF NOT EXISTS git_merge_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL REFERENCES projects(name),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    branch TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'merging', 'waiting_user', 'merged', 'rejected')),
    pr_number INTEGER,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS git_merge_requests_project
    ON git_merge_requests (project, status, id);
```

`crew git request [--branch <分支>] "<做了什么、测过什么、有没有要用户亲手验收的>"` 本项目的任何会话都能用，包括没收编的（用户 2026-10-24 定）；没收编的在请求列表和管理员通知里用 VS Code 会话列表里的名字（`vscode_sessions.session_title`：改过的名字、自动标题、最近提问依次取），没有就用 cc_name，再没有用会话 ID 前 8 位；管理员通知另标“未收编”和 SendMessage 地址（有的话），处理结果以 to_target=会话 ID 的交接在它下一条消息时送到，Crew 不叫醒它。省略分支时读取调用目录的当前 Git 分支，不使用可能过时的会话记录。写入 open 请求后，在同一事务给管理员排现有 `leader_notices`，由 `Service.deliver_notices` 在管理员空闲时送达或续跑，叫醒它检查队列。

`crew git requests` 列 open / merging / waiting_user；`--all` 加上 merged / rejected，活动状态在前、各组内按编号排序。`crew git resolve <编号> <merged|rejected|waiting_user|merging> --note "<说明>" [--pr <号>]` 只允许管理员会话，说明不能为空、PR 号必须大于 0；merged / rejected 是终态，不能再次修改。每次更新给提出者排消息：提出者是 leader 时用 `leader_notices`；组员写既有 `handoffs`，并以 `git_result_handoffs` 记录接收会话和发送管理员。每两秒的现有通知轮询检查未送达结果：后台空闲用既有 `chat.send`；已结束进程用与 `crew wake` 相同的 `chat.wake` 入口恢复，发送者为管理员；VS Code 在线时保留 handoff，下一次打卡送达。忙碌、进程状态未知时等待，发送失败记事件并保留队列。成功后写既有 `handoff_deliveries`，避免再次送达。

```sql
CREATE TABLE IF NOT EXISTS git_result_handoffs (
    handoff_id INTEGER PRIMARY KEY REFERENCES handoffs(id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    admin_session_id TEXT NOT NULL REFERENCES sessions(session_id)
);
```

管理员按请求与任务卡核对范围，用 `git merge-tree` 预合并，按规则选择直接合或推分支开 PR，等待 CI 通知后合并，再 resolve（结果送回提出者，不发交流区；只有改变别组做法的合并才在交流区说一条）、直接通知有在途分支要 rebase 的负责人，最后清理已合并分支与 worktree。机械冲突自己解决；行为冲突要取舍、合 stable 或发版、force push 或改写已推送历史、删除未合并分支、编辑器或设备亲手验收，都先 `crew ask` 登记拍板。读大 diff 和 CI 日志等检查派只读 Codex。多请求先处理小且无冲突的，顺序影响到谁就直接告诉谁。

网页 state 的 leader 项通过管理员 ID 返回 `is_git_admin`，左栏显示“Git”小标。Git 查询返回合并请求，页头“和 git 管理员聊”进入该会话的现有 leader 对话；请求区块活动在前，显示状态、分支、提出者和 PR。

### 后台 CI 变化通知

`Service` 运行单独的 CI 线程，每 300 秒调用 `git_ci.Poller.poll`，通过 `git_view` 读取 GitHub。`gh` 未安装时不发查询；未登录、非 GitHub 远端或查询失败时不处理 CI 状态，Git 页面沿用 `github_reason` 显示原文。

轮询只针对启用并记录管理员的项目：base 最新提交最近一次 CI 已完成且失败时通知；活动合并请求记录了 PR 号，且同一提交的 PR CI 从 pending 进入 success / failure 时通知。通知包含结论、提交 SHA 和链接，经既有 leader 通知送达。观察和通知去重在同一事务持久化，同一次运行重查或服务重启后不重复通知。

```sql
CREATE TABLE IF NOT EXISTS git_ci_state (
    project TEXT NOT NULL REFERENCES projects(name),
    subject TEXT NOT NULL,
    run_key TEXT NOT NULL,
    sha TEXT NOT NULL,
    status TEXT NOT NULL,
    notified INTEGER NOT NULL CHECK (notified IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project, subject, run_key)
);
```

`subject` 为 `base:<分支>` 或 `pr:<编号>`，`run_key` 区分运行，`sha` 将 PR 状态变化关联到同一提交；`notified` 记录这次运行是否已排通知。缺失表在部署后重启服务时自动创建，无需迁移已有表。


### 整组关闭、撤销与托管进程

`POST /api/group/close` 接收 `{group, with_members}`，从当前项目查找分组及 leader，拒绝 Git 管理员。总是归档 leader 并关闭分组；`with_members=true` 归档全部下属，false 则下属保留名字和状态、退出分组，进入待收编的“有名字但不在组里”。无下属时同样归档 leader。响应为 `{ok:true, before}`。

`POST /api/group/undo` 接收 `{group, before}`，校验当前项目、原组标识、leader、快照字段、控制记录、成员项目和当前分组关系；原组已经重新打开、成员已进入别组或恢复名字冲突时拒绝。响应为 `{ok:true}`。快照只由前端 toast 的撤销动作持有，不新增表、不单独落库：

```json
{
  "group": {"project":"demo", "name":"lead", "leader_session_id":"leader-id", "note":null, "created_at":"…", "closed_at":null},
  "sessions": [
    {"session_id":"leader-id", "before":{"crew_name":"lead", "group_name":"lead", "status":"idle", "status_note":null, "ended_at":null, "control":null}}
  ]
}
```

`sessions` 包含操作前组内全部会话及 leader；`control` 是完整的原 `session_controls` 行（`session_id, archived_status, archived_note, archived_ended_at, removed, name_override`），原来没有行时为 null。`session_ops.close_group` 和 `undo_group` 各自使用一个事务；归档复用单会话的事务内 `_archive`，撤销复用 `_undo`，恢复组记录、分组关系、状态与完整控制行，任意成员失败整体回滚。`board.close_group` 已删除，生产调用方使用 session_ops 或 Service 编排。

网页单会话归档/移出、整组归档和 VS Code 归档同步先提交数据库，再调用 `Manager.stop_managed`：无论 busy 或 idle，都终止对应网页托管进程，最多等待退出 10 秒，再最多等待输出读完 10 秒；失败显式报错，数据库保持已归档，避免后续 hook 复活会话。保留下属的关闭方式只停止 leader。外部 VS Code/终端进程不属于 Manager，不由它终止。CLI 整组关闭通过 `/api/group/close` 使用相同编排。

Git 页停用在一个数据库事务中先删除 `git_management` 配置，再调用同一事务内整组关闭逻辑归档管理员和下属；失败整体回滚。提交后 Service 停止对应托管进程。通用整组结束/撤销及单会话归档/移出都拒绝当前 Git 管理员，必须从 Git 页停用。

撤销只恢复数据库，不自动启动进程。恢复后 leader 在下次收到用户网页消息时续跑，下属在下次发消息或叫醒时按现有恢复逻辑续跑；“停下这一轮”仍只发送 interrupt，与归档终止进程是两个动作。
