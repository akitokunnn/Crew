# 增量事件流设计：快照 + 序号

> 日期：2026-11-27 · 分支 `claude/event-stream`（基于 `9a2cc6f`）· 依据：[架构重审](ARCHITECTURE_REVIEW.md) §6.2 / §7 阶段 3、[拆分边界](SPLIT_BOUNDARIES.md) §3.3、[交互清单](INTERACTION_INVENTORY.md)
> 状态（第 2 轮，2026-11-27）：协议、存储、读取、订阅处理函数、前端数据层模块已实现；6.1 中全部“DB”领域在业务写的同一事务里记录整值变更（第 5 节）；迁移阶段 2 完成，`state()` 只读，用量扫描移到后台线程（第 6.3 节）。主控已选方案 A（第 9 节）：会话托管状态的生产者等 `chat.py` 拆分（C1）合入后接入，**3 秒轮询与 `app.js` 接线保持不动**。

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

`seq` 在构造 `data` **之前**读取（`change_feed.snapshot`）。架构重审要求“同一个只读事务”：阶段 2 之后 `state()` 已不写库、不拿写锁（6.3），但它仍自己开连接、并读进程与会话日志等库外状态，所以仍按“先读序号”处理。先读序号的保证：构造期间提交的变更可能已经在 `data` 里，订阅后会再收到一次；事件按实体整值替换、按序应用，重复应用是幂等的，追上之后结果与最新状态一致，不会漏。客户端把追赶阶段的事件攒到 `ready` 再一次合并，避免中间态闪回。

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

当前登记（后端 `change_feed.KINDS` = `BUILDERS` 的 9 种 + `limits`；前端 `CrewAppData.KINDS` 同名同形，测试 `test_the_frontend_registry_mirrors_the_backend_kinds` 逐项比对）。三种形状：**列表**（按键替换 / 删除后按后端顺序重排）、**字段**（整值替换，没有删除）、**字段组**（只合并登记的几个字段，同一对象里其他来源的字段保持原值；字段不一致视为页面过旧）：

| kind | 范围 | 形状 · 快照位置 | 实体 ID | 排序 / 字段 | 构造函数（`snapshot_values` 等，快照同用） |
| --- | --- | --- | --- | --- | --- |
| `todo` | 工作区 | 列表 `todos[]` | `id` | 阻塞在前 → `created_at` → `id`；派生 `todo_count` | `todo.change_value` |
| `project` | global | 列表 `projects[]` | `name` | `name` | `project`（已移除 → `removed`） |
| `workspace` | 工作区 | 字段 `project` | 工作区名 | — | `workspace` |
| `ai` | global | 字段 `ai` | `ai` | — | `ai` |
| `decision` | 工作区 | 列表 `decisions[]` | `id` | `id`（只列 open） | `decision` |
| `goal` | 工作区 | 列表 `goals[]` | `id` | `priority` 降序 → `created_at` → `id`（只列可见 leader 的任务） | `goal` |
| `domains` | 工作区 | 字段 `domains` | 工作区名 | — | `domains` |
| `channel` | 工作区 | 字段组 `channel` | 工作区名 | `unread`、`latest_id`、`digest_id`（`digesting`、`digest_error` 属摘要管理器内存，未接入） | `channel` |
| `sound_settings` | global | 字段组 `sounds` | `sounds` | `preferences`、`revision`、`catalog`、`missing`（`events` 随会话日志，未接入） | `sound_settings` |
| `limits` | global | 字段组（快照顶层） | `limits` | `limits`、`usage_scanned_at`、`usage_error` | 后台用量扫描直接记录（6.3） |

## 5. 写入与业务写同事务

