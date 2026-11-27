# 助手作战设计：开局起盘与后期重整

> 日期：2026-11-27 · 分支 `claude/campaign-core`（基于集成分支 `191d4e2`）
> 依据：[新手引导与 Teamwork 设计](ONBOARDING_AND_TEAMWORK_DESIGN.md) §4.2–4.4、[总计划](REBUILD_PLAN_2026-11-27.md) 阶段四第 2 条、[操作清单设计](OPERATIONS_REGISTRY_DESIGN.md) §2 / §5.6、[增量事件流](EVENT_STREAM_DESIGN.md) §5、[想法思维导图](MINDMAP_DESIGN.md) §3
> 本文分两层：第 1–2 节是完整流程与分层；第 3–7 节是**本任务已实现的底层结构能力**；第 8 节是下一步（助手流程）怎么调用它们。

---

## 1. 完整流程

开局与重整是同一功能的两种用法，都是“探底 → 方案 → 确认 → 执行 → 交棒”。助手只动结构（架构图、Leader 编制、任务作废 / 改派），不改代码。

| 阶段 | 开局起盘 | 后期重整 | 用到的操作 |
| --- | --- | --- | --- |
| 探底 | 已有代码：派只读调研摸清目录与技术栈；空白目录：2–3 个问题对齐目标；有导图时直接以导图为草稿 | 读现有架构图、版本历史、Leader 编制、进行中的任务与近期交付 | `domains.get`、`domains.versions.list`、`mindmap.get`、`goal.list`、`workspace.state` |
| 方案 | 项目思路图 + 首批任务 + 各领域推荐载体 / 模型 | 改前 / 改后对照：领域变化、Leader 合并 / 拆分 / 退役、任务作废 / 改派、预计启动的 Leader | 开局：`mindmap.preview`；重整：`domains.restructure.preview`（只读，可反复调） |
| 确认 | 用户看预览、改交接稿、去掉不要的项，确认一次；选“建完立刻开工”或“先只建结构” | 同左 | 高风险操作走用户确认（见第 2 节） |
| 执行 | 一个事务落地结构，生成一个架构图版本；之后按确认结果启动新 Leader、派首批任务 | 一个事务落地结构 + 归档 + 任务改派 / 作废 + 交接稿投递，生成一个架构图版本 | `mindmap.convert` / `domains.restructure.apply`，然后 `leader.create`、`goal.create` |
| 交棒 | 盯首轮里程碑与跨领域联调，交付后作战室归档 | 同左；结构推翻不对时一键回滚 | `domains.version.get`、`domains.rollback` |

## 2. 分层：哪一层管什么

| 层 | 职责 | 本任务 |
| --- | --- | --- |
| 结构操作（后端，操作清单） | 预览只读、执行一个事务、版本快照、回滚、交接稿投递；规则写在后端状态机里，不靠提示词 | **已实现**（第 3–6 节） |
| 用户确认（R5 第二阶段） | AI 调 `risk=high` 的操作时不直接执行，生成绑定“发起者 + 操作 + 参数哈希 + 目标版本”的确认请求（OPERATIONS_REGISTRY_DESIGN §5.6）；用户在界面确认后执行 | 待接线：确认卡里嵌本任务的重整预览面板 |
| 费用确认（助手流程） | 新建 / 启动 Leader 数量、各自载体与模型、首批任务范围，确认一次；“建完立刻开工 / 先只建结构” | 不在本任务；结构执行本身**从不启动模型**（第 5.4 节），所以费用确认只挂在“启动新 Leader / 派首批任务”这一步 |
| 预算上限（作战室） | 首轮花到设定 token 量先暂停等用户确认 | 不在本任务；属于作战室主题，按作战室记账 |

费用确认放在助手流程层而不是结构层，因为结构执行唯一可能产生用量的是交接稿投递唤醒接手 Leader：它由每份交接稿上的 `wake` 显式决定（界面默认不勾），走现有 leader 通知的唤醒规则；其余结构动作都不碰模型。

## 3. 架构图版本

### 3.1 数据（`schema.sql` 新表，`CREATE TABLE IF NOT EXISTS`，旧库首次连接即建表，不改旧表）

`domain_board_versions(project, number, board_version, source, note, state, detail, restored_from, created_at)`，主键 `(project, number)`。

