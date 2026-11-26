# 增量事件流设计：快照 + 序号

> 日期：2026-11-27 · 分支 `claude/event-stream`（基于 `9a2cc6f`）· 依据：[架构重审](ARCHITECTURE_REVIEW.md) §6.2 / §7 阶段 3、[拆分边界](SPLIT_BOUNDARIES.md) §3.3、[交互清单](INTERACTION_INVENTORY.md)
> 状态：协议、存储、读取、订阅处理函数、前端数据层模块与待办试点已实现并有测试；**删除 3 秒轮询与 `app.js` 接线暂停**，原因见第 9 节“前提冲突”，等主控拍板。

## 1. 目标与边界

前端现在每 3 秒拉一次 `/api/state` 全量快照。改为：先取一次快照（带序号），再按序号订阅增量；断线从上次序号续传，续不上就要求重拿快照；重连只恢复订阅，不重发任何写请求。桌面网页与将来的手机 PWA 共用同一协议。

不在本文范围：写请求的 `request_id` 去重与写响应带 `seq`（操作清单 R5，架构重审 §6.1）；统一用户输入请求（§6.3）；对话日志 `/api/chat/events`（保持现有格式与它自己的序号，客户端分别保存两种游标）。

## 2. 存储：`change_log`

```sql
CREATE TABLE IF NOT EXISTS change_log (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,      -- 提交时的 UTC 时间
    scope   TEXT NOT NULL,      -- 'global' | 'workspace:<工作区名>'
    type    TEXT NOT NULL,      -- '<kind>.updated' | '<kind>.removed'
    entity  TEXT NOT NULL,      -- 实体在该 kind 内的 ID（文本）
    data    TEXT                -- 实体在快照中的完整 JSON；删除为 NULL
);
CREATE INDEX change_log_at (at); CREATE INDEX change_log_scope (scope, seq);
```

- **迁移**：新表走 `schema.sql` 的 `CREATE ... IF NOT EXISTS`（与现有新表同一做法），旧库原样保留，重复连接与并发首次连接都安全（测试 `MigrationTests`）。不往 `schema.sql` 加种子行，因为它在每次 `store.connect` 都执行，种子 INSERT 会让每次连接都变成一次写。
- **不复用 `events`**：`events` 是审计日志，待办、通知、恢复、摘要都在读它，不能套裁剪窗口（架构重审 §6.2）。
- **与架构重审的差异**：不建 `change_log_state` 水位表。裁剪只删最老的一段连续前缀，所以水位 = `min(seq) - 1`；表被裁空时 = 最后分配的序号（SQLite 为 AUTOINCREMENT 维护的 `sqlite_sequence`，裁剪不降低它）。保留规则按全局序号与时间计算，按范围分别记水位只会得到同一个值。

## 3. 序号、保留窗口与续传规则

| 项 | 规则 | 依据 / 测试 |
| --- | --- | --- |
| 序号 | 全局单调、永不重复。SQLite 同时只有一个写者，`BEGIN IMMEDIATE` 拿到写锁后才分配序号，提交顺序 = 序号顺序，读者不会先看到 7 后看到 6 | `ConcurrencyTests`：4 个连接各写 40 条，同时一个读者跟读，读到的序号恰好是 1..160 且每次只前进 |
| 回滚 | 业务写回滚时增量一起回滚，序号也不被占用（`sqlite_sequence` 在同一事务里） | `test_rolled_back_business_write_leaves_no_change_and_no_used_sequence` |
| 保留 | 最近 10,000 条或 24 小时（初值，阶段 3 用真实数据校准）。每分配 256 个序号在同一事务里裁剪一次；按时间裁剪时取“早于截止时间的最大序号”为边界整段删除，时钟回拨也不会删出空洞 | `test_age_pruning_keeps_one_contiguous_prefix...`、`test_recording_prunes_on_its_own...` |
| 续传 | `after == 水位` 可续；`after < 水位` → `snapshot-required{reason:"pruned"}`；`after > 最新序号` → `"ahead"`；该订阅可见的积压超过 1000 条 → `"backlog"` | `ReadTests` |
| 可见范围 | 有工作区的订阅看 `global` + `workspace:<名>`；入口页（无工作区）只看 `global` | `test_resume_returns_exactly_the_changes...` |

## 4. 快照与事件格式

### 4.1 快照 `GET /api/snapshot?project=<名>`