- **标记 + 提交前现算**：写入点只调用 `change_feed.touch(conn, kind, 工作区, 实体)`（可在事务内任意位置，同一实体去重、保持首次标记顺序）。`board.transaction` 在 `BEGIN IMMEDIATE` 后 `change_feed.begin`，**提交前** `change_feed.flush` 按 `BUILDERS` 登记的构造函数用事务内的最终状态现算每个实体的值并写 `change_log`，`finally` 里 `end`。所以增量与业务写同连接、同事务提交或回滚；一个事务里多次写同一实体只记一条最终值，中间态不外泄（测试 `test_an_entity_written_many_times_in_one_transaction_is_recorded_once_with_its_final_value`）。构造函数返回 `None` 表示快照不再列出该实体 → 记 `removed`。
- **严格**：不在 `board.transaction` 里标记（自动提交模式、或裸 `BEGIN`）直接 `RuntimeError`；未登记 kind、范围与 kind 不符直接 `ValueError`。生产代码对本地库的顶层写事务来自 `board.transaction`（`goals.transaction` 嵌套时用 SAVEPOINT，`usage._transaction` 也落到它），唯一的裸事务（工作区导入）已接入同一记录流程（见下），所以没有放行分支。SAVEPOINT 回滚后标记仍在，提交时按当时的真实状态重算，只会多一条与快照一致的幂等事件。
- **构造函数与快照同源**：`plugin/crew/snapshot_values.py` 里的函数同时被 `web_service/snapshot.py` 调用（工作区列表、当前工作区、AI、拍板、任务可见性、架构图、交流区、音效设置），事件值与之后重建的快照逐字段相同（`test_change_feed_domains` 每条用例都把增量与新快照比对）。构造函数只读；在旧库上读工作区可能先补主题（`board._ensure_project_themes`），这会再次标记，`flush` 循环到没有新标记为止。
- 非数据库来源（用量扫描合成的额度值）不走构造函数，在产生它的事务里直接 `change_feed.updated`（6.3）。
- **库外副作用**（追加会话日志 JSONL、启动进程、投递输入）不能随 SQLite 回滚，按架构重审 §6.2 “持久意图 + 发件箱”另做，不在本文范围。上传 / 删除自定义音效只改文件，写文件后单独开一个事务记录 `sound_settings`。
- **严格的例外只有一处**：`storage_v2/activation.py` 的工作区导入（`activate_workspace`）自己开裸 `BEGIN IMMEDIATE`（需要 `defer_foreign_keys`），本轮让它像 `board.transaction` 一样 `begin` / 提交前 `flush` / `end`，所以它经 `board.init_project` 标记的工作区列表与字段同事务记录。导入批量插入的会话、任务、拍板、待办**不逐条记录**：导入既有问题——旧库里的进展报告（`data_json` 无 `requirement_version`）导入后版本号排到交付报告之后，`goal_api.card` 取该键 `KeyError`，这样导入的工作区 `state()` 本来就打不开（`test_storage_v2_features` 两条真实链路可复现）。若此时整区重发，这类导入会从“成功但页面打不开”变成整体回滚，属于改变存储口径，已写入交付待主控决定；修好排序后再在导入里整区标记。新建的导入工作区没有订阅者，受影响的只有“导入到一个页面正开着的空工作区”这一情形。导入修复（`repair_activations`）只写可移植库元数据与审计事件，不影响快照。
- **迁移不是生产者**：`store.py` 启动迁移与 `home_migration.py` 换库不记录变更；换库后序号从头开始，持有旧序号的客户端会得到 `ahead` → 重拿快照。

### 5.1 待办试点（已实现）

待办写入点改用 `todo.touch`（第 1 轮的 `publish_change` 已删除）。

