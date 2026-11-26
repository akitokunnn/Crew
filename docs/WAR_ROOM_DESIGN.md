# 跨 Leader 作战室（Leader 发起）设计

> 日期：2026-11-27 · 状态：第一版施工中（阶段四）
> 依据：[新手引导与 Teamwork 设计](ONBOARDING_AND_TEAMWORK_DESIGN.md) §1、§4.1、§5、§7；投递状态机见 [消息投递设计](MESSAGE_DELIVERY_DESIGN.md)。
> 范围：只做 Leader 发起的跨领域联合推进。助手发起的开局 / 重整（§4.2–4.4）、预算上限不在本版。

## 1. 一句话

作战室 = 交流区里的一个**话题**，加上三样东西：**固定成员**、**共享状态板**（任务拆解 + 一份接口契约）、**统一拍板卡**。消息仍是交流区发言（`channel_messages`），点名、回复通知、投递徽章全部沿用交流区的投递状态机；不另起消息系统。

## 2. 交流区和作战室的边界

| | 交流区 | 作战室 |
| --- | --- | --- |
| 用途 | 轻量异步：一问一答、通告、找人、交付通知 | 几个 Leader 为同一个目标持续协作，直到验收收尾 |
| 谁能看到 | 工作区所有 Leader 和用户 | 所有人可看（透明），只有成员会收到新发言摘要 |
| 谁会被叫醒 | 被 `@` 的、被回复的原帖作者 | 同左，但只能 `@` 成员；`@所有成员` 叫醒全体成员 |
| 共享状态 | 无 | 任务状态板、接口契约、拍板卡 |
| 生命周期 | 永久流水 | 开启 → 进行 → 收尾归档（只读保留） |

**什么时候升级成作战室**（Leader 守则与界面提示用同一套话）：

1. 同一件事要 **3 位及以上 Leader** 一起推进；或
2. 一条脉络已有 **4 条以上回复** 还没收口；或
3. 需要一份**共同遵守的接口 / 数据契约**或**分工状态**；或
4. 几个组的分歧需要**用户拍板**。

一问一答、通知结果、找人这类仍在交流区。

**一键升级**：交流区脉络（≥2 条回复时出现的“脉络 · N 条回复”）旁出现“升级为作战室”；Leader 用 `war_room.open` 带 `from_message`。升级时：脉络的参与者（发言人、被点名、被回复的 Leader）默认成为成员，可增删；原脉络留在交流区不搬家，作战室顶部引用它作为“起因”；交流区自动出现一条开房公告（谁开了什么作战室、成员是谁），收尾时再出现一条收尾公告。

## 3. 角色与协作规则

- **牵头 Leader**（开房者）：邀请 / 移出成员、发拍板卡、收尾。只负责收口：在成员各自说清后做结论、把跨组分歧汇成一张拍板卡、验收后收尾。**不当中间层转述**——成员之间直接 `@`。
- **成员 Leader**：直接 `@` 需要的成员；把自己负责的那块写进状态板（谁、做什么、状态、阻塞原因）；契约要改就直接改契约并在房里说一句；需要用户定的事告诉牵头，不各自去问用户。
- **用户**：随时在房里插话纠偏（用户发言同样叫醒被点名的人），在拍板卡上答复。
- 作战室发言与交流区一样短（150 字 / 3 行，长内容写私聊并标 `detail`）。

## 4. 数据模型（`schema.sql` + 迁移）

```sql
war_rooms(id, project, title, goal, lead_session_id,
          origin_message_id,        -- 从交流区哪条发言（脉络根）升级而来，可空
          opened_message_id, closed_message_id,  -- 交流区里的开房 / 收尾公告
          contract, contract_updated_at, contract_updated_by,
          status CHECK (status IN ('open','closed')), summary, created_at, closed_at)
war_room_members(room_id, session_id, joined_at, left_at, PRIMARY KEY(room_id, session_id))
war_room_items(id, room_id, title, owner_session_id,
               status CHECK (status IN ('todo','doing','blocked','done')), note, updated_at, updated_by)
war_room_decisions(decision_id PRIMARY KEY, room_id)     -- 拍板卡就是现有 decisions 的一条
war_room_reads(room_id, reader, message_id, updated_at, PRIMARY KEY(room_id, reader))
channel_messages.room_id INTEGER REFERENCES war_rooms(id)  -- 迁移加列；NULL = 交流区主流
```

- **消息**：作战室发言是 `room_id` 非空的 `channel_messages`。交流区列表、未读数、要点整理、Leader 每轮的交流区摘要都只看 `room_id IS NULL`。
- **投递**：`channel.post(..., room_id=)` 照常建 `channel_mentions` / `leader_notices`；徽章、钩子补送、P4 回合证据、P5 Codex 游标一律不变。回复作战室里的发言自动落在同一个作战室。
- **拍板卡**：`war_room.decide` 以牵头身份调用现有 `board.ask`（问题前缀“作战室《标题》：”），再记入 `war_room_decisions`。答复走现有流程回到牵头；房内时间线把卡片和答复一起显示，牵头据此在房里收口（第一版不另外通知成员，见 §9）。
- **已读**：`war_room_reads` 每个读者每个作战室一个游标（用户为 `user`）。
- 迁移：只加表、加一列，旧数据不动；沿用 `store` 的“先查后改、`BEGIN IMMEDIATE` 内复查”。

## 5. 每轮带给成员的内容

成员 Leader 下一轮的交流区摘要（Claude 走提示词钩子，Codex 走 `channel.preview` → 输入接受后 `channel.seen`）在交流区块之后追加，每个进行中的作战室一块：

