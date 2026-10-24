# QQ 与 Lark 远程交互

目标是让用户知道自己正在和哪个项目的哪个 leader 交流，并能从原消息直接接着聊。Crew 会话 ID 是路由身份；Claude / Codex 原生 ID 不参与远程寻址。

## 当前实现与验证边界

QQ 已有真实传输实现，本次补齐以下行为：

- `/projects` 或 `/p` 查看项目；`/project 项目名` 选择项目；再用 `/leader 名字` 选择该项目中的 leader。同名 leader 不会跨项目猜选。
- `/leader` 继续列出所有 leader，`/leader 2`、`/l 2`、`/2` 继续按原持久编号选择。已有编号不会因为排序、优先级或项目选择而重分配。
- `/where` 或 `/w` 确认普通文字、图片将发给谁。切换确认和每条发送回执都带项目及 leader 名称。只选择项目时，不会自动挑第一个 leader。
- 引用已保存的 leader 发言或回复，会发给原消息的 leader，且不改变普通聊天的当前选择。原 leader 已归档时不会回退发送给另一个 leader。引用一条涉及多个 leader 的汇总消息时，要求明确选人。
- 拍板消息保存发送时的完整快照，包含原问题、选项、推荐、阻塞状态及结构化摘要。新按钮携带快照摘要；题目修订后，旧按钮和旧引用不会被映射成新选项。
- 最终快照检查、保存答复及 leader 通知在同一个数据库写事务中完成，避免 CLI 同时修订题目造成检查后再误存。
- 历史 `题号:选项序号` 按钮只在题目未修订、已发送快照均仍与当前题一致时兼容接受。新的 `题号:选项序号:快照摘要` 按钮还必须对应一条实际已发送记录。
- 结构化拍板在 QQ 显示简短标题、为什么需要用户决定、推荐理由及每个选项的后果。

实现位于 `plugin/crew/notifications.py`。验证使用临时 Crew home、FakeQQ 和替身模型进程；本轮没有读取正式账号秘密、修改远程配置或发送真实消息。

Lark **尚未接通**。本机已安装 `lark-cli`，但安装 CLI 不等于 Crew 专用机器人已配置、订阅生效或消息可达。下面的共享路由层与 Lark adapter 是后续设计，不应在产品中标记为已连接。

## 可复用的远程路由层

QQ 当前仍由单一主人的 Inbox 管理。增加 Lark 时，应抽取消息归一化之后、运行时调用之前的路由和投递记录；不把 QQ 网络协议直接复制成第二套业务逻辑。

以下为接口约定伪代码，尚未实现共享 router：

```text
Envelope = {
    "channel": "qq" | "lark",
    "account_id": str,       # 当前机器人/应用的稳定标识
    "event_id": str,
    "actor_id": str,         # 平台认证过的操作者 ID，不用显示名作为权限
    "chat_id": str,
    "thread_id": str | None,
    "reply_to": str | None,  # 平台消息 ID / 已解析的引用标识
    "text": str,
    "attachments": list,
    "action_token": str | None,
}

router.resolve(conn, envelope) -> Intent | NeedsSelection
router.receive(conn, envelope, host_callbacks) -> DispatchResult
router.select(conn, scope, project, leader_session_id) -> Target
router.record_outbound(conn, receipt, target, item_snapshot) -> None
```

这里的 `scope` 是 `(channel, account_id, actor_id, chat_id, thread_id)`，不能共享一个全局“当前 leader”。收件项目和 leader 最终均从数据库校验；消息正文、卡片参数或模型输出不能改写平台操作者身份。

路由按以下顺序解析：

1. 已验证且仍有效的动作 token。
2. 引用消息在出站记录中绑定的项目 / leader / 业务事项。
3. 当前 thread 的明确绑定。
4. 用户显式指定的项目和 leader。
5. 同一 scope 中已确认的普通聊天选择。

有多个候选或已知目标失效时返回 `NeedsSelection`。不要选择第一个项目、最近启动的 leader，或把已经识别的引用悄悄回退到另一个当前 leader。

`Intent` 只承载明确操作：选择聊天目标、给 leader 发消息、答复拍板、完成人工待办、取消人工待办、查看状态。删除、部署、Git 操作等业务执行继续由 Crew 会话与其现有规则处理。

### 宿主接口

```python
submit_user_message(
    leader_session_id, text, images,
    source="qq" | "lark", identity=stable_inbound_identity,
) -> {"ok": bool, "queued": bool, "message_id": str}
```