```json
{"seq": 41, "data": { …与现在 /api/state 完全相同… }}
```

`seq` 在构造 `data` **之前**读取（`change_feed.snapshot`）。架构重审要求“同一个只读事务”，但现在的 `state()` 自己开连接、还会调 `scan_usage` 写库（读路径去副作用是迁移阶段 2，尚未做），做不到同事务。先读序号的保证：构造期间提交的变更可能已经在 `data` 里，订阅后会再收到一次；事件按实体整值替换、按序应用，重复应用是幂等的，追上之后结果与最新状态一致，不会漏。客户端把追赶阶段的事件攒到 `ready` 再一次合并，避免中间态闪回。

### 4.2 订阅 `GET /api/events?project=<名>&after=<seq>`（SSE）

```
id: 42
data: {"seq":42,"at":"…","type":"todo.updated","entity":"7","data":{…快照 todos 里该项的完整结构…}}

id: 43
data: {"seq":43,"at":"…","type":"todo.removed","entity":"7","data":null}

event: ready
data: {"seq":43}          ← 订阅前已提交的积压发完，之后都是实时变更

: heartbeat               ← 空闲每 15 秒

event: snapshot-required
data: {"reason":"pruned"} ← 发完即关闭连接
```

- 游标取 `after` 与 `Last-Event-ID` 中较大者（与 `/api/chat/events` 相同），两者都没有或不是非负整数 → 400。
- 服务端循环：每 0.5 秒醒来按 `seq > 游标` 重新查表 → 发送 → 推进游标。**不依赖进程内通知**：`crew todo add` 等 CLI、hook、Codex 工具在别的进程写同一个库，进程内通知收不到；查询走主键索引，每连接每秒两次本机 SQLite 查询，代价可忽略。真实套接字冒烟：写入后约 0.2 秒到达订阅端。
- 远程设备：沿用 `_chat_events` 的 `stream_opened/stream_closed` 计数与 `remote_stop_event`；每次醒来经 `handler._request_active()` 检查设备是否已吊销，已吊销即结束。
- 服务停止（`service.stop`）立即结束循环。

### 4.3 事件内容与 kind 登记

事件按实体给出**快照中的完整新值**，客户端按 `kind` 找到它在快照里的位置并按实体 ID 替换或删除；不用 JSON Patch。后端 `change_feed.KINDS` 与前端 `CrewAppData.KINDS` 是同一份登记，记录未登记的 kind 直接报错（后端 `ValueError`，前端 `UnknownChange` → 状态 `failed` 并提示刷新页面），不静默丢弃。schema 只增不改：新增 kind 必须先进前端登记，再由后端产生。

当前登记：

| kind | 快照位置 | 实体 ID | 排序（与后端一致） | 派生字段 |
| --- | --- | --- | --- | --- |
| `todo` | `todos[]` | `id` | 阻塞在前 → `created_at` → `id`（`todo.list`） | `todo_count = todos.length` |

## 5. 写入与业务写同事务

- `change_feed.updated / removed` 只能在业务写已开启的事务里调用（否则 `RuntimeError`），与业务写同一连接、一起提交或回滚。`board.transaction`（`BEGIN IMMEDIATE`）与 `goals.transaction`（嵌套时 SAVEPOINT）都满足；SAVEPOINT 回滚同样撤销增量。
- 实体新值在同一事务里按快照的构造函数现算（待办用 `todo.get`，与 `todo.list` 同一 `_enrich`），保证事件值与之后重建的快照一致（测试逐项比较 `data == todo.list(...)[i]`）。
- **库外副作用**（追加会话日志 JSONL、启动进程、投递输入）不能随 SQLite 回滚，按架构重审 §6.2 “持久意图 + 发件箱”另做，不在本次试点范围。

### 5.1 待办试点（已实现）

| 业务写 | 位置 | 产生的事件 |
| --- | --- | --- |
| 登记（含 CLI / Codex 工具 / 目标转待办经 `add_native`） | `todo.add` | `todo.updated`；重复登记（`duplicate`）不产生事件 |
| 用户完成 / 取消、AI 撤回 | `todo._change`（`resolve` / `withdraw`） | `todo.removed`（快照只列 open） |
| 撤销 | `todo.undo` | 恢复为 open → `todo.updated`；否则 `todo.removed` |
| 关联到任务执行 | `goal_links.bind`（kind=todo） | `todo.updated`（`goal_ids`、`goal_links` 变化） |
| 标记已投递 / 归档原轮次 | `goal_links._mark_delivered`、`goal_links.deliver` 失效分支 | 按当前状态 updated / removed（`goal_links[].delivered_version` 变化） |
| 工作区清理批量取消 | `maintenance.Manager._archive` | 每条原 open 待办一个 `todo.removed` |

