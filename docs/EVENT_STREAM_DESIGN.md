# 增量事件流设计：快照 + 序号

> 日期：2026-11-27 · 分支 `claude/event-stream`（基于 `9a2cc6f`）· 依据：[架构重审](ARCHITECTURE_REVIEW.md) §6.2 / §7 阶段 3、[拆分边界](SPLIT_BOUNDARIES.md) §3.3、[交互清单](INTERACTION_INVENTORY.md)
> 状态（第 4 轮，2026-11-27）：并入集成分支后两个入口改由操作清单登记（`workspace.snapshot`、`workspace.events`，第 10 节）；新增页面类 kind `channel_message`，交流区投递徽章随 `leader_notices` 的投递列变化实时更新（4.3、5.3）。
>
> 第 3 轮：生产方式改为**只记失效、发送前现算**（方式 Y，第 5 节）；快照里每个会随时间变化的字段都有生产者（6.1）——数据库领域在业务事务里标记，卡片依赖的二十多张表由触发器标记（5.3），会话托管状态、扫描、连接与摘要状态经通知器标记（5.4）；`app.js` 已接线到 `CrewAppData`，**3 秒轮询已删除**（第 10 节）。

## 1. 目标与边界

前端现在每 3 秒拉一次 `/api/state` 全量快照。改为：先取一次快照（带序号），再按序号订阅增量；断线从上次序号续传，续不上就要求重拿快照；重连只恢复订阅，不重发任何写请求。桌面网页与将来的手机 PWA 共用同一协议。

不在本文范围：写请求的 `request_id` 去重与写响应带 `seq`（操作清单 R5，架构重审 §6.1）；统一用户输入请求（§6.3）；对话日志 `/api/chat/events`（保持现有格式与它自己的序号，客户端分别保存两种游标）。

## 2. 存储：`change_log`

```sql
CREATE TABLE IF NOT EXISTS change_log (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,      -- 提交时的 UTC 时间
    scope   TEXT NOT NULL,      -- 'global' | 'workspace:<工作区名>'
    type    TEXT NOT NULL,      -- '<kind>.invalidated'；用量扫描的 'limits.updated'（及第 3 轮前写入的旧行）
    entity  TEXT NOT NULL,      -- 实体在该 kind 内的 ID（文本）
    data    TEXT                -- 失效行为 NULL；'.updated' 行为整值 JSON
);
CREATE INDEX change_log_at (at); CREATE INDEX change_log_scope (scope, seq);
```

- **迁移**：新表走 `schema.sql` 的 `CREATE ... IF NOT EXISTS`（与现有新表同一做法），旧库原样保留，重复连接与并发首次连接都安全（测试 `MigrationTests`）。不往 `schema.sql` 加种子行，因为它在每次 `store.connect` 都执行，种子 INSERT 会让每次连接都变成一次写。
- **不复用 `events`**：`events` 是审计日志，待办、通知、恢复、摘要都在读它，不能套裁剪窗口（架构重审 §6.2）。
- **与架构重审的差异**：不建 `change_log_state` 水位表。裁剪只删最老的一段连续前缀，所以水位 = `min(seq) - 1`；表被裁空时 = 最后分配的序号（SQLite 为 AUTOINCREMENT 维护的 `sqlite_sequence`，裁剪不降低它）。保留规则按全局序号与时间计算，按范围分别记水位只会得到同一个值。

## 3. 序号、保留窗口与续传规则

| 项 | 规则 | 依据 / 测试 |
| --- | --- | --- |
| 序号的含义 | **一个序号表示“这个实体在此时变了”，不表示某个历史值**。读者收到的是读取时的当前值；同一实体在 41 改、42 删，从 40 续传只会收到 42 的删除。需要逐条送达、播放或审计的事实（完成、审批）不能靠这里，各自在原事务保存不可变内容（架构重审 §6.2） | `test_an_entity_changed_removed_and_recreated_while_away_arrives_once_as_it_is_now` |
| 序号 | 全局单调、永不重复。SQLite 同时只有一个写者，`BEGIN IMMEDIATE` 拿到写锁后才分配序号，提交顺序 = 序号顺序，读者不会先看到 7 后看到 6 | `ConcurrencyTests`：4 个连接各写 40 条，同时一个读者跟读，读到的序号恰好是 1..160 且每次只前进 |
| 回滚 | 业务写回滚时增量一起回滚，序号也不被占用（`sqlite_sequence` 在同一事务里） | `test_rolled_back_business_write_leaves_no_change_and_no_used_sequence` |
| 保留 | 最近 10,000 条或 24 小时（初值，阶段 3 用真实数据校准）。触发器也写行，所以由 `board.transaction` 提交前判断：本事务内序号跨过 256 的倍数就在同一事务里裁剪一次；按时间裁剪时取“早于截止时间的最大序号”为边界整段删除，时钟回拨也不会删出空洞 | `test_age_pruning_keeps_one_contiguous_prefix...`、`test_recording_prunes_on_its_own...` |
| 续传 | `after == 水位` 可续；`after < 水位` → `snapshot-required{reason:"pruned"}`；`after > 最新序号` → `"ahead"`；该订阅可见的积压超过 1000 条 → `"backlog"`；批内有该工作区的重置标记 → `"reset"`；某个值构造失败 → `"projection"`（见 4.2） | `ReadTests` |
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
data: {"reason":"pruned"} ← 发完即关闭连接；不带 id，客户端游标不动

