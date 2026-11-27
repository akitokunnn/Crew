# Crew 消息投递可靠性设计（R2，2026-11-27）

> 主题会话「消息可靠性」产出。依据：[总计划](REBUILD_PLAN_2026-11-27.md) 阶段三 R2、[新手引导与 Teamwork 设计](ONBOARDING_AND_TEAMWORK_DESIGN.md) 第 4–7 节、[AGENTS.md](../AGENTS.md)“依据证据判断状态”“按行动意图通知”。
> 本文第 3、4 节的投递状态枚举是**提议稿**，按总计划第 4 节由主会话定稿后成为共享契约。

## 1. 要解决的问题

| # | 现象 | 根因（代码现场） |
| --- | --- | --- |
| P1 | Leader 以为对方已看到或已回复，实际只是排队 | 交流区点名只有 `replied` 一个布尔值，没有“排队 / 已送达”之分；`crew say` 回执是一坨原始 JSON；私聊协作消息回执没有“对方已回复”这一态，`accepted` 之后再无进展可查 |
| P2 | 交流区待送点名被跳过 | `channel.summary` 把“本人有点名通知”的发言一律排除（假定通知会另行送达），同时把已读游标推到最新。但网页服务**从不向正在外部窗口运行的 Claude Code 会话写输入**（`web.py deliver_notices` 遇到外部进程只记 `leader:notice_blocked`），钩子也不带 Leader 通知，于是这条点名在对方使用期间既不在摘要里、也不会送达 |
| P3 | 只用 `--reply-to` 引用回复时原帖作者不知道 | `channel.post` 只给 `@`/`--to` 点名的人排通知，回复关系只存在 `reply_to` 列里 |

诊断中另外发现、与上面同类的问题（列入第 7 节施工）：

- **P4 假“已在私聊回复”**：`web.settle_channel_mentions` 在“通知已送出且 leader 不忙”时代发“回复在私聊里”。`chat.busy()` 只在托管进程存活且在工作时为真；leader 回合失败、被用户停止、空闲回收后进程退出，都会被当成“已经答完”，交流区出现一条 leader 根本没说过的回复。
- **P5 Codex 摘要先推游标再投递**：Codex 没有提示词钩子，`chat._send` 在拼输入时调用 `channel.summary`，游标在输入被接受**之前**就前移；输入被拒或回执未知时，这些新发言摘要丢失。
- **P6 外部会话回合中途收不到 Leader 通知**：PreToolUse 钩子只带 `peer_messages`，不带 `leader_notices`；外部 leader 只有在用户下次打字时才能收到交流区点名。

## 2. 现有链路（全链路梳理）

| 通道 | 存储 | 谁投递 | 何时投递 | 送达判定 |
| --- | --- | --- | --- | --- |
| 私聊协作消息（`crew send` / `crew_send_message`） | `peer_messages`（`delivered_at`、`accepted`、`error`、`kind`、`reply_to`） | ① 托管会话：`chat._deliver_messages`（对方空闲；leader 对自己组员可 steer）② 外部 CC：PreToolUse / UserPromptSubmit 钩子 `messaging.take_for_hook` | ① 对方空闲 ② 对方下一次工具调用或提示词 | ① `chat.send` 回执 `accepted` → `accepted=1` ② 写入钩子附加上下文 → `delivered_at` |
| 任务内消息（目标执行之间） | `peer_messages` + `goal_message_meta`（`status` queued/delivered/accepted/archived/failed） | `goal_messages.deliver` / `take_for_hook` | 执行空闲 | 同上，状态写在 meta |
| Leader 通知（拍板答复、交流区点名、Git 结果等） | `leader_notices`（`sent_at`、`wake`、本次新增 `delivered_via`） | `web.deliver_notices`：同一 leader 的待送通知合成一个 `leader_notice_batch`（先写 journal 再 `chat.send`），用户的交流区原话单独成批并在私聊显示为用户气泡 | leader 空闲；外部运行则阻塞 | 回执 `accepted` 后 `mark_notice_sent` |
| 静默通知（`wake=0`） | 同上 | `chat.send` 随用户下一条消息附带 | 用户下次发言 | 同上 |
| 交流区发言 | `channel_messages`（`reply_to`、`detail`、`auto`）+ `channel_mentions`（`notice_id`、`await_reply`、本次新增 `reason`）+ `channel_reads` 游标 | 点名 / 回复通知走 Leader 通知；其余发言靠 `channel.summary` 在下一轮提示词附带（CC 钩子、Codex 拼输入） | 同上 | 通知见上；摘要附带即推游标 |
| 用户点名后未在交流区回答 | `channel_mentions.await_reply=1` | `web.settle_channel_mentions` → `channel.settle` 代发“回复在私聊里” | leader 不忙 | 见 P4 |