- `number` 是工作区内从 1 起的版本号；`board_version` 是写入时的 `domain_boards.version`。
- `source`：`baseline`（启用版本历史前的样子）、`edit`（在架构图上手动改）、`mindmap`（思维导图转换）、`restructure`（重整方案，含 Leader 合并 / 退役）、`rollback`（回滚）。
- `state` 是**改后**的结构快照 JSON：`direction`、`parts[{id,parent_id,name,x,y,collapsed,created_at}]`、`leaders[{session_id,domain_id,lead,name}]`（只记放进 part 的 leader，名字留存，便于会话删除后仍能看懂历史）。
- `detail` 是重整 / 回滚的说明 JSON（改动条目、归档的人、任务处理、交接稿去向、待启动 Leader），手动编辑为空。

### 3.2 什么时候记版本

- `domains.apply_ops` 的一批改动里只要有结构改动（`create` / `rename` / `parent` / `delete` / `assign` / `lead`）就在同一事务里记一个版本；只有摆位、折叠、方向（`move` / `collapse` / `direction`）的批次不记——它们是布局，不是结构，回滚也不动它们。
- 导图转换、重整执行、回滚都走同一个记录点，`source` 与说明由调用方给出（`apply_ops` 的 `source` / `note` 是必填关键字参数，没有默认值）。
- 工作区第一次记版本时，先把**改动前**的样子记成 `baseline`（空白架构图也记），这样开局作战整个可以回滚到空白。不扫描旧库补记，不改旧数据；同一写事务内判断“是否已有版本”，并发安全。
- 永久删除 leader（`release_leader`）不记版本：它不是批量结构修改；历史版本里这位 leader 的位置在回滚预览里列为“已删除，无法恢复”。

### 3.3 差异

`domain_versions.diff(a, b)` 只比结构：part 新建 / 删除 / 改名 / 改挂，leader 放入 / 移出 / 换 part / 负责人变化；坐标、折叠、方向不算差异。版本详情返回“当前 → 该版本”的差异，也就是回滚会改什么。

### 3.4 回滚

- 只恢复结构：该版本有、现在没有的 part 用原 id 原名重建（导图转换链接随之重新有效），现在有、该版本没有的 part 删除；仍存在的 part 保留当前坐标与折叠；方向不变。
- leader 位置按该版本恢复；当时在、现在已永久删除的 leader 跳过并在预览里列出；当时在、现在已归档的 leader 恢复位置但仍是归档状态（与架构图“归档 leader 保留位置”一致）。
- **失去归属**：现在放在某个 part、回滚后不在任何 part 的 leader，在版本详情的 `rollback.unplaced` 里列出，回滚确认框里醒目显示。
- 不删除、不归档、不恢复任何会话；回滚本身是一个新版本（`source=rollback`，`restored_from` 指向目标版本）。
- 提交带 `board_version`；预览后架构图又被改动返回 409，界面要求重看差异。

## 4. 重整方案

### 4.1 变更条目（按顺序执行，前面新建的 part 可被后面引用）

| `type` | 字段 | 规则 |
| --- | --- | --- |
| `part.create` | `ref`、`name`、`parent` | `ref` 同时是新 part 的 id（6–64 位字母数字 `_-`）；`parent` 是现有 part、前面新建的 `ref` 或 `null` |
| `part.rename` | `part`、`name` | 名称规则同架构图（1–40 字） |
| `part.move` | `part`、`parent` | 不能挂到自己或下级下面 |
| `part.merge` | `part`、`into` | 下级 part 改挂到 `into`，leader 移进 `into`（负责人标记取消），原 part 删除；不能并入自己的下级 |
| `part.delete` | `part` | 同架构图删除：下级与 leader 上移一层 |
| `leader.assign` | `leader`、`part` | `part=null` 为移出到未归属 |
| `leader.lead` | `leader`、`lead` | 同架构图负责人规则 |
| `leader.merge` | `leader`、`into` | 见 4.3 |
| `leader.retire` | `leader`、`successor` | 见 4.3；`successor=null` 时该 leader 未结束的任务必须在方案里逐个作废或改派 |
| `leader.split` | `leader`、`ref`、`name`、`runtime`、`model`、`effort`、`part` | 拆出一位新 leader 负责 `part`：只进预览与执行结果的“待启动”清单，见 5.4 |
| `goal.cancel` | `goal` | 作废任务（走 `goals.request_cancel` / `finalize_cancel`） |
| `goal.transfer` | `goal`、`to` | 改派给另一位在岗 leader（走 `goals.request_transfer` / `finalize_transfer`） |

### 4.2 预览（`domains.restructure.preview`，只读）

把变更在内存里的结构副本上逐条模拟（与 `domains._Edit` 同一套规则），返回：