event: snapshot-required
data: {"reason":"projection","kind":"goal","entity":"g-1","seq":57,"error":"KeyError: 'requirement_version'"}
```

- **读批次**（`change_feed.read`，首次追赶与在线循环共用）：在**一个读事务**里检查水位与积压、取出可见行、按 `(scope, kind, entity)` 只留最后一行、按保留行的序号升序逐个构造当前值；失效行构造后转成前端已认识的 `.updated` / `.removed`，带值的旧行直接解码。整批构造完才结束读事务、写网络。读取期间 `PRAGMA query_only=ON`：构造函数若试图写库，直接报错，而不是被读事务回滚悄悄吞掉。
- **构造失败**：读取层把构造函数抛出的任何异常包装成 `ProjectionFailed(kind, entity, seq)`（`raise … from`，原异常链保留）——这是全仓库唯一一处有意的宽捕获，作用是“不越过这个实体”。`events.py` 只做传输与错误：打印完整异常链（含 kind / entity / seq 与真实原因），发上面第二种不带 `id` 的 `snapshot-required` 并结束该订阅；**失败批次不发 `ready`、不推进游标**。前端按 `snapshot-required` 重拿快照；若数据本身坏了，快照也会带同一原因失败，前端显示错误并按 1/2/5/10 秒退避重试。用户已提交的写入不受影响（它们在写事务里只记了失效）。

- 游标取 `after` 与 `Last-Event-ID` 中较大者（与 `/api/chat/events` 相同），两者都没有或不是非负整数 → 400。
- 服务端循环：每 0.5 秒醒来按 `seq > 游标` 重新查表 → 发送 → 推进游标。**不依赖进程内通知**：`crew todo add` 等 CLI、hook、Codex 工具在别的进程写同一个库，进程内通知收不到；查询走主键索引，每连接每秒两次本机 SQLite 查询，代价可忽略。真实套接字冒烟：写入后约 0.2 秒到达订阅端。
- 远程设备：沿用 `_chat_events` 的 `stream_opened/stream_closed` 计数与 `remote_stop_event`；每次醒来经 `handler._request_active()` 检查设备是否已吊销，已吊销即结束。
- 服务停止（`service.stop`）立即结束循环。

### 4.3 事件内容与 kind 登记

事件按实体给出**快照中的完整当前值**，客户端按 `kind` 找到它在快照里的位置并按实体 ID 替换或删除；不用 JSON Patch。后端 `change_feed.KINDS`（kind → 范围）与前端 `CrewAppData.KINDS` 是同一份登记（`snapshot` 除外：它从不下发，服务端把它变成 `snapshot-required`），记录未登记的 kind 直接报错（后端 `ValueError`，前端 `UnknownChange` → 状态 `failed` 并提示刷新页面），不静默丢弃。schema 只增不改：新增 kind 必须先进前端登记，再由后端产生。测试 `test_the_frontend_registry_mirrors_the_backend_kinds` 逐项比对。四种形状：**列表**（按键替换 / 删除后按后端顺序重排）、**字段**（整值替换，没有删除）、**字段组**（只合并登记的几个字段，同一对象里其他来源的字段保持原值；字段不一致视为页面过旧）、**页面**（快照里没有的实体，数据层不合并，原样留在 `meta.changes` 里交给显示它的视图）：

| kind | 范围 | 形状 · 快照位置 | 实体 ID | 排序 / 字段 | 构造 |
| --- | --- | --- | --- | --- | --- |
| `todo` | 工作区 | 列表 `todos[]` | `id`（读取时转回整数） | 阻塞在前 → `created_at` → `id`；派生 `todo_count` | `todo.change_value` |
| `project` | global | 列表 `projects[]` | `name` | `name` | `snapshot_values.project`（已移除 → `removed`） |
| `workspace` | 工作区 | 字段 `project` | 工作区名 | — | `snapshot_values.workspace` |
| `ai` | global | 字段 `ai` | `ai` | — | `snapshot_values.ai` |
| `decision` | 工作区 | 列表 `decisions[]` | `id`（读取时转回整数） | `id`（只列 open） | `snapshot_values.decision` |
| `goal` | 工作区 | 列表 `goals[]` | `id` | `priority` 降序 → `created_at` → `id`（只列可见 leader 的任务） | `snapshot_values.goal` |
| `domains` | 工作区 | 字段 `domains` | 工作区名 | — | `snapshot_values.domains` |
| `channel` | 工作区 | 字段组 `channel` | 工作区名 | `unread`、`latest_id`、`digest_id` | `snapshot_values.channel` |
| `channel_message` | 工作区 | 页面 · 交流区（不在快照里） | 发言 `id`（读取时转回整数） | 一条发言的完整值，与 `/api/channel` 列出的相同（含 `mentions` / `notified` 的投递状态）；清理边界之前或已不存在 → `removed` | `snapshot_values.channel_message` → `channel.listed_message` |
| `sound_settings` | global | 字段组 `sounds` | `sounds` | `preferences`、`revision`、`catalog`、`missing` | `snapshot_values.sound_settings` |
| `limits` | global | 字段组（快照顶层） | `limits` | `limits`、`usage_scanned_at`、`usage_error` | 后台用量扫描**带值记录**（6.3），读取直接解码 |
| `conversations` | 工作区 | 字段组（快照顶层） | 工作区名 | `leaders`、`members`、`attention`、`native_approvals`、`git_assistant`、`git_branches`、`candidates` | 服务 `_conversation_cards`（快照同用） |
| `channel_digest` | 工作区 | 字段组 `channel` | 工作区名 | `digesting`、`digest_error` | 服务 `channel_digest.state` |
| `sound_events` | global | 字段组 `sounds` | `sound_events` | `events` | 服务 `event_sounds.events`，按**订阅者的工作区**构造（入口页列全部） |
| `runtimes` | global | 字段 `runtimes` | `runtimes` | — | 服务 `runtime_catalog.snapshot` |
| `notification` | global | 字段 `notification` | `notification` | — | 服务 `notification_status` |
| `scanner` | global | 字段组（快照顶层） | `scanner` | `agents_error`、`vscode_error` | 服务（进程扫描、VS Code 扫描内存） |
| `snapshot` | 工作区 | —（不下发） | 工作区名 | — | 读到即 `SnapshotRequired('reset')` |

数据库 kind 的构造函数登记在 `change_feed.BUILDERS`；托管 kind 由持有状态的服务构造（`web_service/snapshot.py` 的 `change_value(conn, kind, project, entity, viewer)`），`events.py` 以订阅者的工作区为 `viewer` 传给读取层。

## 5. 生产：只记失效，发送前现算（方式 Y）

主控裁决（2026-11-27，依据 astra 评审）：业务事务只记“哪个实体变了”（kind + 范围 + 实体），不在写锁里构造、序列化整值；值在发送前由读取层按当前状态现算（4.2）。所以页面值构造失败只结束那一个订阅并要求重拿快照，**不回滚用户的写入**——在第 2 轮“提交前现算”下，回答一个关联到坏任务卡的拍板会必然回滚（评审第 2 节）。

失效记录有三个来源，都写同一张 `change_log`：

| 来源 | 适用状态 | 机制 | 事务 |
| --- | --- | --- | --- |
| 业务写标记（5.1、5.2） | 数据库领域 | 写入点调 `change_feed.touch`；`board.transaction` 提交前 `flush` 每键写一行 `<kind>.invalidated` | 与业务写同一事务 |
| 触发器（5.3） | 卡片依赖的二十多张表、投递回执、结构变化 | SQLite `AFTER INSERT/UPDATE/DELETE` 触发器直接插入失效行 | 与写入语句同一事务（不论谁写：服务、CLI、hook、导入） |
| 通知器（5.4） | 不在数据库的托管状态 | 生产者调 `Notifier.mark / session / everywhere`；服务的记录线程合并后在一个短事务里 `touch` | 独立短事务，至多每秒一次，最后一次必记 |

- **严格**：不在 `board.transaction` 里标记（自动提交、裸 `BEGIN`）直接 `RuntimeError`；未登记 kind、范围与 kind 不符、对 `limits` 调 `touch` 直接 `ValueError`。生产代码对本地库的顶层写事务来自 `board.transaction`（`goals.transaction` 嵌套时用 SAVEPOINT，`usage._transaction` 也落到它）；唯一的裸事务（工作区导入）同样 `begin` / 提交前 `flush` / `end`。
- **同事务去重与派生**：同一事务多次标记同一实体只记一行。任务或拍板变化同时标记该工作区的 `conversations`（`change_feed.DERIVED`：leader 卡片的待拍板数、进行中任务与 `attention` 由它们派生）。
- **构造函数只读**：投影里不再有隐式写入。第 2 轮 `board.project` / `board.projects` 遇到缺主题的旧库会插入主题并再次标记，任务卡待验收分支与快照都会走到；现在补主题挪到 `store.connect` 的显式迁移 `_migrate_project_themes`，`init_project` 在自己的事务里分配主题并标记，读取永不写库（读取期间 `query_only` 兜底报错）。音效目录属于文件系统，发送的是读取时观察到的目录值。
- **导入**（`storage_v2/activation.py` 的 `activate_workspace`，裸 `BEGIN IMMEDIATE` + `defer_foreign_keys`）：除 `init_project` 标记的工作区列表与字段外，提交前写一条工作区级重置标记 `snapshot.invalidated`，已打开该工作区的页面重拿快照（修正第 2 轮“导入到已打开的空工作区收不到内容”的缺口）。批量插入的会话、组等行也会经触发器写卡片失效，被重置标记覆盖。导入后报告版本错乱致快照打不开是既有缺陷，按决策日志在 `claude/import-report-fix` 修根因；在那之前重取快照仍会报同一原因。
- **库外副作用**（追加会话日志 JSONL、启动进程、投递输入）不能随 SQLite 回滚，按架构重审 §6.2 “持久意图 + 发件箱”另做，不在本文范围。上传 / 删除自定义音效只改文件，写文件后单独开一个事务标记 `sound_settings`。
- **迁移不是生产者**：`store.py` 启动迁移（含补主题）与 `home_migration.py` 换库不调用 `touch`；但换库重定位路径的 UPDATE 会经卡片触发器多写几条 `conversations.invalidated`，读者重算即可。换库到序号更小的库时，持有旧序号的客户端得到 `ahead` → 重拿快照。

### 5.1 待办试点（已实现）

待办写入点调 `todo.touch`。

| 业务写 | 位置 | 读到的事件 |
| --- | --- | --- |
| 登记（含 CLI / Codex 工具 / 目标转待办经 `add_native`） | `todo.add` | `todo.updated`；重复登记（`duplicate`）不产生事件 |
| 用户完成 / 取消、AI 撤回 | `todo._change`（`resolve` / `withdraw`） | `todo.removed`（快照只列 open） |
| 撤销 | `todo.undo` | 恢复为 open → `todo.updated`；否则 `todo.removed` |
| 关联到任务执行 | `goal_links.bind`（kind=todo） | `todo.updated`（`goal_ids`、`goal_links` 变化） |
| 标记已投递 / 归档原轮次 | `goal_links._mark_delivered`、`goal_links.deliver` 失效分支 | 按当前状态 updated / removed |
| 工作区清理批量取消 | `maintenance.Manager._archive` | 每条原 open 待办一个 `todo.removed` |

待办里嵌着的提出者名、所属组与 leader 随会话 / 组的结构变化更新：这些变化由触发器写工作区重置（5.3），页面重拿快照。

### 5.2 第 2 轮铺开的领域（全部直接写入点）

| kind | 业务写（位置） | 联动 |
| --- | --- | --- |
| `project` + `workspace` | 创建 / 重新打开（`board.init_project`，含分配主题）、改主题（`board.set_project_theme`）、改显示名或目录（`workspace_settings.apply_update`）；移除（`workspace_settings.remove`）只标记 `project`（读到 `removed`） | `board.touch_project` 同时标记两者 |
| `ai` | `settings.save`（含 `ai_settings` 与 `ai_task_settings` 的增删） | — |
| `decision` | 提问 `board.ask`、答复 `board.answer`（含 `trust_mode.auto_answer`、远程渠道作答）、撤回 `board.withdraw`、记录归属 leader `trust_mode.record_decision_scope`、改写 `decision_briefs.revise`、保存简报 `decision_briefs._save`、讨论 / 给选项 `decision_flow.discuss/ready`、关联执行 `goal_links.bind`、工作区清理 `maintenance.Manager._archive` | `snapshot_values.touch_decision` 同时标记关联任务；派生 `conversations` |
| `goal` | `goals.py` 全部写函数（经 `goals._touch` 或 `goals.touch`）、工作清单 `goal_work.*`、决策模式 `goal_mode.*`、上下文回执改执行版本 `goal_context.mark_applied` | 派生 `conversations` |
| `domains` | `domains.apply` / `apply_ops`（思维导图转架构图、架构重整在同一事务里调用，`apply_ops` 内经 `domains.bump` 标记）、回滚 `domains.rollback`、只改任务与编制的重整 `restructure.apply`（都经 `domains.bump`）、永久删除 leader 时 `domains.release_leader`；版本历史随架构图版本变化重读，不另设 kind | — |
| `channel` | 发言 `channel.post`、自动指引 `channel.settle`、用户已读 `channel._mark_read`、要点卡 `channel_digest.Manager._run`、工作区清理边界 `maintenance.Manager._archive` | — |
| `sound_settings` | `event_sounds.save`、上传 `add_custom`、删除 `delete_custom` | — |

### 5.3 卡片触发器（`change_triggers.py`）

leader / member 卡片读二十多张表，写入点散在服务、CLI、hook 与导入里，逐个函数标记必然漏。每张表一组 `AFTER INSERT/UPDATE/DELETE` 触发器，在写入语句的同一事务里插入该工作区的 `conversations.invalidated`；找不到工作区的行（个人会话、未登记会话）不插入。

| 定位工作区的方式 | 表 |
| --- | --- |
| 行上的 `project` | `sessions`、`groups`、`runtime_switches`、`leader_trust_settings`、`leader_notices`、`reports`、`leases`、`claims`、`goals`、`git_management`、`git_merge_requests`、`leader_pins`、`leader_order`、`branch_claims` |
| 经 `sessions` | `session_controls`、`session_appearance`、`session_options`、`session_runtime_bindings`、`session_runtimes`、`read_cursors`、`session_cards`、`leader_member_settings`（`leader_session_id`） |
| 经任务 / 执行 / 拍板 | `goal_executions`、`goal_execution_bindings`、`decision_leader_scopes` |
| 所有工作区 | `attention_resolutions`（条目 ID 不含工作区；用户手动关闭，极少） |

- **结构变化写重置**：会话改名、换组、换角色、换工作区、关闭或重开（`sessions` 的条件更新与删除），归档 / 移除 / 改名覆盖（`session_controls`），组改名、关闭、换 leader 与删除（`groups`），会改动嵌在其他 kind 里的值（待办提出者、拍板提问人、哪些 leader 的任务在页面上）。这些触发器写 `snapshot.invalidated`，页面重拿快照。这补上了第 2 轮列为“会话 / 组领域，未覆盖”的几项。
- **交流区投递徽章**：`leader_notices` 的 `sent_at`、`delivered_via`、`waiting` 任一变化时，触发器 `feed_channel_leader_notices_update` 给挂在这条通知上的每条发言（`channel_mentions.notice_id`）写 `channel_message.invalidated`。这些变化来自服务投递批次的 `mark_notice_sent` 与 `note_waiting`（`chat_delivery.deliver_leader_notices`，自动提交的单句，不在 `board.transaction` 里，无法 `touch`）和外部会话提示词钩子的 `claim_for_hook`；只改 `batch_id` 的认领不改变徽章，不写。新增索引 `channel_mentions_notice` 供触发器查找。挂着点名的通知受外键保护不会被删，所以不设删除触发器。回复使徽章变成“已回复”在 `channel.post`（`reply_to`）与 `channel.settle`（私聊指针）里直接 `touch('channel_message', …)`。
- **全局表**：投递回执 `notification_messages` 的写入写全局 `notification.invalidated`（快照的连接状态列出最近回执）；轮次交付 `goal_round_finishes` 的写入写全局 `sound_events.invalidated`（交付提示音）。
- **安装**：`store.connect` 比对 `sqlite_master` 里 `feed_` 触发器的名字与语句，与当前版本不同才在一个写事务里整组重装；相同时只多一次查询。

### 5.4 托管状态的通知器（`change_feed.Notifier`）

不在数据库的状态由持有它的对象在变化处标记；`chat.Manager.changes` 是服务唯一的通知器，摘要管理器构造时拿到它，连接与收件箱经 `service.chat.changes` 使用。服务的记录线程 `crew-changes`（`web_service/workers.py` `_poll_changes`）等通知唤醒，取走全部标记，在一个 `board.transaction` 里 `touch`，然后至少等 `CHANGE_SECONDS`（1 秒）再取下一批：每实体每秒至多一行，最后一次标记必定记录；数据库忙超时（`sqlite3.Error`）时打印原因并把标记放回，下一轮再记。`close()` 在会话与摘要都停下后唤醒它做最后一次记录再结束。

- `session(key)`：会话日志的键（总对话的 `session_id` 或任务执行 ID）。记录时解析到工作区（`sessions.project`，或执行所属任务的工作区）并标记 `conversations`；同时标记全局 `sound_events`。解析不到工作区的键（个人助手）只标记 `sound_events`。
- `everywhere(kind)`：每个工作区的同一 kind（进程扫描、VS Code 扫描影响所有工作区的卡片与候选）。
- `mark(kind, project, entity)`：其他托管 kind。

## 6. 快照逐项对应：来源与生产者

按 `web_service/snapshot.py` 的 `state()` 与 `_project_conversations()` 逐项列出。来源分五类：**DB**＝只读数据库，可在业务写处同事务产生；**托管**＝`chat.Manager` 的进程、会话日志与活动内存状态（`chat.py`）；**扫描**＝后台线程或 `state()` 自身驱动的文件 / 进程扫描；**派生**＝由其他字段与当前时间算出；**常量**。

### 6.1 顶层字段

| 字段 | 来源 | kind | 生产者 | 状态 |
| --- | --- | --- | --- | --- |
| `todos`、`todo_count` | DB | `todo` | 5.1；提出者等派生值随结构变化重置（5.3） | **已覆盖** |
| `projects`、`project` | DB | `project`、`workspace` | 5.2 | **已覆盖** |
| `ai` | DB | `ai` | 5.2 | **已覆盖** |
| `decisions` | DB | `decision` | 5.2；提问人改名随结构变化重置 | **已覆盖** |
| `goals` | DB | `goal` | 5.2；leader 关闭 / 移除 / 换组随结构变化重置 | **已覆盖** |
| `domains` | DB | `domains` | 5.2 | **已覆盖** |
| `channel` 的 `unread`、`latest_id`、`digest_id` | DB | `channel` | 5.2 | **已覆盖** |
| `channel` 的 `digesting`、`digest_error` | 摘要管理器内存 | `channel_digest` | `channel_digest.Manager.start`（开始）、`_run` 两个结束分支 | **已覆盖** |
| `leaders`、`members`、`attention`、`native_approvals`、`git_assistant`、`git_branches`、`candidates` | 混合，见 6.2 | `conversations` | 6.2 | **已覆盖**（仓库外部变化与 24 小时窗口见 6.2） |
| `limits`、`usage_scanned_at`、`usage_error` | 扫描 | `limits` | 后台用量扫描（6.3） | **已覆盖** |
| `runtimes` | 内存：执行工具目录 | `runtimes` | 目录刷新处（`/api/runtimes`、切换到 Codex 前的强制刷新） | **已覆盖**（Claude CLI 是否安装由 `which` 当场判断，安装 / 卸载本身没有生产者，下一次刷新时更新） |
| `notification` | 收件箱与连接管理器内存 + DB 回执 | `notification` | `Inbox.__setattr__`（`status()` 展示的字段被赋成不同值）、`save_config`、`connections.set_away / save_preferences / _replace`；回执表触发器 | **已覆盖** |
| `sounds` 的设置部分 | DB + 文件 | `sound_settings` | 5.2 | **已覆盖** |
| `sounds.events` | 会话日志 + DB 交付记录 | `sound_events` | 任何会话日志变化（5.4）；交付记录 `goal_round_finishes` 的触发器（5.3） | **已覆盖** |
| `agents_error`、`vscode_error` | 扫描内存 | `scanner` | `refresh_agents`、`_refresh_vscode`、`_read_vscode`：错误值变化时 | **已覆盖** |
| `now` | 服务端时钟 | 不作为 kind | 只随快照下发；前端 `CrewActivity` 用它校准一次时钟偏差，之后本地计时 | 前端本地计时 |
| `themes`、`models`、`efforts` | 常量 | 无 | — | 不需要 |

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

事件给出整组卡片（`conversations` 字段组），不拆成单卡：卡片之间有交叉派生（`member_ids`、`pending`、排序、Git 助手卡、`attention`），整组重算与快照同源。卡片的生产者：

| 卡片状态 | 生产者（变化处） |
| --- | --- |
| DB 部分（名字、组、外观、模型、授权、已读游标、通知、进展、租约、认领、执行记录、Git 管理、置顶、排序……） | 5.3 触发器 |
| 待拍板数、进行中任务、`attention` 的拍板部分 | `goal` / `decision` 标记派生（5 节） |
| `status`、`busy`、`running`、`where` 的托管部分；`activity`（阶段、计时起点、token 数）；`applied`；`context`（压缩小环）；`last_seq`、`last_reply_id`、`unread`、`web_chat`、`general_has_output`；`attention` 的出错部分；`codex` 里的子代理任务 | `Journal`：`emit`（每个日志事件，含状态、子代理、原生审批、压缩、出错）、`set_status`（含状态不变时进度重置）、`consume`（流式记录推进阶段与 token）、`reset_runtime`、`remember_context`；`Journal.changed()` 由 `_control_response`（Claude `get_settings`）、Codex `applied` 两处与 Codex 进度更新调用；进程登记（`chat.py` 两处、`goal_runtime.py` 两处）、维护占用 / 释放（`maintenance.py`、`chat_maintenance.py`）调 `changes.session` |
| `where`、`busy`、`running` 的外部进程部分；`candidates` | `refresh_agents` 的结果与上次不同；VS Code 会话列表变化（`_read_vscode`）→ `everywhere('conversations')` |
| `codex`、`dispatches`（派工回执、转录里观察到的设置） | 用量扫描事务一有新增或更新的行时，同一事务标记所有工作区的 `conversations` |
| `title`（Claude 转录标题） | 每次重算卡片时读取（有缓存）；只在转录变化、卡片没有其他变化时滞后 |
| `git_branches` | 每次重算卡片时经 `git_overview.read` 读取（10 秒内复用缓存）。仓库在 Crew 之外被改（终端里提交）没有生产者，随下一次卡片失效更新；Git 页面打开时自己强制重读 |
| “最近 24 小时”派工窗口 | 纯时间推移，没有生产者；任务过窗后在下一次卡片失效时移出。运行中任务的“N 分钟”由前端本地计时重绘（第 7 节） |

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
- **状态**：`connecting`、`live`、`disconnected`、`resyncing`、`failed`（页面版本过旧）、`idle`。外壳 `showConnection` 在 `disconnected` 显示“已断开，正在重连…”、`resyncing` 显示“正在重新同步…”、`failed` 显示“页面版本过旧，请刷新页面”（`#connection-status`，警示色胶囊，沿用 `--warn` 系状态色），其余状态隐藏。
- **前端本地计时**：活动计时与 token 滚动本来就在 `CrewActivity` 里按本地时钟插值（快照的 `now` 只校准一次偏差）。“N 分钟前”“已运行 N 分钟”这类只随时间变化的文字由外壳每 30 秒本地重绘一次（`setInterval(render, 30000)`，不发请求）。
- 外壳渲染抛出的错误不被数据层吞掉（只捕获 `UnknownChange`）。
- **交流区发言（页面类 `channel_message`）**：`applyState` 在渲染后把本批 `channel_message.*` 交给 `CrewChannel.changed`：已加载的同 id 发言整条替换（`removed` 则移除）并重绘，没加载的忽略（之后读到的就是当前值）；读页请求进行中收到变化则读完再读一次最新页，避免读到的旧徽章盖掉新值。快照（首次、写后、重同步）不带发言，`changed(null)` 让交流区重读最新页。这取代了消息可靠性分支里“leader 的 `notices_pending`/`busy`/`where` 变化就重读最新页”的近似做法（已删除）。侧栏 leader 行在 `notices_pending > 0` 时显示“N 条通知排队中”，卡片字段 `channel_queued_id`（最早一条仍在排队的点名所在发言）不为空时可点，打开交流区并定位到那条发言。