| 业务写 | 位置 | 产生的事件 |
| --- | --- | --- |
| 登记（含 CLI / Codex 工具 / 目标转待办经 `add_native`） | `todo.add` | `todo.updated`；重复登记（`duplicate`）不产生事件 |
| 用户完成 / 取消、AI 撤回 | `todo._change`（`resolve` / `withdraw`） | `todo.removed`（快照只列 open） |
| 撤销 | `todo.undo` | 恢复为 open → `todo.updated`；否则 `todo.removed` |
| 关联到任务执行 | `goal_links.bind`（kind=todo） | `todo.updated`（`goal_ids`、`goal_links` 变化） |
| 标记已投递 / 归档原轮次 | `goal_links._mark_delivered`、`goal_links.deliver` 失效分支 | 按当前状态 updated / removed（`goal_links[].delivered_version` 变化） |
| 工作区清理批量取消 | `maintenance.Manager._archive` | 每条原 open 待办一个 `todo.removed` |

待办里还嵌着别的领域的派生值：提出者名、所属组与 leader（`proposer_name`、`group`、`leader_session_id`、`leader_name`）、任务标题（`goal_links[].goal_title`）。任务标题没有写入点（`goals.py` 不改 `title`），无需联动；改名、换组、关组、归档会话属于会话 / 组领域（依赖托管生产者，见 6.1），随这些领域接入时重发受影响的待办。

### 5.2 第 2 轮铺开的领域（全部直接写入点）

| kind | 业务写（位置） | 联动 |
| --- | --- | --- |
| `project` + `workspace` | 创建 / 重新打开（`board.init_project`）、补主题（`board._assign_project_themes`）、改主题（`board.set_project_theme`）、改显示名或目录（`workspace_settings.apply_update`）；移除（`workspace_settings.remove`）只记 `project.removed` | `board.touch_project` 同时标记两者 |
| `ai` | `settings.save`（含 `ai_settings` 与 `ai_task_settings` 的增删） | — |
| `decision` | 提问 `board.ask`、答复 `board.answer`（含 `trust_mode.auto_answer`、远程渠道作答）、撤回 `board.withdraw`、记录归属 leader `trust_mode.record_decision_scope`、改写 `decision_briefs.revise`、保存简报 `decision_briefs._save`、讨论 / 给选项 `decision_flow.discuss/ready`、关联执行 `goal_links.bind`、工作区清理批量撤回 `maintenance.Manager._archive` | `snapshot_values.touch_decision` 同时标记关联任务（`pending_decision`） |
| `goal` | `goals.py` 全部写函数（`create`、`amend`、`assign`、`transition`、`request_pause`、`resume_review`、`resume`、`continue_task`、`reopen`、`configure_execution`、`create_execution`、`set_execution_status`、`save_report`、`review_acceptance`、`observe`、`request_finish`、`finalize_finish`、`request_cancel`、`request_transfer`、`finalize_transfer`，经 `goals._touch` 或直接 `goals.touch`）、工作清单 `goal_work.add/update/carry_forward/hand_over`、决策模式 `goal_mode.initialize/set_mode`、上下文回执改执行版本 `goal_context.mark_applied` | 拍板答复 / 撤回联动任务卡；不进卡片的表（`goal_context_*`、`goal_messages`、执行已读、原生绑定、`goal_ops_pauses`）不标记 |
| `domains` | `domains.apply`（全部改动批次）、永久删除 leader 时 `domains.release_leader` | — |
| `channel` | 发言 `channel.post`、自动指引 `channel.settle`、用户已读 `channel._mark_read`（leader 自己的已读不在页面上，不标记）、要点卡 `channel_digest.Manager._run`（插入改为在事务内与清理边界检查一起提交）、工作区清理边界 `maintenance.Manager._archive` | — |
| `sound_settings` | `event_sounds.save`、上传 `add_custom`、删除 `delete_custom` | — |

## 6. 快照逐项对应：来源与生产者

按 `web_service/snapshot.py` 的 `state()` 与 `_project_conversations()` 逐项列出。来源分五类：**DB**＝只读数据库，可在业务写处同事务产生；**托管**＝`chat.Manager` 的进程、会话日志与活动内存状态（`chat.py`）；**扫描**＝后台线程或 `state()` 自身驱动的文件 / 进程扫描；**派生**＝由其他字段与当前时间算出；**常量**。