- `before` / `after` / `diff`：与导图转换预览同一风格的改前 / 改后两栏对照；
- `changes[]`：每条的可读说明与错误；`errors[]` 非空时不能执行；
- `leaders[]`：受影响的 leader 与具体影响（换 part、移出、负责人变化、并入、退役）；
- `goals[]`：作废 / 改派的任务，标明是方案里写的还是随合并 / 退役自动改派；
- `archived[]`：将归档的会话（退役 leader 连同其组员，原会话保留）；
- `handoffs[]`：每位被合并 / 退役（有接手人）/ 拆分的 leader 的交接稿草稿；
- `to_start[]`：预计要启动的新 Leader（载体、模型、放进哪个 part）；
- `board_version` 与 `revision`（相关 leader 与任务当前状态的摘要），执行时两者都要一致，否则 409 重新预览。

### 4.3 Leader 合并与退役

- **合并 A → B**：A 未结束的任务改派给 B（方案里另有写法的以方案为准），A 的交接稿投给 B；A 离开架构图，若 A 是某 part 负责人且 B 在同一 part、该 part 没有别的负责人，B 接任负责人；A 的协作组收起，A 与组员归档保留。
- **退役 A（接手人 C）**：同上，但 C 不接任负责人。**退役 A（无接手人）**：没有交接稿；A 的未结束任务必须在方案里作废或改派。
- 前提（预览里作为错误列出）：A 在岗未归档、这一轮不在工作、没有正在运行 / 暂停中的任务（先暂停）、不是 Git 助手；接手人在岗、有协作组、不在本方案里退役。
- 交接稿复用 `runtime_switch.handoff`（白板事实、待办、未决问题、文档、最近聊天片段，16000 字上限），开头用新的可编辑提示词 `leader_handoff`；界面可编辑，确认后作为 **leader 通知**（`board._queue_leader_notice`，来源 `web`）投给接手人。`wake=true` 时按现有规则唤醒接手人开一轮，`false` 时等它下一次对话带上。除此之外不启动任何模型。

### 4.4 执行（`domains.restructure.apply`，一个事务）

顺序：重新模拟并核对 `board_version` / `revision` → 结构改动翻译成 `domains` 的 op 一次写入（`source=restructure`，记版本）→ 任务改派 / 作废（本事务内 request + finalize；仍有执行在跑的作废在提交后由目标控制器停下再收尾）→ 收起退役 leader 的协作组并归档 → 投递交接稿 → 提交。提交后：停掉被归档会话的托管进程（`stop_archived`，与“收起 leader”同一条路）、目标控制器同步一次。

单独的 `leader.merge` / `leader.retire` 是只含一条变更的重整方案，预览用 `domains.restructure.preview`。

### 4.5 不在一个事务里的东西

新 Leader 要启动模型进程，不能放进数据库事务，也不该在结构确认里顺带花钱。所以 `leader.split` 在执行时只建好它要负责的 part（若是新建的），返回 `to_start[{ref,name,runtime,model,effort,part_id,brief}]`，`brief` 是用户审阅过的交接稿；下一步助手流程在费用确认后用现有 `leader.create`（`task=brief`）启动它，再用 `domains.apply` 的 `assign` 放进 part。

## 5. 操作清单新增条目（`plugin/crew/ops/domains/structure.py`）

按操作清单设计（`docs/OPERATIONS_REGISTRY_DESIGN.md`）§2、§4、§5.6 登记；只经 `/api/op` 调用（界面用 `POST /api/op/<操作名>`），没有单独的 HTTP 地址。

| 操作 | 读写 / 风险 | 范围 | 准入 | 生成 CLI |
| --- | --- | --- | --- | --- |
| `domains.versions.list` | 读 | H B D A | — | `crew domains versions list` |
| `domains.version.get` | 读 | H B D A | — | `crew domains version get` |
| `domains.rollback` | 写 / **high** | H B D A | — | `crew domains rollback` |
| `domains.restructure.preview` | 读 | H B D A | — | `crew domains restructure preview` |
| `domains.restructure.apply` | 写 / **high** | H B D A | `model_work` | `crew domains restructure apply` |
| `leader.merge` | 写 / **high** | H B D A | `model_work` | `crew leader merge` |
| `leader.retire` | 写 / **high** | H B D A | `model_work` | `crew leader retire` |

H = 本机界面，B / D = 远程浏览器 / 远程桌面（与 `domains.apply` 相同），A = 全局助手（经 `assistant_cover` 以用户身份运行同一处理函数，参数带 `project`）。