## 8. 与操作清单 / MCP、手机端的关系

- **操作清单（R5）**：清单负责命令面（写入口、`request_id` 去重、权限）；事件流负责状态面。写操作执行器拥有连接与事务，处理函数在该连接上做业务写，执行器在同一事务里写回执与 `change_log`，写响应带最后一个 `seq`，客户端据此判断“我的修改是否已在当前状态里”，届时可去掉写后 `loadState(true)` 的整份快照重取。`change_feed.updated / removed` 不关心调用方是 HTTP、CLI 还是 MCP，只要求在同一事务里。
- **MCP / AI 会话**：AI 不订阅工作区事件流；它通过 MCP 工具读写。MCP 写入经同一执行器，因此 AI 的操作会实时出现在所有打开的页面上。
- **手机端**：PWA 与桌面共用 `frontend/` 与本协议。弱网下断线续传只补差量；补不上才重拿快照。远程设备吊销时长连接立即结束。手机息屏 / 标签页隐藏时是否断开订阅（对话页 `chat.js` 是隐藏即断、可见再补读）待接线时按交互清单 `chat.read-visibility` 统一。
- **其他仍在轮询的入口**（不在本次范围，列出供后续统一）：`remote-access-ui.js` 每 5 秒、桌面 `tabs.cjs` 读 `/api/workspaces/activity`、`assistant.js` 的 `/api/assistant/state`。它们可在对应领域接入事件流后改为订阅 `global` 范围。