输入层另有一份证据：每个会话的 `chat/<id>.jsonl` journal 记录 `input_delivery`（`submitted → accepted / rejected / unknown`）和 `leader_notice_batch`。它是“已写进对方输入”的最终依据，数据库状态是它的投影。

## 3. 后端投递状态机（提议的共享枚举）

```
          ┌────────── archived（仅归档，不投递、不唤醒）
 发出 ──► │
          └──► queued ──► delivered ──► replied
                 │  ▲         │
                 │  └─(重试)──┘(回执未知时保持 queued，原编号重试，不重复成功操作)
                 └──► failed（终态：目标已归档/移除、被拒且不可重试；带真实原因）
```

| 状态 | 含义（对外文案） | 判定证据（只由后端写） |
| --- | --- | --- |
| `none` | 不投递（点名自己） | `channel_mentions.notice_id IS NULL` |
| `archived` | 仅归档，未投递、不唤醒 | `kind IN (information, ack)` 或任务内重复消息 |
| `queued` | 已排队，对方尚未收到 | 有待送记录，未被任何投递者认领；`DeliveryWaiting:` 只是等待原因，不改变状态 |
| `delivered` | 已送达对方输入，未回复 | 交流区：`leader_notices.sent_at`；私聊：`delivered_at`（钩子）或 `accepted`（原生输入回执）。细分放在 `via`：`input` / `hook` |
| `replied` | 对方已回复 | **派生，不存储**：交流区存在对方 `reply_to` 本帖的发言；私聊存在对方 `reply_to` 本消息的协作消息 |
| `failed` | 投递失败，带原因 | `peer_messages.error` 非 `DeliveryWaiting:`、meta `failed` |

已落地（第二部分）：交流区通知的状态机在 `chat_delivery.py` 顶部——`notice_state` 读状态，`claim_batch` / `claim_for_hook` 两个认领者，`note_waiting` 记等待原因，`_first_turn_succeeded` 判回合证据；`DeliveryMixin.deliver_leader_notices` / `settle_channel_mentions` 是两个驱动循环（原在 `web_service/workers.py`，现在那里只取外部进程列表后委托）。

规则：

1. **单调**：`queued → delivered → replied` 只前进。认领一律用带条件的更新（`UPDATE … SET sent_at=? WHERE id=? AND sent_at IS NULL`，看 `rowcount`），谁先标记谁投递，并发的钩子和网页服务不会重复送达。
2. **同一条通知只有一个投递者**：网页服务在写 journal 批次**之前**先用带条件的更新把通知记到 `leader_notices.batch_id`（`WHERE sent_at IS NULL AND (batch_id IS NULL OR batch_id=?)`），该批就独占这些通知（重试按 `batch_id` 找回同一批，journal 里没写成就按同一编号重建）；钩子只认领 `batch_id IS NULL` 的通知。归属只看数据库，`channel.py` 不再读 chat 日志。
3. **“已回复”只看回复记录**：送达、回合结束、代发占位都不是回复证据。`replied` 不落库，避免两处写入。
4. **兼容现有字段**：私聊回执保留 `status` 的 `delivered` / `accepted`（任务内消息、目标验收依赖它们），新增 `replied` 与 `replied_by`；`accepted` 布尔在 `replied` 时仍如实反映原生输入是否接受过。定稿后对外统一成结构 `delivery: {state, via, at, error, replied_by}`，旧字段在下一次大版本删除。

