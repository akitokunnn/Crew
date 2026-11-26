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

规则：

1. **单调**：`queued → delivered → replied` 只前进。认领一律用带条件的更新（`UPDATE … SET sent_at=? WHERE id=? AND sent_at IS NULL`，看 `rowcount`），谁先标记谁投递，并发的钩子和网页服务不会重复送达。
2. **同一条通知只有一个投递者**：网页服务一旦把通知写进 `leader_notice_batch`，该批就独占这些通知（重试用同一批编号）；钩子只认领**不在任何批次里**的通知。
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

本阶段新增两列（`schema.sql` 与 `store._migrate_channel_replies`）：

| 表.列 | 定义 | 旧数据含义 |
| --- | --- | --- |
| `channel_mentions.reason` | `TEXT NOT NULL DEFAULT 'mention' CHECK (reason IN ('mention','reply'))` | 旧行都是显式点名 → `mention` |
| `leader_notices.delivered_via` | `TEXT CHECK (delivered_via IN ('hook'))` | `NULL` = 写进输入（含全部旧通知）；`hook` = 外部会话提示词钩子带上 |

- **保留历史**：只加列，不改不删旧行；旧点名的 `delivery` 由现有 `notice_id` / `sent_at` / 回复记录直接推出。
- **幂等**：沿用迁移函数先查后改；重复打开数据库为空操作（测试 `test_older_mentions_gain_a_reason_and_keep_their_notices` 连开两次）。
- **并发安全**：`BEGIN IMMEDIATE` 后在事务内重新检查缺列，再 `ALTER TABLE`；多进程同时打开只有一个真正加列。
- 不新增“状态”列：`queued/delivered` 由现有时间戳决定，`replied` 派生；避免同一事实两处写入。
- 后续（定稿后）如需对外统一 `delivery.at`，`leader_notices.sent_at`、`peer_messages.delivered_at` 已能提供，不必迁移。

## 6. 本阶段已落地（只改非拆分文件）

| 提交 | 内容 |
| --- | --- |
| `9f26b12` | 先写复现测试 `plugin/tests/test_message_delivery.py`（13 例，改前 11 例失败） |
| `7e0ada4` | `channel.py`：点名 / 回复通知带 `delivery`，`line()` 与新增 `receipt()` 如实写状态；`post()` 只用 `reply_to` 时通知原帖作者 Leader（自回复、回复用户、作者已退役都不排通知；同时 `@` 了作者只发一条）；`summary(carry_pending=…)` 让外部会话钩子认领未进批次的待送点名并完整送达，标记 `delivered_via='hook'`；`awaiting()` 不再把钩子送达的用户点名交给 `settle` 代发。`hooks.py` 外部会话（无 `CREW_SESSION_ID`）传 `carry_pending=True`。`messaging.receipt` / `message_receipts` 增加 `replied`。`cli.py` `crew say` 输出回执文本；`codex_tools.py` `crew_say` 返回 `receipt`。`schema.sql` / `store.py` 迁移两列 |
| `80a69c3` | Leader 守则（`plugin/skills/leader/SKILL.md`）与 `crew_say` 工具说明同步：回复会通知原帖作者、排队不等于已看到或已回复 |

验证：`test_message_delivery`、`test_channel`、`test_message_receipts`、`test_mixed_messaging`、`test_leader_channel_http`、`test_channel_digest(_codex)`、`test_cards_notices`、`test_notice_noise`、`test_goal_messages`、`test_codex_tools` 共 209 例通过；钩子相关 `test_core`、`test_hook_project`、`test_inactive_runtime`、`test_session_ops`、`test_trust_mode`、`test_workspace_activity` 165 例通过；`test_channel_runtime_ui.js` 通过。三条既有断言因新增 `delivery` 字段改写（`test_channel` 两处、`test_leader_channel_http` 一处，旧断言只缺新字段）。

## 7. 待拆分后施工（`web.py`、`chat.py`、`frontend/app.js`、`frontend/chat.js`、`frontend/channel.js`）

阶段二拆分合入后按顺序做；每条先补失败测试。模块名按 [拆分边界](SPLIT_BOUNDARIES.md)：`deliver_notices` / `settle_channel_mentions` → `web_service/workers.py`；`_send` / `_deliver_messages` → `chat_delivery.py`（`DeliveryMixin`）；`/api/channel`、`/api/messages` → `web_api/board.py`；`renderChannel` → `frontend/app/45-todos-channel-all.js`。

