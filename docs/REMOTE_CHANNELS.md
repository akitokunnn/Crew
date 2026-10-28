# QQ 与 Lark / 飞书消息渠道

打开 **设置 → 消息渠道**。桌面首页和工作区设置使用相同入口，QQ 与 Lark / 飞书可同时启用，不需要手工编辑 JSON 或重启整个 Crew。

## 新用户连接流程

### QQ

1. 在 [QQ 开放平台](https://q.qq.com/)创建机器人，启用单聊消息和事件接收；测试阶段把自己的 QQ 号加入测试范围。
2. 在 Crew 选择 QQ，填写机器人的 App ID 和 App Secret，点击“保存并连接”。
3. 等待状态变为“等待绑定”，复制页面上的 `/bind 绑定码`，用自己的 QQ 账号私聊机器人。收到绑定回执后，页面自动更新。
4. 在 QQ 机器人会话中打开“允许主动发送”。点击 Crew 的“发送测试通知”，在手机会话中确认收件。

已有 QQ 主人绑定会保留。新连接不会把第一个任意私聊者自动设为主人；只有输入当前绑定码的账号才能完成绑定。

### Lark / 飞书

1. 在 [飞书开放平台](https://open.feishu.cn/app)或 [Lark 开放平台](https://open.larksuite.com/app)创建企业自建应用，添加机器人能力。中国版选择“飞书”，国际版选择“Lark”。
2. 开通应用身份权限 `im:message:send_as_bot` 和 `im:message.p2p_msg:readonly`，分别用于发送消息和接收用户私聊。
3. 将 App ID 与 App Secret 填入 Crew，点击“保存并连接”。再到平台“事件与回调”，选择“使用长连接接收事件”，订阅 `im.message.receive_v1` 并保存。无需公网回调地址，也不依赖本机 lark-cli 登录。
4. 创建并发布应用版本，将自己加入可用范围。打开机器人私聊，发送 Crew 页面提供的 `/bind 绑定码`。
5. 收到绑定回执后，在 Crew 点击“发送测试通知”，确认手机会话收到消息。

当前支持人类用户与机器人的**私聊文字、命令、引用回复、拍板和通知**。图片、文件、群聊、thread 路由和交互卡片按钮不在当前能力内；拍板请引用机器人发出的原消息，输入答案。

平台说明：[长连接订阅](https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-)、[接收消息事件](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive)。传输依据 [官方 Python SDK 长连接实现](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/ws/client.py)和 [protobuf 定义](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/ws/pb/pbbp2_pb2.py)，Crew 实现使用 Python 标准库。

## 如何开始对话

- `/projects`：查看项目；`/project 项目名`：选择项目。
- `/leader 名字`：选择该项目的 leader；`/leader` 列出带稳定编号的 leader，`/1` 等编号入口仍可用。
- `/where`：确认普通消息将发往哪个项目、哪个 leader。
- 普通文字发给当前选择的 leader；引用机器人已发送的消息，会回到原消息所属的 leader，不改变普通聊天的当前选择。
- `/decisions`：查看未挂起拍板；引用原拍板消息输入答复。QQ 还可使用原有按钮。
- `/todo`：查看需要亲手做的待办；`/h`：帮助；`/back`：回到电脑前。

同名 leader 不跨项目猜选，已归档的原收件人不会回退到另一个 leader。拍板保存原问题与选项快照；题目修改后，旧按钮或旧引用会要求先查看新内容。

## 连接管理与状态

“保存并连接”立即应用凭证。现有 Secret 留空表示保留，不会回显；更换 App ID 时必须填写新 Secret，并重新绑定。Lark 与飞书切换也需要重新绑定。

“重新连接”保留绑定和聊天目标。“断开连接”停止当前渠道收发，保留凭证。“重新绑定”经页面内确认后生成新码，原主人失去该渠道的控制权。

- 待配置：尚未保存凭证。
- 连接中 / 重连中：传输尚未就绪。
- 等待绑定：平台长连接已建立，需私聊发送绑定命令。
- 已就绪：平台连接建立且主人已绑定。测试通知被平台接受后，仍需在手机确认收件。
- 连接异常 / 发送被拒绝：查看页面的实际错误；检查凭证、权限、发布范围、订阅和网络后重连。
- 已断开：该渠道停用，凭证保留。

“我已离开”是本机共用状态。手动离开或检测到锁屏时，新增人工 TODO、待拍板、真实错误、明确发布的阶段报告与最终交付通过所选渠道发送。共享 TODO 只提醒一次；普通进度对话和结束句不主动外推。聊天问答回到发起渠道，不依赖离开开关。电脑睡眠或关机期间无法收发。锁屏检测不可用时可使用手动开关。

QQ 与 Lark 分别保存发送回执，一边成功不因另一边失败而重发。明确的平台拒绝最多自动重试两次，等待 5、10 秒；重启保留尝试次数和错误。网络接收状态未知时保留错误，不盲重发；已经处理的事项或换主人后的旧通知不重试。保存连接设置不会发送测试，测试通知需要单独操作。

## 本地存储与隔离

| 数据 | QQ | Lark / 飞书 |
| --- | --- | --- |
| 凭证与启用状态 | HOME/qq.json | HOME/lark.json |
| 主人、绑定码、当前项目和 leader | HOME/notify.json | HOME/lark-notify.json |
| 投递、入站去重、回复队列 | notification_* 表 | lark_notification_* 表 |

两个渠道独立保存主人、当前项目、leader 编号和投递记录，平台恰好使用相同消息 ID 也不会互相去重或串投。更换账号或重绑会隔离旧的待回复记录。凭证只保存在所选 HOME，不返回设置读取接口，不写入聊天或日志。

## 实现与验证

`connections.py` 管理配置与线程生命周期，`notifications.py` 共用项目/leader 路由、拍板版本检查及回传队列；`qq.py` 与 `lark.py` 分别负责平台传输。API 为 `GET /api/connections`、`POST /api/connections/configure` 和 `POST /api/connections/action`；写操作沿用本机 Web API 的来源检查。

隔离测试覆盖保存、绑定、防抢绑、双平台隔离、失败回执、线程停止、重新连接、旧库升级及真实 Electron 表单。Lark 传输用本机替身服务验证 HTTPS 请求形状、二进制 WebSocket、心跳、ACK 与事件。未读取正式凭证或向真实 QQ / Lark 账号发送消息；实际租户权限与手机收件需在指定机器人中按上述流程确认。