## 4. 回执、提示词与前端怎样如实区分

| 位置 | queued | delivered | replied | 其他 |
| --- | --- | --- | --- | --- |
| `crew say` 回执（CLI 文本） | `@beta：已排队，对方尚未收到；送达后才会看到` | `@beta：已送达，未回复` | `@beta：已回复` | 没点名：`没有点名任何人，不会叫醒谁。`；回复通知：`alpha（原帖作者）：…` |
| `crew_say`（Codex） | 返回发言 JSON + `receipt` 同上文案；`mentions[].delivery`、`notified[].delivery` | 同左 | 同左 | 工具说明写明 queued 不等于已看到 |
| `crew channel` / 摘要行 `channel.line` | `@beta（已排队，对方尚未收到）` | `@beta（已送达，未回复）` | `@beta（已回复）` | 回复通知：`（已通知 alpha：…）`；自点名不带括号 |
| `crew send` 回执 | `[task · queued] 已排队，尚未交给执行输入。` | `[task · accepted] 已交给执行输入，尚不代表模型已读…` / `delivered`（钩子） | `[task · replied] 对方已回复（回复消息编号 X）；回复不代表任务已完成，以回复内容为准。` | archived / failed 原有文案 |
| 被点名方收到的通知 | — | `交流区：「alpha」点名你…` / `交流区：「beta」回复了你的交流区发言 #N…` / 用户版本带“这是用户本人的话” | — | 外部会话钩子补送：`[Crew 交流区点名] 以下点名之前没送到你这里，现在送达：` |
| Leader 守则 | 点名或回复别的 leader 的发言才叫醒对方；叫醒要等对方空闲；“排队中就是对方还没看到，别当成对方已知道或已回复” | | | |
| 前端交流区 | 状态徽章，见第 8 节 | | | |

前端只渲染后端给的 `delivery`，不自行推断（例如不再用“发言人是不是用户”决定显示与否）。

## 5. 数据模型与迁移

新增四列（`schema.sql` 与 `store._migrate_channel_replies`；前两列第一部分、后两列第二部分）：

| 表.列 | 定义 | 旧数据含义 |
| --- | --- | --- |
| `channel_mentions.reason` | `TEXT NOT NULL DEFAULT 'mention' CHECK (reason IN ('mention','reply'))` | 旧行都是显式点名 → `mention` |
| `leader_notices.delivered_via` | `TEXT CHECK (delivered_via IN ('hook'))` | `NULL` = 写进输入（含全部旧通知）；`hook` = 外部会话提示词钩子带上 |
| `leader_notices.batch_id` | `TEXT` | 网页服务输入批次的编号（`leader-notices:<摘要>` 或 `channel:<发言>:<摘要>`），认领即写；旧通知为 `NULL` |
| `leader_notices.waiting` | `TEXT CHECK (waiting IN ('external','busy','stopped'))` | 还在排队的原因，网页服务每轮看到原因变化才写；送出后不再读取 |

- **保留历史**：只加列，不改不删旧行；旧点名的 `delivery` 由现有 `notice_id` / `sent_at` / 回复记录直接推出。
- **幂等**：沿用迁移函数先查后改；重复打开数据库为空操作（测试 `test_older_mentions_gain_a_reason_and_keep_their_notices` 连开两次）。
- **并发安全**：`BEGIN IMMEDIATE` 后在事务内重新检查缺列，再 `ALTER TABLE`；多进程同时打开只有一个真正加列。
- 不新增“状态”列：`queued/delivered` 由现有时间戳决定，`replied` 派生；避免同一事实两处写入。
- 后续（定稿后）如需对外统一 `delivery.at`，`leader_notices.sent_at`、`peer_messages.delivered_at` 已能提供，不必迁移。