- **leader、任务执行、Git 助手都不开放**：重整方案会移动其他 leader、收起并归档他们的协作组、改派或作废他们的任务，超出 leader 的“本组 / 自己的目标”范围；leader 之间是平级，架构图本身（`domains.get` / `domains.apply`）也不对 leader 开放。leader 认为结构该变时在交流区或作战室提出，由用户或全局助手预览、确认后执行。
- **高风险与确认**：回滚、执行重整、合并、退役为 `high`。全局助手调用时不直接执行，生成一条用户确认请求（`interaction_requests`），工具返回 `awaiting_user`；用户在助手页确认后按助手当前权限执行，结果回告助手。用户自己在页面上操作时仍是页面内的预览 / 原地确认。所有写操作都校验预览（或版本差异）给出的 `board_version`，重整与合并 / 退役另校验 `revision`，所以用户确认时架构图或相关任务已经变了会执行失败并提示重新预览，而不会按过期的方案执行。
- **准入**：执行重整、合并、退役可能投递交接通知并唤醒接手 leader，按 `model_work` 准入（更新暂停时拒绝）；回滚不唤醒任何会话。
- **说明文字**：每条的 `summary` / `description` 写明何时用、先预览再执行、确认请求，进入全局助手的工具指南（`ops/guide.py`）与 `crew_search`；预览一条的摘要就是“开局 / 重整先预览，再请用户确认执行”。
- 参数完全由清单的 JSON Schema 校验；未知的变更类型在 schema 里就被拒绝（`type` 是枚举），其余逐条问题由预览列出。生成 CLI 的数组参数是逐项文本，`changes` / `handoffs` 这类对象数组目前无法从命令行传入（生成 CLI 的通用限制）。

## 6. 事件流

不新增 kind。版本记录、回滚、重整都在同一事务里抬高 `domain_boards.version` 并 `change_feed.touch('domains', …)`；归档、任务、leader 通知各自的写入点与触发器原本就登记失效。架构图页面收到 `domains` 版本变化后重读架构图，版本历史面板开着时一并重读列表与选中的差异。

## 7. 界面

- **架构图 → 版本历史**：标题栏按钮打开右侧抽屉（手机全屏）：版本列表（来源标签、说明、时间、part / leader 数）、选中版本看“当前 → 该版本”的改前 / 改后对照、回滚确认（列出失去归属 / 已删除 / 仍归档的 leader）。手机上只看不回滚，与架构图“手机只读”一致。
- **重整预览面板**（`frontend/restructure-ui.js`，`window.CrewRestructure`）：不依赖 `app.js` 的独立组件，`open({project, changes, note})` 打开；显示改前 / 改后、影响清单、可编辑交接稿（字数、截取提示、是否立即唤醒）、每条变更可移除、合并 / 退役的接手人可改；确认后执行。架构图上 leader 的右键菜单提供“合并到…”“退役…”，打开同一面板。
- 三视口（1280 / 758 / 375）浅深色，样式只用 Spatial Studio 变量。

## 8. 下一步：助手流程怎么用

1. **开局**：读导图（`mindmap.get`）→ `mindmap.preview` 出方案 → 用户确认（R5 确认请求）→ `mindmap.convert`（记 `mindmap` 版本）→ 费用确认后 `leader.create` 建 Leader、`domains.apply` 放进 part、`goal.create` 派首批任务。空白目录无导图时用 `domains.restructure.preview` / `apply` 的 `part.create` 起盘，同样记版本。
2. **重整**：`domains.get` + `domains.versions.list` + `goal.list` 探底 → 拼变更条目调 `domains.restructure.preview`，按 `errors` 修方案 → 把同一组 `changes` 交给确认卡（嵌 `CrewRestructure` 面板，用户可改交接稿、去掉条目）→ `domains.restructure.apply` → 对 `to_start` 做费用确认后启动。
3. **回滚**：用户说“退回去”时 `domains.versions.list` 找到版本 → `domains.version.get` 给用户看差异与失去归属的 leader → 确认后 `domains.rollback`。

## 9. 待接线

- 助手页面的确认请求目前按通用格式显示操作名与参数；还没有在确认卡里嵌 `CrewRestructure` 面板（或版本差异）——需在 `assistant.html` 加载 `restructure-ui.js` / `.css`，对 `domains.restructure.apply`、`leader.merge` / `retire`、`domains.rollback` 的确认请求用面板展示改前 / 改后。
- 作战室主题：预算上限与首轮暂停；交接稿投递如需改走作战室消息，由作战室在 `chat_delivery` 侧接。