待办里还嵌着别的领域的派生值：提出者名、所属组与 leader（`proposer_name`、`group`、`leader_session_id`、`leader_name`）、任务标题（`goal_links[].goal_title`）。改名、换组、关组、归档会话、任务改标题时必须重发受影响的待办；这些写入属于会话 / 组 / 任务领域，随这些领域接入时一起做（见 6.1 “跨实体派生”）。在此之前不能删除轮询（第 9 节）。

## 6. 快照逐项对应：来源与生产者

按 `web_service/snapshot.py` 的 `state()` 与 `_project_conversations()` 逐项列出。来源分五类：**DB**＝只读数据库，可在业务写处同事务产生；**托管**＝`chat.Manager` 的进程、会话日志与活动内存状态（`chat.py`）；**扫描**＝后台线程或 `state()` 自身驱动的文件 / 进程扫描；**派生**＝由其他字段与当前时间算出；**常量**。

### 6.1 顶层字段

| 字段 | 来源 | 建议 kind / 范围 | 生产者位置 | 状态 |
| --- | --- | --- | --- | --- |
| `todos`、`todo_count` | DB `todos` + 会话 / 组 / 任务标题 | `todo` / 工作区 | 见 5.1 | **已实现**（跨实体派生待会话、组、任务领域） |
| `projects`、`project` | DB `projects`、`project_themes`；`project.root` | `project`（列表 `projects[]` 按 `name`）/ global；`workspace` 字段 / 工作区 | `board.init_project`、工作区设置、主题 | 待做 |
| `ai` | DB `ai_settings` 等 | `ai`（整值）/ global | `settings.save` 等 | 待做 |
| `decisions` | DB `decisions` + `decision_briefs` / `decision_flow` 富化 + `trust_mode` | `decision`（`id`）/ 工作区 | `board.ask/answer/withdraw`、拍板流程、简报 | 待做 |
| `goals` | DB `goals` 等（`goal_api.card`）；只含可见 leader 的任务 | `goal`（`id`）/ 工作区 | `goals.*` 写入 | 待做 |
| `domains` | DB（`domains.summary`） | `domains`（整值）/ 工作区 | `domains.*` | 待做 |
| `channel` | DB `channel.state` + `channel_digest` 内存（`digesting`、`digest_error`） | `channel`（整值）/ 工作区 | `channel.*` 写入；摘要管理器状态变化 | 待做；R2 合入后接（与 R2 文件归属错开） |
| `native_approvals` | 内存：Codex app-server 待审批（`native_approvals.py`） | `approval`（`id`）/ 工作区 | 审批到达 / 答复处 | 待做 |
| `candidates` | 扫描：外部进程列表 + VS Code 会话扫描（`workers.py` 每 10 秒） | `candidates`（整值）/ 工作区 | `refresh_agents`、`_read_vscode` 结果变化时 | 待做 |
| `agents_error`、`vscode_error` | 扫描（同上） | `scanner`（整值）/ global | 同上 | 待做 |
| `limits`、`usage_scanned_at` | DB `codex_rate_limits`（由用量扫描写入）+ Claude 额度文件 + `crew_usage` / `claude_budget` 内存 | `limits`（整值）/ global | **前置**：`scan_usage` 现在由 `state()` 每次调用驱动，删轮询后无人驱动，须先按迁移阶段 2 移到后台扫描线程，在扫描结果变化处产生 | 待做（依赖阶段 2） |
| `runtimes` | 内存：原生载体发现（`runtime_catalog`） | `runtimes`（整值）/ global | 发现刷新完成处 | 待做 |
| `notification` | `connections` 管理器（渠道状态、离开状态） | `notification`（整值）/ global | 渠道状态变化、`set_away` | 待做 |
| `sounds` | DB 音效设置 + 会话日志 | `sounds`（整值）/ 工作区 | 设置写入；日志相关部分依赖托管生产者 | 待做 |
| `git_assistant`、`git_branches` | DB `git_management` + leader 卡片 + Git 进程读取 | `git`（整值）/ 工作区 | Git 设置写入、Git 读取刷新、Git 助手 leader 卡片变化 | 待做 |
| `attention` | 派生：会话、拍板、派工任务、**会话日志事件** | `attention`（`id`）/ 工作区 | 依赖托管生产者 | 待做（依赖托管） |
| `leaders`、`members` | **混合**，见 6.2 | `leader` / `member`（`session_id`）+ `leader_order`（顺序整值）/ 工作区 | 见 6.2 | 待做（依赖托管） |
| `now` | 服务端时钟，前端用于活动计时校准 | 不作为 kind；`ready` 与心跳改为带 `now`，客户端更新校准 | `web_service/events.py` | 待做（接线时一起做） |
| `themes`、`models`、`efforts` | 常量 | 无（随快照） | — | 不需要 |