## 6. 已落地

| 提交 | 内容 |
| --- | --- |
| `9f26b12` | 先写复现测试 `plugin/tests/test_message_delivery.py`（13 例，改前 11 例失败） |
| `7e0ada4` | `channel.py`：点名 / 回复通知带 `delivery`，`line()` 与新增 `receipt()` 如实写状态；`post()` 只用 `reply_to` 时通知原帖作者 Leader（自回复、回复用户、作者已退役都不排通知；同时 `@` 了作者只发一条）；`summary(carry_pending=…)` 让外部会话钩子认领未进批次的待送点名并完整送达，标记 `delivered_via='hook'`；`awaiting()` 不再把钩子送达的用户点名交给 `settle` 代发。`hooks.py` 外部会话（无 `CREW_SESSION_ID`）传 `carry_pending=True`。`messaging.receipt` / `message_receipts` 增加 `replied`。`cli.py` `crew say` 输出回执文本；`codex_tools.py` `crew_say` 返回 `receipt`。`schema.sql` / `store.py` 迁移两列 |
| `80a69c3` | Leader 守则（`plugin/skills/leader/SKILL.md`）与 `crew_say` 工具说明同步：回复会通知原帖作者、排队不等于已看到或已回复 |

验证：`test_message_delivery`、`test_channel`、`test_message_receipts`、`test_mixed_messaging`、`test_leader_channel_http`、`test_channel_digest(_codex)`、`test_cards_notices`、`test_notice_noise`、`test_goal_messages`、`test_codex_tools` 共 209 例通过；钩子相关 `test_core`、`test_hook_project`、`test_inactive_runtime`、`test_session_ops`、`test_trust_mode`、`test_workspace_activity` 165 例通过；`test_channel_runtime_ui.js` 通过。三条既有断言因新增 `delivery` 字段改写（`test_channel` 两处、`test_leader_channel_http` 一处，旧断言只缺新字段）。

**第二部分（阶段二拆分合入后）**：

- `chat_delivery.py`：交流区通知状态机（见第 3 节）；`deliver_leader_notices` 先认领后写批次，外部运行 / 正忙 / 被停止时记 `waiting`；`settle_channel_mentions` 只在承载该点名的批次输入 `accepted` 后**第一个**结束的回合成功时代发“回复在私聊里”（P4），回合失败、被停止、进程丢失都不代发，徽章保持“已送达，未回复”；Codex 的交流区摘要改为 `channel.preview` + 输入接受后 `channel.seen`（P5）。
- `channel.py`：去掉读 chat 日志的 `_batched`；点名 / 回复通知多给 `queued_at`、`delivered_at`、`via`、`waiting`、`reply_id`；`settle` 改为只处理投递模块给出的、有回合证据的发言。
- `web_api/board.py`：`POST /api/channel` 接受用户的 `reply_to`。
- `web_service/workers.py`（增量事件流主题的文件，只动了这一处）：`deliver_notices` 改为取外部进程列表后委托给 `chat.deliver_leader_notices`，`settle_channel_mentions` 搬进投递模块。
- 前端交流区按第 8 节重做：`frontend/channel.js`、新增 `frontend/channel.css`（`index.html` 引用，`web.py` 的 `STATIC` 登记一行）、`app.js` 只改 `renderChannel()`。

## 7. 拆分后施工清单（状态）

| # | 内容 | 状态 |
| --- | --- | --- |
| 1 | P4 代发“回复在私聊里”要有回合证据 | 已做（`chat_delivery.settle_channel_mentions`） |
| 2 | P5 Codex 摘要两阶段 | 已做（`channel.preview` / `channel.seen`） |
| 3 | 外部运行等排队原因可见，`delivery` 带 `queued_at` | 已做（`leader_notices.waiting`，三种原因） |
| 4 | 用户在网页回复某条发言 | 已做（`/api/channel` 的 `reply_to`，前端引用条） |
| 5 | 前端交流区重做 | 已做（见第 8 节） |
| 6 | 私聊气泡投递徽章、“待送达：交流区 #id”标记（`chat.js`） | 未做：`chat.js` 不在本主题归属，见第 12 节待接线 |
| 7 | 侧栏 / 头部“N 条通知排队中”（`app.js` 数据层） | 未做：`app.js` 数据层归增量事件流，见第 12 节 |
| 8 | 徽章变化的增量事件 | 未做：`change_feed` 尚未合入集成分支，生产点列在第 12 节 |