## 9. 前提冲突与主控裁决（2026-11-27，已结）

第 1 轮发现：快照里 leader / member 卡片的忙闲、活动计时、压缩小环、未读、`attention`、派工任务等来自 `chat.Manager` 的进程与会话日志内存状态；`state()` 自身驱动用量扫描；架构重审规定全部领域覆盖前不删轮询。主控裁决：

1. 选**方案 A**，不做“比对生产者”过渡代码。第 2 轮把 6.1 全部“DB”领域铺开（5.2），并完成迁移阶段 2（6.3）。
2. 会话托管状态的生产者等 `chat.py` 拆分合入后做（第 3 轮已做，5.4、6.2）。
3. `replace` 留在 `app.js` 渲染层，不抽出。
4. 不建 `change_log_state`，沿用 §2 的水位做法；理由已补进架构重审 §6.2 脚注。

第 3 轮裁决：

5. 生产方式改为 **Y：业务事务只记失效，发送前在读批次里现算**（第 5 节、4.2）；构造失败只结束该订阅并要求重拿快照，不越过游标，不回滚业务写入。共享投影器等有扇出实测再议。
6. 会话托管状态在真正变化的地方产生失效标记，托管状态不在 SQLite 事务里的直接追加失效记录（5.4）；全部覆盖后接线前端、删除 3 秒轮询（第 10 节）。
7. 无法在变化处产生的纯时间推移显示由前端本地计时，不做后台轮询（第 7 节、6.2）。