### 6.2 leader / member 卡片

卡片是同一实体里混合多种来源，事件必须给出整张卡片，所以先把 `snapshot.py` 的卡片构造拆成“按一个 `session_id` 构造一张卡片”的函数，任何来源变化都对该实体重算整卡。

| 卡片字段 | 来源 |
| --- | --- |
| `name`、`goal`/`task`、`group`、`leader_session_id`、`member_ids`、`cc_name`、`branch`/`worktree`/`cwd` | DB 会话与组 |
| `color`、`avatar_version`、`pinned`、`trust`、`model`/`effort`/`speed`、`runtime`、`member_defaults`、`read_seq`、`notices_pending`、`pending`、`scope`/`done_when`、`progress`、`leases`、`claims`、`active_goal_id(s)`、`execution_records` 的 DB 部分 | DB |
| `status`、`busy`、`running`、`where` | **托管**进程状态 + 外部进程扫描 |
| `activity`、`applied`、`context`（压缩小环） | **托管**（会话日志、活动阶段、原生上下文） |
| `last_seq`、`last_reply_id`、`unread`、`web_chat`、`general_has_output` | **托管**会话日志 |
| `codex`、`codex_older`、`dispatches` | DB 派工记录（由用量扫描写入）+ **托管** `subagent_tasks`；并且“最近 24 小时”窗口随时间推移变化（派生） |
| `title` | 扫描：Claude 转录标题 |
| `runtime_switch` | DB + 托管 + 外部进程 |

托管部分的生产者只能放在 `chat.py`（`Journal.emit / set_status / _phase / _tokens`、`Running` 启停、`Manager._read`）：按架构重审，高频进度按实体合并，每实体每秒最多一条并保证最后一次送达；结束、删除、审批等不可丢事件单独落盘；生产者在短事务里写 `change_log`，不得在写锁里调用完整 `state()`。

## 7. 前端合并规则（`frontend/app-data.js`，`window.CrewAppData`）

- `create(ctx)`：`ctx` 由 `app.js` 提供 `origin`、`project`、`state`（getter，状态仍归外壳所有，测试对 `state=` 的赋值照常被看到）、`api`（晚绑定）、`fetch`、`EventSource`、`setTimeout/clearTimeout`、`onState(next, previous, meta)`、`onStatus(status, detail)`、`onError(error)`。返回 `queryPath`、`request`、`loadState(fresh)`、`stop`、`seq`、`status`。
- **加载**：`loadState()` = 取快照 → `onState(快照, 旧状态, {source:"snapshot"})` → 以快照 `seq` 建**一个**订阅。并发调用共用一次加载，`fresh` 在当前加载完成后再取一次（与原 `loadState` 合同相同）。失败抛给调用方（外壳显示“加载失败”或提示），同时数据层按退避自动再取，页面能自己恢复。
- **合并**：只应用 `seq > 当前序号` 的事件（重复送达自动忽略）；按 kind 登记找到列表，按实体 ID 替换 / 追加 / 删除，按登记的排序重排并重算派生字段；返回新对象，未改动的部分保持原引用，旧状态不被修改（外壳仍可比较新旧状态，例如音效）。
- **追赶**：`ready` 之前到达的积压先攒起来，`ready` 时一次合并并标 `meta.catchUp = true`；外壳据此不重播过去的音效与提醒（架构重审：音效只对晚于本次连接建立的事件触发）。
- **断线**：连接出错 → 关闭该订阅（不用浏览器自带重连，避免两个订阅并存）→ 状态 `disconnected` → 按 1、2、5、10 秒退避，用最后应用的序号重新订阅，不重拿快照；订阅收到 `ready` 后退避归零。
- **`snapshot-required`**：关闭旧订阅、取消旧重连定时器 → 状态 `resyncing` → 重拿快照 → 以新序号建一个订阅。自动重同步失败时经 `onError` 报告并按退避再取。
- **不重放写操作**：数据层的 `request` 每次调用只发一次请求，重连与重同步只重建订阅与快照，测试断言整个断线 / 重同步过程中 POST 只有一次。写请求结果的查询依赖 R5 的 `request_id`。
- **状态**：`connecting`、`live`、`disconnected`、`resyncing`、`failed`（页面版本过旧）、`idle`。断开期间外壳必须显示“已断开”，不把旧数据当在线状态展示（接线时做）。
- 外壳渲染抛出的错误不被数据层吞掉（只捕获 `UnknownChange`）。