**P6**（外部 leader 回合中途经 PreToolUse 收点名）：仍待主会话拍板，未做。

## 8. 交流区界面重设计（定稿）

视觉稿由 CLI Claude Code（claude-dispatch，`claude-opus-5-5`，effort high）产出，主题会话看过 6 张截图后验收定稿；实现同样派 CLI Claude Code 按原型移植到 `frontend/channel.js` / `frontend/channel.css`，主题会话审核并在真实后端上走查后，原型目录 `prototypes/channel-delivery/` 已删除（原型、截图与色值全表见提交 `3df4e99`）。

实现与原型的差异：不画原型自带的左侧栏（应用有自己的侧栏）；手机发言框底部不再加安全区（底栏已留）；详情卡不显示字数（`detail` 只是布尔）；“新发言”分隔线按未读数定位到第一条未读；状态说明里的示例徽章写“@leader”且不可点。

**版式**：玻璃页头（标题、`整理`、预留的虚线禁用 `作战室·后续`）→ 要点卡（可折叠）→ 发言流（顶部“已整理的 N 条原始发言”折叠区，“新发言”分隔线，打开时停在分隔线）→ 发言框（引用条、点名、输入）。≥1024 右侧常驻 288px“投递追踪”栏：成员 2×2（载体、在线 / 在跑任务 / 离线）、按 Leader 合并的“等待回应”（排队最久的在前）、状态说明。≤760 收成页头下一条“N 位 leader 待回应 · 语音 排队 14 分钟 · 投递追踪 ›”，点开为底部单。

**投递徽章**（类名前缀 `cx-`，新增变量前缀 `--dl-`，只用 `style.css` 现有变量派生）：两段式 `[@名字 │ 图标 状态]` + `[私聊图标]`，左段点开状态说明，右段直达私聊。高 26px（≤760 或粗指针 32px），透明热区扩到 32px。

| `delivery` | 外观 | 图标 | 文案 |
| --- | --- | --- | --- |
| `queued` | 透明底、虚线框 | 时钟 | 排队中 |
| `queued` ≥ 10 分钟 | 琥珀底、实线框（不加动画） | 时钟带 ! | 排队 N 分钟 |
| `delivered` | 卡片底、蓝色实线框 | 单勾 | 已送达 |
| `replied` | 青色填充（`--accent` 13%） | 双勾 | 已回复 |
| `none` | 只显示名字 | — | — |
| `archived`（私聊 / 作战室） | `--hover` 底 | 归档盒 | 仅归档 |
| `failed`（私聊 / 作战室） | 浅红底 | 三角 ! | 失败（说明里写原因） |

框型、颜色、图标、文字四个信号同时区分，色弱也能分清；徽章文字对比度浅色 4.96–6.37、深色 4.74–7.86（AA）。Crew 代发的“回复在私聊里”用实线浅底，不与“排队”的虚线撞义。

**状态说明**：桌面悬停 220ms 或点击固定；≤760 为底部单（按钮 44px）。写清对方当前状态（离线 / 在跑任务 / 外部窗口运行）、**是否已经看到**（加粗）、已等多久；主按钮“打开 X 的私聊”，`replied` 另有“跳到回复 #id”。

**脉络**：发言流始终按时间排序。被回复的发言顶部有引用块，点击跳到原发言并闪烁 2.2s（`prefers-reduced-motion` 时改静态描边）；一条发言的后代回复 ≥2 条时出现“脉络 · N 条回复”，展开为缩进树（每层 20px，手机 14px），点任一行跳转。发言框引用条可取消；回复对象是未被点名的 Leader 时标“会通知 X”（对应本次后端的回复通知）。