### 6.1 顶层字段

| 字段 | 来源 | kind / 范围 | 生产者位置 | 状态 |
| --- | --- | --- | --- | --- |
| `todos`、`todo_count` | DB `todos` + 会话 / 组 / 任务标题 | `todo` / 工作区 | 5.1 | **已覆盖**（直接写入点全部覆盖；提出者改名、换组等跨实体派生随会话 / 组领域接入） |
| `projects`、`project` | DB `projects`、`project_themes`、`project_settings` | `project` / global；`workspace` / 工作区 | 5.2 | **已覆盖** |
| `ai` | DB `ai_settings`、`ai_task_settings` | `ai` / global | 5.2 | **已覆盖** |
| `decisions` | DB `decisions` + `decision_briefs` / `decision_presentations` / `decision_leader_scopes` / `goal_links` | `decision` / 工作区 | 5.2 | **已覆盖**（提问人改名属会话领域，未覆盖） |
| `goals` | DB `goals` 等（`goal_api.card`）；只含可见 leader 的任务 | `goal` / 工作区 | 5.2 | **已覆盖**（leader 关闭 / 移除 / 换组改变可见性属会话领域，未覆盖：在那之后对该任务的下一次写入才会记 `goal.removed`） |
| `domains` | DB `domains`、`domain_leaders`、`domain_boards` | `domains` / 工作区 | 5.2 | **已覆盖** |
| `channel` | DB（`unread`、`latest_id`、`digest_id`）+ 摘要管理器内存（`digesting`、`digest_error`） | `channel` 字段组 / 工作区 | 5.2 | **DB 部分已覆盖**；内存部分未覆盖（摘要管理器状态变化处，非 DB） |
| `native_approvals` | 内存：Codex app-server 待审批（`native_approvals.py`） | `approval`（`id`）/ 工作区 | 审批到达 / 答复处 | 未覆盖（托管） |
| `candidates` | 扫描：外部进程列表 + VS Code 会话扫描（`workers.py` 每 10 秒） | `candidates`（整值）/ 工作区 | `refresh_agents`、`_read_vscode` 结果变化时 | 未覆盖（扫描） |
| `agents_error`、`vscode_error` | 扫描（同上） | `scanner`（整值）/ global | 同上 | 未覆盖（扫描） |
| `limits`、`usage_scanned_at`、`usage_error` | DB `codex_rate_limits`（由用量扫描写入）+ Claude 额度文件 + 预算文件 + 扫描内存 | `limits` 字段组 / global | 后台用量扫描，6.3 | **已覆盖**（阶段 2） |
| `runtimes` | 内存：原生载体发现（`runtime_catalog`） | `runtimes`（整值）/ global | 发现刷新完成处 | 未覆盖（托管） |
| `notification` | `connections` 管理器（渠道状态、离开状态） | `notification`（整值）/ global | 渠道状态变化、`set_away` | 未覆盖（非 DB） |
| `sounds` | DB 音效设置 + 自定义音效文件（`preferences`、`revision`、`catalog`、`missing`）+ 会话日志（`events`） | `sound_settings` 字段组 / global | 5.2 | **设置部分已覆盖**；`events` 未覆盖（托管会话日志） |
| `git_assistant`、`git_branches` | DB `git_management` + Git 助手的 leader 卡片 + Git 进程读取 | `git`（整值）/ 工作区 | Git 设置写入、Git 读取刷新、助手卡片变化 | 未覆盖：值里包含托管的 leader 卡片（`busy`、`status` 等）与 Git 进程读取结果，在 DB 写入点算不出整值 |
| `attention` | 派生：会话、拍板、派工任务、**会话日志事件** | `attention`（`id`）/ 工作区 | 依赖托管生产者 | 未覆盖（托管） |
| `leaders`、`members` | **混合**，见 6.2 | `leader` / `member`（`session_id`）+ `leader_order`（顺序整值）/ 工作区 | 见 6.2 | 未覆盖（托管） |
| `now` | 服务端时钟，前端用于活动计时校准 | 不作为 kind；`ready` 与心跳改为带 `now`，客户端更新校准 | `web_service/events.py` | 未覆盖（接线时一起做） |
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