## 8. 与操作清单 / MCP、手机端的关系

- **操作清单（R5）**：清单负责命令面（写入口、`request_id` 去重、权限）；事件流负责状态面。写操作执行器拥有连接与事务，处理函数在该连接上做业务写，执行器在同一事务里写回执与 `change_log`，写响应带最后一个 `seq`，客户端据此判断“我的修改是否已在当前状态里”，届时可去掉写后 `loadState(true)` 的整份快照重取。`change_feed.updated / removed` 不关心调用方是 HTTP、CLI 还是 MCP，只要求在同一事务里。
- **MCP / AI 会话**：AI 不订阅工作区事件流；它通过 MCP 工具读写。MCP 写入经同一执行器，因此 AI 的操作会实时出现在所有打开的页面上。
- **手机端**：PWA 与桌面共用 `frontend/` 与本协议。弱网下断线续传只补差量；补不上才重拿快照。远程设备吊销时长连接立即结束。手机息屏 / 标签页隐藏时是否断开订阅（对话页 `chat.js` 是隐藏即断、可见再补读）待接线时按交互清单 `chat.read-visibility` 统一。
- **其他仍在轮询的入口**（不在本次范围，列出供后续统一）：`remote-access-ui.js` 每 5 秒、桌面 `tabs.cjs` 读 `/api/workspaces/activity`、`assistant.js` 的 `/api/assistant/state`。它们可在对应领域接入事件流后改为订阅 `global` 范围。

## 9. 前提冲突（停工点，待主控拍板）

任务书要求“在会改变快照内容的业务写入处记录变更”并“删除 3 秒全量轮询，不保留开关”，同时“不得修改 `chat.py`、`chat_*.py`、`web.py`、`web_api/`”。现场核对后，这几条不能同时满足：

1. **快照里大量字段没有“业务写”**：leader / member 卡片的忙闲、在线位置、活动计时与阶段、token 滚动、压缩小环、未读数与最后回复、`attention`、派工 / 子代理任务，都来自 `chat.Manager` 的进程与会话日志内存状态（6.2），生产者只能加在 `chat.py`，而本任务禁止改它（另一个任务正在拆）。
2. **`state()` 本身就是用量扫描的驱动者**：`state()` 每次先调 `scan_usage(live_dispatch=True)`，派工回执、Codex 额度、Claude 周用量都靠 3 秒轮询触发写库（6.1 `limits`）。删掉轮询，这些数据就不再更新。架构重审把“读路径去副作用、扫描移到后台”列为迁移阶段 2，是阶段 3 的前置，尚未做。
3. **外部扫描结果**（外部进程、VS Code 会话、候选人、Git 分支、原生审批）都是后台线程或请求时读取的内存状态，也没有业务写。
4. 架构重审 §6.2 / §7 原定“试点一个领域，**全部领域覆盖前不删轮询**”，与任务书“直接删轮询”冲突。

若此时删除轮询，上面这些字段在页面上会冻结在快照那一刻，直接违反交互零丢失门禁（`chat.activity-*`、`leader.row-unread`、`rail.nav-unread`、`workbench.working*`、`chat.context-ring.*` 等，见交付核对表）。所以本次**没有**删除轮询、**没有**把 `app.js` 接到新数据层。

可选方案（需主控决定）：