**私聊直达落点**：`detail` → 发言者私聊里该发言时刻；“回复在私聊里” → `channel:<reply.id>:` 前缀定位；`queued` → 对方私聊底部“待送达：交流区 #id”标记；`delivered` / `replied` → 对方私聊里注入的“交流区 #id”输入；`failed` / `archived` → 那条私聊消息原位。

**三视口**：1280 左栏 + 主卡 + 右侧投递追踪，要点卡展开；758 去左栏、出返回键、投递追踪收成顶部条、要点卡折叠、点名横向滚动；375 页头两行、副标题短文案、脉络树铺满并隐藏时间、徽章区“点名”标签单列。

**验收时提出、实施时一并改**：

1. “点名 / 回复通知”标签改用 `--text-2`（原型用 `--text-3`，约 3:1 不到 AA）。
2. 375 宽度发言框占约 160px：选中引用后收起点名行。
3. 1280 首屏放不下状态说明的“仅归档 / 失败”两项：说明区默认只列交流区会出现的四态，其余两态放进徽章自己的说明浮层。
4. 排队时长用 `leader_notices.created_at`（排通知的时刻）而不是发言时间；两者现在相同，以后若有重新排队就不会偏大。后端需在 `delivery` 里给出 `queued_at`（第 7 节第 3 条一并做）。
5. 成员格的在线 / 在跑任务 / 外部窗口运行取 `/api/state` 已有的 `where` / `busy` 字段，不新增接口。

## 9. 与操作清单 / MCP、增量事件流、跨 Leader 作战室的衔接

**操作清单 / 生成式 MCP（总计划第 7 条裁决）**

| 操作 | 读写级别 | 权限范围 | 幂等 | 回执 |
| --- | --- | --- | --- | --- |
| `channel.post` | 写 | leader（本工作区）、用户 | 否 → 建议加调用方 `client_id`，同编号重放返回原发言（对齐 t3code“命令带 ID 只执行一次”） | 发言 + `mentions[].delivery` + `notified[].delivery` + `receipt` 文本 |
| `channel.read` | 读（推游标） | leader、用户 | 是 | 发言列表（含状态） |
| `message.send` | 写 | 同项目会话 | 按 `message_id` 幂等（已有） | 第 3 节回执 |
| `message.receipt` | 只读 | 发送方、接收方 | 是 | 第 3 节回执（新增：按编号查最新状态，代替 Leader 反复问“你收到没”） |
| `channel.delivery`（新增，可选） | 只读 | leader、用户 | 是 | 我发出的点名里仍在 `queued` 的清单 |

- **身份绑定的耦合点**：本阶段用“钩子进程有没有 `CREW_SESSION_ID`”区分托管 / 外部会话以决定 `carry_pending`。改成每会话 MCP 令牌后，这个判断要换成会话登记里的“是否由 Crew 托管输入”，否则外部会话会退回 P2。R5 会话改身份来源时必须同步这一处（`hooks.py` 调 `channel.summary` 处有注释）。
- 回执文案由操作清单生成的工具说明引用同一份状态枚举，不在提示词里另写一套。

**增量事件流（R0 迁移）**：状态变化点都已在后端单点发生——排通知（`leader:notice`，已有）、认领送达（网页批次 / 钩子）、回复产生（`channel.post` 带 `reply_to`）。事件流只需在这三处各发一条 `delivery:changed {message_id, session_id, state}`，前端按事件更新徽章，不再靠 3 秒全量轮询看状态。

**跨 Leader 作战室**：作战室 = 交流区里的一个话题（根发言 + 回复链）+ 成员表 + 共享状态板。复用本设计：