```
[Crew 作战室 #3《支付回调联调》] 牵头：支付；你上次看之后有 2 条新发言：
- #41 05:31 前端 回复 #40 …：……
状态板：3 项进行中、1 项阻塞（语音：等签名算法）；契约 05:20 由 支付 更新
在房里说话：crew say --reply-to <编号> "…"（Codex 用 crew_say）；看全貌：war_room.get
```

点名自己的发言仍由通知单独送达，不重复。收尾后不再出现。

## 6. 操作（统一操作清单登记）

照 `plugin/crew/ops/domains/` 的写法登记在新模块 `ops/domains/war_room.py`。现有 `route(...)` 适配的是不知道调用者的旧 HTTP 函数，作战室要区分“谁在说话 / 谁在改”，所以用同样的 `register(Operation(...))` 字段，处理函数直接读 `ctx.caller`（用户 = `human`，Leader = R5 令牌带来的 `leader` 身份）。

| 操作 | 读写 | 谁能调 | HTTP | 说明 |
| --- | --- | --- | --- | --- |
| `war_room.list` | 读 | 用户、Leader | GET `/api/war-rooms` | 本工作区作战室（进行中在前） |
| `war_room.get` | 读 | 用户、Leader | GET `/api/war-room?id=` | 元信息、成员、状态板、契约、拍板卡、发言（分页） |
| `war_room.open` | 写 | 用户、Leader | POST `/api/war-room/open` | `title`、`goal`、`members`、可选 `from_message`；开房者为牵头 |
| `war_room.members` | 写 | 牵头、用户 | POST `/api/war-room/members` | `add` / `remove` 名字 |
| `war_room.post` | 写 | 用户、成员 | POST `/api/war-room/post` | `body`、`to`（`*` = 全体成员）、`reply_to`、`detail` |
| `war_room.item` | 写 | 成员、用户 | POST `/api/war-room/item` | 新增或更新状态板一项 |
| `war_room.contract` | 写 | 成员、用户 | POST `/api/war-room/contract` | 整份替换；带 `expected_updated_at` 防覆盖 |
| `war_room.decide` | 写 | 牵头 | POST `/api/war-room/decide` | 发统一拍板卡（问题、选项、推荐） |
| `war_room.close` | 写 | 牵头、用户 | POST `/api/war-room/close` | `summary`；交流区收尾公告，成员各收一条留档通知后回原岗 |
| `war_room.mark_read` | 写 | 用户、成员 | POST `/api/war-room/read` | 推进已读游标 |

Leader 在房里说话不需要新命令：`crew say --reply-to <房内编号>` 自动落在该作战室；新开一段用 `war_room.post`。

## 7. 收尾

`war_room.close`：状态改 `closed`，写 `summary` 与 `closed_at`；交流区出现收尾公告（结论一句话 + 链接）；每位成员收到一条**不叫醒**的留档通知（随下一次对话带上）：“作战室《x》已收尾，结论：……；回到原岗位继续”。成员会话不重建、不归档——它们本来就是常驻 Leader，协作过程已在各自上下文里，这就是“带着上下文回原岗”。房间只读保留，可在交流区公告或作战室列表里打开。

## 8. 界面（Spatial Studio，§5）

入口：侧栏“Leader 交流区”下方列出进行中的作战室；交流区里开房 / 收尾公告是一张可点的卡；脉络旁“升级为作战室”。

- **顶部参战栏**：成员头像、实时状态（在线 / 在跑任务 / 外部窗口 / 离线）、载体标签、上下文占用小环、牵头标记；每人一键“暂停”（现有停止）与“接管”（打开其私聊）。
- **中间主时间线**：起因引用 → 房内发言（投递徽章同交流区）；突出交接节点（状态板变化）、阻塞原因、拍板卡；底部发言框，用户可随时插话，可 `@所有成员`。
- **右侧面板**：目标与验收说明、任务状态板（按状态分组，可改）、接口契约（可编辑，显示谁何时改的）、拍板卡、成员当前任务与交付物入口。
- 三视口：1280 三栏；≤760 参战栏变横向滚动条、右侧面板收成底部单；375 单列。浅深色都覆盖。

视觉稿由 CLI Claude Code 出，验收后的规格写在本节末尾。

## 9. 不在第一版 / 已知取舍

- 拍板卡答复后不自动通知全体成员：由牵头收口时在房里说；以后可在 `board.answer` 里对作战室的卡片补发房内消息。
- 预算上限、助手发起的开局 / 重整、跨工作区作战室不做（§4.2–4.4、§7.2）。
- 成员只能是本工作区在用的 Leader；Git 助手不进房。

## 10. 与增量事件流、操作清单的衔接（待接线）

- 事件流：新增 kind `war_room`（范围 `workspace`，实体 = 作战室 id），在所有作战室写操作的事务里 `change_feed.touch(conn, 'war_room', project, room_id)`；房内发言同时是交流区发言，已有的 `channel` 失效照常触发。builder `war_room:change_value(conn, project, room_id)` 返回列表卡片值（标题、状态、成员、未读、阻塞数）。事件流合入前，前端在 `/api/state` 的 `channel.latest_id` 或成员状态变化时重新拉 `war_room.get`。
- 操作清单 / MCP：Leader 调这些操作要靠 R5 的会话令牌与通用入口（`crew_do` 一类）；CLI 子命令（如 `crew room …`）与 Codex 工具说明由 R5 接线。