| 方案 | 做法 | 优点 | 代价 |
| --- | --- | --- | --- |
| A（推荐，按架构重审原路线） | 现在把 DB 领域（6.1“DB”行）按待办试点的做法铺开；先做阶段 2 把 `scan_usage` 等扫描移到后台并在结果变化处产生事件；`chat.py` 拆分合入后在托管生产点接入 leader / member / attention；全部覆盖后接线 `app.js` 并删除轮询 | 不留过渡代码；每个字段由真实变化处产生，符合“读者不再采集” | 轮询要等 `chat.py` 拆分之后才能删 |
| B | 新增服务端“比对生产者”：有订阅时后台按间隔（或会话日志唤醒时）重建工作区快照，按实体比对后写 `change_log`；可立刻删轮询，不碰 `chat.py` | 删轮询最快，网络流量立刻降到心跳级 | 后端仍周期性构造完整 `state()`（含 `scan_usage` 写锁），违背架构重审“生产者不得调用完整 state()”；是过渡代码，之后要整条删除；事件不与业务写同事务 |
| C | DB 领域同事务产生 + 托管 / 扫描字段暂用 B 的比对生产者，`chat.py` 拆分后逐个替换 | 能较早删轮询，DB 领域一步到位 | 同时维护两种生产方式一段时间，替换完成前仍有 B 的代价 |

另需确认一处小出入：任务书第 4 条把 `replace` 列为数据层要抽出的内容，而拆分边界 §3.3 列的是 `api`、`queryPath`、`loadState`、轮询定时器、`notifySounds`。`replace` 是渲染辅助（依赖 `composingInput` 等外壳状态，负责焦点、选区、滚动保留），本次没有抽出，建议留在 `app.js` 渲染层。

## 10. 待接线

### 10.1 路由（`web.py` 的 `Handler._dispatch` GET 分支，主控登记）

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/snapshot` | `web_service.events.serve_snapshot(self, service, query)` | 返回 `{seq, data}`；`query` 为 `parse_qs(url.query)` |
| GET | `/api/events` | `web_service.events.serve_events(self, service, query)` | SSE 长连接；`after` 缺失或非法时抛 `ValueError`（由 `_handle` 回 400），工作区不存在抛 `BoardError` |

- 导入：`from .web_service import events as change_events`（`web.py` 已有 `from .web_service...` 子包导入方式）。
- 远程访问：两个路径都是读接口，与 `/api/state`、`/api/chat/events` 同权限；若远程鉴权中间件按路径白名单区分长连接，需把 `/api/events` 登记为与 `/api/chat/events` 同类。
- `Handler.log_message` 里“页面每 3 秒轮询”的注释在删除轮询后应改写。
- 删除轮询之后，`/api/state` 只剩桌面 `web.py:1368/1385` 自用与 `test_web_contract` 等测试调用，是否保留由迁移阶段 5 统一决定。

### 10.2 静态文件（`web.py` 的 `STATIC`，主控登记）

| 路径 | 文件 | 类型 |
| --- | --- | --- |
| `/app-data.js` | `app-data.js` | `text/javascript; charset=utf-8`（与 `/app.js` 相同写法） |

同时登记 `plugin/tests/test_assistant_http.py::test_new_page_assets_are_served_by_the_actual_backend`。

### 10.3 前端接线（第 9 节拍板后执行）

1. `index.html`：在 `<script src="/app.js" defer>` 之前加 `<script src="/app-data.js" defer></script>`。
2. `app.js`：`queryPath` 与 `api` 的通用部分改为 `CrewAppData.create(ctx)` 返回的 `queryPath` / `request`，外壳保留 `function api(path, data)`（发送前后的 `waitingSounds` 记账留在外壳）与 `function loadState(fresh)` 同名绑定，使测试注入 `api=`、`loadState` 照常可用；`ctx` 用 `get state() { return state; }`、`api: (...a) => api(...a)` 晚绑定。
3. `onState` 承接原 `loadState` 成功分支的全部渲染逻辑（`CrewSound.observe`、`nativeApprovals.sync`、`CrewActivity.sync`、路由守卫、`render`、`refreshUsage`、`notifySounds`），`meta.catchUp` 或 `source === "snapshot"` 时不播音效。
4. 删除 `setup()` 里的 `setInterval(loadState, 3000)`；首次加载失败页的“每 3 秒自动重试”文案改为“正在自动重连”，`test_app_state_ui.js` 对该文案的断言同步改写（行为等价改写，列入交付）。
5. 增加“已断开”显示（`onStatus`），按交互清单补验 `workbench.initial-retry`、`workbench.refresh-focus`、`workbench.ime-refresh`。