- 成员间点名、回复通知、投递状态全部沿用 `channel_mentions` / `leader_notices` / 第 3 节枚举；作战室只新增“话题归属”（如 `channel_threads(root_message_id, room_id)`）与成员表，不另建消息表。
- “回复通知原帖作者”保证线程内的来回对话不会因为忘了 `@` 而断；作战室的“统一拍板卡”复用现有拍板，不走交流区。
- 界面上的“升级为作战室”入口挂在话题线程上（第 8 节已预留位置）；交流区与作战室的边界（设计文档第 7 节待定项 1）建议按“同一话题来回超过 3 轮或涉及 3 个以上 leader 时提示升级”，由主会话定。

## 10. 真实 AI 实测

隔离 `--home`、独立端口、临时项目目录；CC 用 `claude-haiku-5-5`，Codex 用 `gpt-6-luna`（reasoning low）。逐条记录模型、实际行为、不符合预期之处。

1. 两个 leader（CC + Codex）：A 在交流区 `@B` 提问，B 正忙 → A 的 `crew say` / `crew_say` 回执应为“已排队”，A 不应据此说“B 已回复 / 已知道”。
2. B 空闲后收到通知并 `--reply-to` 回答（不写 `@A`）→ A 被叫醒并收到“回复了你的交流区发言”；A 自己回复自己时不被叫醒。
3. 外部窗口运行的 CC leader 被点名 → 用户在该窗口打字时，提示词里出现“以下点名之前没送到你这里”，且只出现一次；交流区徽章变为“已送达”。
4. `crew send --kind task` 后对方 `--kind result --reply-to` → 发送方查回执为 `replied`；`information` 始终 `archived`。
5. 用户在交流区点名外部 leader → 不出现 Crew 代发的“回复在私聊里”。
6. （第 7 节 P4 落地后）leader 回合失败或被停止 → 不代发“回复在私聊里”。

### 实测结果（2026-11-27，第二部分）

隔离 `CREW_HOME`（临时目录）、临时项目、PATH 去掉正式 `~/.crew/bin` 只留临时 `crew` 包装；网页服务不参与，“送达”由脚本代替网页服务把通知写成 `[Crew]` 输入；Claude 用 `claude-haiku-5-5`（`--setting-sources project --strict-mcp-config`），Codex 用 `gpt-6-luna`（reasoning low，`codex exec`）。两个 leader：支付、语音。

| 场景 | 模型 | 实际行为 | 结论 |
| --- | --- | --- | --- |
| 1 点名后用户要对方答复 | haiku（支付） | `crew say "@语音 …"`，回执“@语音：已排队，对方尚未收到；送达后才会看到”；又查 `crew channel` 看到“已排队”；回答“语音组还没有回复……回复后我再把原话告诉你” | 符合，没有编造对方答复 |
| 1 同上 | luna（支付） | 同样发出点名，答“消息还在排队，对方尚未收到，所以目前还没有回复” | 符合 |
| 2 收到点名并回复 | luna（语音） | 只用 `crew say --reply-to 1 "…"`，没写 `@支付`；支付随即排上“「语音」回复了你的交流区发言 #1”通知，#1 徽章变“已回复” | 符合 |
| 2b 原帖作者收到回复通知 | haiku（支付） | 读交流区后 `--reply-to 2` 回了一句实质内容（不是“收到”），语音因此又被通知 | 符合规则；来回两轮后自然停止，没出现刷屏 |
| 3 外部窗口 leader 的待送点名 | haiku（语音） | 钩子上下文出现“以下点名之前没送到你这里，现在送达”，带上 2 条（一条回复通知、一条点名），第二次钩子为空；模型回复了点名那条，回复通知那条没回（内容不需要回） | 符合；两条通知 `delivered_via=hook`，徽章“已送达” |

不符合预期 / 要留意：