1. **P4 代发“回复在私聊里”要有回合证据**（`web_service/workers.py` 的 `settle_channel_mentions`）：只有在 leader journal 里、承载该通知的 `leader_notice_batch` 输入 `accepted` 之后出现**成功结束的回合**（`result` 非错误、且有助手正文）才代发；回合失败、被停止、进程退出时保持 `await_reply=1`，徽章显示“已送达，未回复”。测试：通知已送出 + 进程退出 / 回合报错 → `settle` 不发。
2. **P5 Codex 摘要两阶段**（`chat_delivery.py` 的 `_send`）：`channel.summary` 拆成“预览”（算出文本和目标游标，不写库）与“确认”（输入 `accepted` 后推游标）；`rejected` / `unknown` 不推。Codex 路径不传 `carry_pending`（托管会话的点名由通知投递负责）。
3. **外部运行的 leader 排队原因可见**（`web_service/workers.py` 的 `deliver_notices`）：遇到外部进程时，在通知上记录等待原因（复用 `events` 的 `leader:notice_blocked`，或给 `leader_notices` 加 `waiting_reason` 列），`_message` 的 `delivery` 附 `reason`：“对方在外部窗口运行，下次提示时送达”。
4. **用户在网页回复某条发言**（`web_api/board.py` 的 `/api/channel`）：接受 `reply_to`，后端已支持用户回复 Leader 发言时按用户点名通知原帖作者（清除停止标记、`await_reply=1`、私聊显示为用户气泡）。
5. **前端交流区重做**（`channel.js`、`frontend/app/45-todos-channel-all.js`、`style.css`、手机样式）：按第 8 节定稿实现徽章、引用脉络、私聊直达；`mentionHTML` 改为渲染所有点名与 `notified` 的 `delivery`，不再只看用户发言。
6. **私聊气泡投递状态**（`chat.js`）：用户在私聊里看到的 `crew send` 消息、交流区转来的用户原话，显示同一套徽章；数据取 `peer_messages` 回执与 `leader_notices`。
7. **侧栏 / 头部排队提示**（`app.js`）：`/api/state` 已有 `notices_pending`，leader 行显示“N 条通知排队中”，点击打开交流区并定位。
8. **事件**：通知送出、钩子认领、回复产生时写 `leader:notice_sent` / `channel:replied` 事件，供增量事件流推送徽章变化（见第 9 节）。

不依赖拆分、可在本会话后续直接做的：

- **P6** PreToolUse 钩子也认领外部 leader 的待送交流区通知（与 `peer_messages` 同一热路径：先只读检查 `leader_notices` 有无待送，再写库认领），使外部 leader 回合中途也能收到点名。是否打断正在进行的工作需主会话拍板；默认建议做，和私聊消息行为一致。

## 8. 交流区界面重设计（定稿）

视觉稿由 CLI Claude Code（claude-dispatch，`claude-opus-5-5`，effort high）产出，主题会话看过 6 张截图后验收定稿。原型、截图与逐项规格在 [`prototypes/channel-delivery/`](../prototypes/channel-delivery/README.md)（`DESIGN.md` 为色值与尺寸全表）；前端实施并验收后整个目录删除。

| 视口 | 截图 |
| --- | --- |
| 1280 | `prototypes/channel-delivery/shots/1280-light.png`、`1280-dark.png` |
| 758 | `758-light.png`、`758-dark.png` |
| 375 | `375-light.png`、`375-dark.png` |

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

## 10. 真实 AI 实测计划（拆分后统一做）

隔离 `--home`、独立端口、临时项目目录；CC 用 `claude-haiku-5-5`，Codex 用 `gpt-6-luna`（reasoning low）。逐条记录模型、实际行为、不符合预期之处。

1. 两个 leader（CC + Codex）：A 在交流区 `@B` 提问，B 正忙 → A 的 `crew say` / `crew_say` 回执应为“已排队”，A 不应据此说“B 已回复 / 已知道”。
2. B 空闲后收到通知并 `--reply-to` 回答（不写 `@A`）→ A 被叫醒并收到“回复了你的交流区发言”；A 自己回复自己时不被叫醒。
3. 外部窗口运行的 CC leader 被点名 → 用户在该窗口打字时，提示词里出现“以下点名之前没送到你这里”，且只出现一次；交流区徽章变为“已送达”。
4. `crew send --kind task` 后对方 `--kind result --reply-to` → 发送方查回执为 `replied`；`information` 始终 `archived`。
5. 用户在交流区点名外部 leader → 不出现 Crew 代发的“回复在私聊里”。
6. （第 7 节 P4 落地后）leader 回合失败或被停止 → 不代发“回复在私聊里”。

## 11. 已知限制

- Codex 路径的摘要仍在输入接受前推游标（P5），到拆分后修。
- 钩子认领与网页批次之间理论上仍有一个窄窗口：网页服务读出待送通知、尚未写 journal 批次时，外部会话恰好提交提示词。此时网页服务因该 leader 在外部运行本就不会发送（`leader:notice_blocked`），批次不会产生，不会重复；若该 leader 恰在同一刻从外部退出并被网页服务接管，可能重复一次。概率极低，拆分后可让网页服务在发送前用同样的条件更新先认领再发送来消除。
- 外部会话中途（PreToolUse）仍收不到 Leader 通知（P6，待拍板）。