## 10. 接线（第 3 轮完成）

| 位置 | 内容 |
| --- | --- |
| 操作清单与传输层 | `workspace.snapshot`（GET `/api/snapshot` → `web_service.events.serve_snapshot`）是只读操作，经 `ops.executor` 分派。`/api/events` 是持有打开着的 HTTP 响应的 SSE 流，按操作清单设计 §8.14 归传输层，登记在 `web_api/routes.py` 的 `TRANSPORT_GET`（与 `/api/chat/events` 同类），不是操作，也就不会被 `POST /api/op` 或 CLI 误调（主会话 2026-11-27 裁决）。`after` 缺失或非法抛 `ValueError`（400），工作区不存在抛 `BoardError`。 |
| `remote_routes.py` 的 `GET_PATHS` | 两个路径与 `/api/state`、`/api/chat/events` 同权限；远程长连接沿用 `stream_opened/stream_closed` 与 `remote_stop_event` |
| `web.py` 的 `STATIC` | `/app-data.js`；`test_assistant_http.py::test_new_page_assets_are_served_by_the_actual_backend` 同步登记 |
| `index.html` | `app-data.js` 在 `app.js` 之前加载；新增 `#connection-status` |
| `app.js` | `api` / `loadState` 保留同名绑定（测试照常注入）；`loadState` 接到 `CrewAppData.create(ctx).loadState`，失败在外壳显示（首次失败页文案改为“正在自动重连，也可以立即重试”）；`applyState` 承接原成功分支的全部渲染（快照与追赶批次不播音效）；删除 `setInterval(loadState, 3000)` 与只为轮询存在的 `pollBusy` / `statePromise` |

- `/api/state` 仍保留：桌面 `web.py` 自用与测试调用，是否删除由迁移阶段 5 统一决定。
- 其他仍在轮询的入口（不在本次范围）：`remote-access-ui.js` 每 5 秒、桌面 `tabs.cjs` 读 `/api/workspaces/activity`、`assistant.js` 的 `/api/assistant/state`。