### 6.3 后台用量扫描（迁移阶段 2）

`state()` 不再调用 `scan_usage`，不写库、不拿写锁（测试 `test_the_snapshot_neither_scans_nor_writes_nor_waits_for_the_writer` 在持有写锁时读状态并追踪所有写语句）。扫描移到 `web_service/usage.py` 的后台线程 `crew-usage`（`start()` 启动，`close()` 等它结束当前一步）：

| 项 | 以前（`state()` 驱动） | 现在（后台线程） |
| --- | --- | --- |
| 派工回执（`codex_runs.jsonl`、`claude_runs.jsonl`） | 每个打开的页面每 3 秒轮询一次扫一次；没有页面打开就不扫 | 每 2 秒一步（`USAGE_TICK_SECONDS`），不管有没有页面 |
| 全量扫描（Claude 转录、Codex rollout 与额度、周用量记录、Crew 周估算、API 预算） | 距上次 ≥ 60 秒的那次读取顺带做 | 距上次 ≥ 60 秒的那一步做（`FULL_SCAN_SECONDS`），首步即全量 |
| 用量页 `/api/usage` | 读之前再扫一次（≥ 60 秒才全量） | 只读后台留下的结果；页面仍按 `usage_scanned_at` 变化重新加载 |
| 首屏 | 第一次 `state()` 同步做完整全量扫描才返回 | `start()` 同步按已存数据预填 Crew 估算与预算（不读转录），首步全量在后台进行；完成前 `usage_scanned_at` 为 `null` |

每一步：

1. **事务一**：`usage.scan`（全量或只读回执）单独提交，回执与 Codex 额度行先落库。这样即使下面的页面值算不出来（Claude 额度缓存格式变化、价格文件损坏），派工回执也照常更新。
2. **事务二**：全量时写周用量记录、算 Crew 估算与预算；合成快照里的 `limits`、`usage_scanned_at`、`usage_error`（`usage_value`，快照也用它），与上次记录的值不同才 `change_feed.updated(..., 'limits', ...)`，与估算写入同一事务。两步之间崩溃不会漏：重启后尚未记录过任何值（`usage_published` 为空），第一步必然记录。额度值随时间变化（到期失效）与预算保存（`web.py` 直接改内存）都会在下一步比对出来记录。
3. **出错**：只捕获扫描可能遇到的错误（`sqlite3.Error`、`OSError`、`ValueError`、`KeyError`、`TypeError`，与 VS Code 扫描同一口径），打印完整 traceback，把“用量扫描失败（2 秒后重试）：类型: 原因”写进 `usage_error` 并记录变更，下一步照常重试；整步成功才清除。记录错误本身也失败时（数据库不可用、页面值算不出来）同样打印原因、下一步重试；此时 `state()` 也会带着同一原因失败，与以前一致。
4. **锁**：重量扫描只持 `write_lock`（库内写者串行），`scan_lock` 只在更新内存字段时短暂持有，`state()` 读 Claude 标题时不再被分钟级全量扫描挡住。
5. **启动顺序**：首个全量扫描读多久就持写锁多久，所以用量线程等进程轮询线程跑完首轮（含对上次服务遗留的崩溃会话自动续回）再开始，与以前“首个页面轮询才触发扫描”时的先后一致；`start()` 创建这个事件（`self.agents_passed`）并作为必填参数传给 `_poll_agents(passed)` 与 `_poll_usage(after)`，等待期间每 0.5 秒检查一次停止信号。

`usage_error` 是新增的快照顶层字段（与 `agents_error`、`vscode_error` 同类）；前端本轮未接线，尚不显示。

## 7. 前端合并规则（`frontend/app-data.js`，`window.CrewAppData`）