它应复用现有 chat 队列和会话唤醒能力。不能用 `messaging.queue(sender_id="user", ...)` 代替：该模块的发送者必须是真实 Crew 会话。

拍板和人工待办分别复用现有领域 API、版本检查及所属 leader 通知链；adapter 负责平台消息与回执，不能重新实现一套“完成”“取消”规则。人工待办取消仍必须原因，操作撤销仍要防止覆盖后续修改。

回复任务保存来源 event / message，避免主动转发 finished turn 时又把同一次远程问答发第二遍。普通进展可按用户关注的项目或 leader 汇总；需要用户处理的新事项按现有通知策略呈现，不增加无变化的定时催促。

### 持久化建议

后续迁移应明确增加以下表，而非静默扩展现有 QQ 单账号字段：

| 表 | 必须保存的关系 |
| --- | --- |
| `remote_bindings` | 平台账号、允许的用户、可访问项目和接收会话 |
| `remote_selections` | scope 到项目、Crew leader ID 的选择 |
| `remote_outbound` | 平台消息 / 引用 ID 到项目、leader、业务事项、发送快照 |
| `remote_inbound` | `(channel, account_id, event_id)` 唯一；接收、排队、处理、失败状态 |
| `remote_actions` | 不透明 token 到具体操作、接收者、业务快照 / 版本、过期时间 |

同一事务保存入站去重记录和待处理意图；网络发送放到可追踪的 outbox。回调先及时确认收到，再处理领域操作和发送回复。网络超时后的 `sending` 代表结果不确定，不能直接记成功，也不能无条件重发。

卡片 token 应绑定实际已发送的记录、允许的操作者、项目、事项、版本 / 快照摘要；用户点击后重新验证状态。只携带可复用的选项下标不足以证明用户选择了当前选项。

## Lark 的交互形态

优先使用应用机器人身份接入常驻通道。一个项目可以绑定一个项目群，每个 leader 使用一条独立 thread；用户回复 thread 时自然定位 leader。同一个私人聊天中也可使用项目和 leader 选择卡，仍按 scope 保存状态。

Lark 官方支持通过回复消息创建 thread，并在接收消息事件中取得 thread ID；应以该 ID 绑定路由，不能仅依赖消息中出现的名称。[Thread Introduction](https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/thread-introduction)

桌面端可以使用官方 SDK 的 WebSocket 长连接接收消息，初始化需要应用 ID 和 Secret；不必为此先暴露本地 Crew HTTP 服务。[Receive events through WebSocket](https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-)

也可评估官方 Channel SDK，把连接、事件解析、流式回复及卡片交互交给传输层；Crew 继续持有路由、授权和白板业务状态。[Lark Agent integration overview](https://open.larksuite.com/document/mcp_open_tools)

`lark-cli event consume` 适合诊断已有应用和验证事件形状。常驻实现优先一个 SDK 连接注册消息及卡片事件，避免把多个 CLI 进程当成 Crew 的业务状态机。

## Lark 接通前的清单

当前未验证以下配置；不能把“未知”显示成“已授权”：

- Crew 专用应用 ID、Secret 的本地凭证引用，以及 Feishu / Lark 租户域名。秘密应由本地凭证层提供，不进入提示词、数据库事件或仓库。
- 应用的机器人能力、发布 / 可见范围、目标群成员资格。
- 机器人的发消息权限（`im:message:send_as_bot`），以及所选私聊 / 群聊接收事件的对应权限。需要读回原消息或卡片时，再配置对应只读权限；不为纯聊天默认申请用户个人资源权限。
- `im.message.receive_v1` 消息订阅，以及 `card.action.trigger` 卡片回调配置。能够建立连接并不证明这些订阅生效。
- 允许操作者的稳定 open ID、目标 chat ID / thread ID，以及首次绑定校验流程。不要根据用户显示名或任意转发文本认定身份。
- 重连、入站去重、卡片重复点击、失效 token、陈旧题目、归档 leader 和跨项目目标的替身测试。
- 在用户指定的测试聊天完成一次主动发来的消息和一次测试卡片操作，验证真实收发回执、重启后的路由与去重，再标记为已接通。

传输适配器的能力描述应显式说明是否支持 thread、引用、卡片、编辑消息和回调确认。QQ 与 Lark 共用领域路由，不假定两者具有完全相同的消息能力。