- luna 开局会大范围摸环境（列目录、读 `--help`、找 `rg`），还读了本机 `~/.codex/memories/MEMORY.md`（Codex 自身的记忆文件，只读，没写）。实测只隔离了 Crew 数据，没隔离 Codex 自己的 HOME；以后实测可给 Codex 单独的 `CODEX_HOME`（需另行登录）。
- haiku 第一次找不到点名用的名字时试了不存在的 `crew leaders` 命令，随后用 `crew status` 找到。属于既有 CLI 可发现性问题，不是本次改动引入。
- “回复自己不叫醒自己”、P4 回合证据、P5 Codex 游标都是后端行为，没有让模型去触发，由自动测试覆盖。

## 11. 已知限制

- 迁移不回填 `batch_id`：升级那一刻若某条通知已写进旧 journal 批次但还没送出，而该 leader 恰好转到外部窗口运行，钩子可能再送一次。需要“曾托管、批次投递失败、随后转外部”三件事同时发生，不为它读全部旧 journal。
- 升级前已送出、仍等待回复的用户点名没有 `batch_id`，不会再代发“回复在私聊里”，徽章停在“已送达，未回复”，直到 leader 在交流区回复。
- 外部窗口运行的 leader 回合是否结束 Crew 看不到，所以钩子送达的用户点名永远不代发“回复在私聊里”。
- 外部会话中途（PreToolUse）仍收不到 Leader 通知（P6，待拍板）。
- 投递追踪栏的“等待回应”只统计当前已加载的发言；更早的排队点名要往上加载才会出现。

## 12. 待接线（交给主会话 / 其他主题）

| 归属 | 位置 | 要做的事 |
| --- | --- | --- |
| 主会话（合并时留意） | `plugin/crew/web.py` 的 `STATIC` | 本分支已加一行 `/channel.css`；若主会话统一管静态表，按原样保留或挪到统一位置 |
| 增量事件流（**已接线**，第 4 轮） | `web_service/workers.py` | 合并时以本分支结构为准：投递与代发留在 `chat_delivery`，`workers.py` 只加回增量记录线程 |
| 增量事件流（**已接线**，第 4 轮） | `change_feed` 生产点 | 登记 `channel_message`（页面类 kind，值为 `channel.listed_message`，即 `/api/channel` 列出的整条发言）。按事件流的“只记失效、发送前现算”实现：`deliver_leader_notices` 的 `mark_notice_sent` / `note_waiting` 是自动提交的单句、不在 `board.transaction` 里，所以投递列（`sent_at`、`delivered_via`、`waiting`）的变化由 `leader_notices` 的更新触发器标记（同时覆盖 `claim_for_hook`），`chat_delivery.py` 未改；`channel.post` 的 `reply_to` 与 `channel.settle` 直接标记被回复的发言。前端 `CrewChannel.changed` 按 id 替换已加载的发言，删除了按 leader 状态变化重读最新页的近似做法。见 `docs/EVENT_STREAM_DESIGN.md` 4.3、5.3、第 7 节 |
| 增量事件流（**已接线**，第 4 轮） | `app.js` 数据层 / 侧栏 | leader 行在 `notices_pending > 0` 时显示“N 条通知排队中”；卡片新字段 `channel_queued_id` 不为空时可点，打开交流区并定位到最早一条排队点名（已在交流区时直接跳到该发言） |
| 前端私聊（`chat.js` 归属方） | 私聊气泡 | 交流区转来的用户原话（`channel:<id>:` 开头的消息）和 `crew send` 消息显示同一套徽章；对方私聊底部加“待送达：交流区 #id”标记，交流区排队徽章的私聊按钮落到这里 |
| 操作清单（R5） | `codex_tools.py` / `cli.py` | 本轮不需要改工具说明；`crew_channel` 的 JSON 自动多出 `queued_at`、`delivered_at`、`via`、`waiting`、`reply_id`，若清单对输出字段有登记，补上这五个 |
| 操作清单（R5） | `hooks.py` 身份 | 钩子用“进程里有没有 `CREW_SESSION_ID`”判断外部会话并传 `carry_pending`；改成 MCP 令牌绑定身份时同步改这里，否则外部 leader 的待送点名会丢 |