- `create(ctx)`：`ctx` 由 `app.js` 提供 `origin`、`project`、`state`（getter，状态仍归外壳所有，测试对 `state=` 的赋值照常被看到）、`api`（晚绑定）、`fetch`、`EventSource`、`setTimeout/clearTimeout`、`onState(next, previous, meta)`、`onStatus(status, detail)`、`onError(error)`。返回 `queryPath`、`request`、`loadState(fresh)`、`stop`、`seq`、`status`。
- **加载**：`loadState()` = 取快照 → `onState(快照, 旧状态, {source:"snapshot"})` → 以快照 `seq` 建**一个**订阅。并发调用共用一次加载，`fresh` 在当前加载完成后再取一次（与原 `loadState` 合同相同）。失败抛给调用方（外壳显示“加载失败”或提示），同时数据层按退避自动再取，页面能自己恢复。
- **合并**：只应用 `seq > 当前序号` 的事件（重复送达自动忽略）；按 kind 登记的形状处理（4.3）：列表按实体 ID 替换 / 追加 / 删除，按登记的排序重排并重算派生字段；字段整值替换；字段组只合并登记的字段，字段不一致或对字段 / 字段组发删除都视为页面过旧（`UnknownChange`）；返回新对象，未改动的部分保持原引用，旧状态不被修改（外壳仍可比较新旧状态，例如音效）。
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

## 9. 前提冲突与主控裁决（2026-11-27，已结）

第 1 轮发现：快照里 leader / member 卡片的忙闲、活动计时、压缩小环、未读、`attention`、派工任务等来自 `chat.Manager` 的进程与会话日志内存状态，生产者只能加在 `chat.py`（当时禁改）；`state()` 自身驱动用量扫描；架构重审规定全部领域覆盖前不删轮询。所以没有删轮询、没有接线 `app.js`，并列出 A / B / C 三个方案。

主控裁决：

1. 选**方案 A**，不做“比对生产者”过渡代码。第 2 轮把 6.1 全部“DB”领域铺开（5.2），并完成迁移阶段 2（6.3）。
2. 会话托管状态（忙闲、活动计时、压缩小环、未读、`attention`、派工任务）的生产者等 `chat.py` 拆分（C1）合入后的下一轮再做；在那之前不删轮询、不接线 `app.js`。
3. `replace` 留在 `app.js` 渲染层，不抽出。
4. 不建 `change_log_state`，沿用 §2 的水位做法；理由已补进架构重审 §6.2 脚注。

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

### 10.3 前端接线（托管生产者接入后执行）

1. `index.html`：在 `<script src="/app.js" defer>` 之前加 `<script src="/app-data.js" defer></script>`。
2. `app.js`：`queryPath` 与 `api` 的通用部分改为 `CrewAppData.create(ctx)` 返回的 `queryPath` / `request`，外壳保留 `function api(path, data)`（发送前后的 `waitingSounds` 记账留在外壳）与 `function loadState(fresh)` 同名绑定，使测试注入 `api=`、`loadState` 照常可用；`ctx` 用 `get state() { return state; }`、`api: (...a) => api(...a)` 晚绑定。
3. `onState` 承接原 `loadState` 成功分支的全部渲染逻辑（`CrewSound.observe`、`nativeApprovals.sync`、`CrewActivity.sync`、路由守卫、`render`、`refreshUsage`、`notifySounds`），`meta.catchUp` 或 `source === "snapshot"` 时不播音效。
4. 删除 `setup()` 里的 `setInterval(loadState, 3000)`；首次加载失败页的“每 3 秒自动重试”文案改为“正在自动重连”，`test_app_state_ui.js` 对该文案的断言同步改写（行为等价改写，列入交付）。
5. 增加“已断开”显示（`onStatus`），按交互清单补验 `workbench.initial-retry`、`workbench.refresh-focus`、`workbench.ime-refresh`。
