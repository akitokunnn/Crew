# 飞书通知与远程回复

Crew 通过本机已有的 `lark-cli` 机器人身份，把离开时需要处理的事发到你的飞书私聊。机器人鉴权和长连接由 CLI 管理，Crew 不另建飞书 SDK 连接，也不在仓库保存应用密钥。当前复用应用 `cli_aabea57f7538dcef`；收件账号保存在本机配置中。

## 本机配置

在 Crew HOME 下保存 `feishu.json`。默认 HOME 是 `%USERPROFILE%\.crew`；命令指定 `--home` 时，以指定目录为准。以下是字段示例，`owner_id` 必须换成本机已经确认的收件人 open_id，`since` 换成实际启用时间：

```json
{
  "enabled": true,
  "owner_id": "ou_接收人的open_id",
  "since": "2026-10-22T00:00:00.000Z",
  "manual_away": false
}
```

| 字段 | 含义 |
| --- | --- |
| `enabled` | 是否启动飞书接收与通知。缺省不启用。 |
| `owner_id` | 唯一收件人。仅接受此人发来的机器人私聊文字或 Crew 卡片操作。 |
| `since` | 错误、等待提示和阶段报告的通知起点，使用带时区的 UTC 时间。尚未答复的待拍板仍属于当前待办，不因创建时间较早而忽略。 |
| `manual_away` | 网页“我已离开”开关的持久状态，服务重启后保留。 |

配置在服务启动时读取；手工修改后需重新启动 Crew 网页服务。“我已离开”开关会直接保存 `manual_away`。

可先检查已有 CLI 机器人身份，不需要为机器人收发消息重新进行用户 OAuth 登录：

```powershell
& "$env:APPDATA\npm\lark-cli.cmd" --profile cli_aabea57f7538dcef auth status --json --verify
```

本机已核对机器人能力、私聊发送/接收及消息更新权限，普通消息事件 `im.message.receive_v1` 已订阅，应用使用长连接。若 CLI 报权限不足，以实际错误为准；修改应用权限或订阅需要在飞书开放平台处理。

## 什么时候通知

网页标记“我已离开”，或本机桌面代理返回 `locked=true` 时，Crew 视为离开。锁屏查询只向 `http://127.0.0.1:47800/api/status` 发带本机认证头的状态请求，读取 `%USERPROFILE%\RemoteDev\desk_token`，不会启动桌面工具或操作窗口。代理的安全桌面等情况也可能被判为锁屏；这个状态不等于精确的人在不在电脑前。

桌面代理无法读取时，网页会显示错误，可使用“我已离开”开关。锁屏不会暂停通知服务；睡眠、休眠、注销或关机期间不能接收消息。

离开时，尚未通知的待拍板、需要人处理的等待/错误、失败的 Codex 任务和 leader 阶段报告会进入待发送队列。首条事项等待约 30 秒，把这一时间段的多条事项合并为一条消息。合并卡的每题都带工作区、leader 和待拍板编号；普通进展也保留自己的工作区与 leader 标签。发送前已经解决的事项不再发送。

发送记录保存在本机数据库，用事项编号去重，并使用 CLI 幂等编号。超时或错误会保留投递状态，不会把无法确认的发送直接当成功，也不会自动重复发送。卡片总大小超过 30 KB 会报错，不会静默截掉问题或拆成多条。网页提示通知错误时，应查看本机记录与服务日志。

## 答复待拍板

可以点击已启用的卡片选项，或回复这条卡片消息：

```text
#42 先补测试，再继续发布
```

同一张卡有多个问题时，用编号明确指向某一题。自由文字答复会保留原文，不要求只能选预设选项。机器人会检查原消息是 Crew 发出的、待拍板属于该工作区和收件人身份，才保存答案。

“答复已保存”表示答案已经进入 Crew 白板；“等待会话下一轮接收”表示会话尚未读取；“已交给会话”表示已完成交付，不代表任务完成。卡片更新后，已答题移除按钮，其余问题仍可作答。最终进展以会话回复或报告为准。

### 卡片按钮当前需要启用回调

2026-10-22 本机实际启动 CLI consumer 核查结果：普通消息事件达到 ready 并正常退出；`card.action.trigger` consumer 以退出码 2 报 `failed_precondition`，明确指出该回调尚未在应用后台订阅。当前按钮回调尚未通过真人验收。

因此，回调未就绪时发送的卡片会显示文字选项和“回复 #编号 答复”，不显示点击后无法生效的按钮。文字私聊回复可以作为当前答复方式。要使用按钮，需要应用管理员在[飞书开放平台](https://open.feishu.cn/app)进入该应用的“事件与回调 → 回调配置”，启用 `card.action.trigger` 长连接回调；按平台提示完成发布后，再让 Crew 重新建立 consumer，并由真人点击一张新卡验证保存、更新和下一轮交付。修改订阅没有在本轮自动执行。

CLI 接收回调只证明事件到达，不等于 Crew 已保存答案。旧卡的运行时显示保持原样；回调真正 ready 后，新卡才带按钮。

## 与多个 leader 对话

只有一个未归档 leader 时，可以直接给机器人发消息。有多个时，写清目标：

```text
@Crew/主控 帮我看看今天的进展
```

名字唯一时也可用 `@主控 你的消息`。这里的 `@工作区/名字` 是 Crew 的文字路由标记，不是飞书通讯录里的 @ 提及。或者直接回复该 leader 发来的原消息。若一条合并消息涉及多个 leader，机器人会要求指定目标，不会随意选一个。

网页后台管理的 leader 可以继续使用自己的输入流；空闲且已经停止的网页会话可按原会话恢复。忙时消息排队，聊天记录标记来源为飞书；离开时，将本条新消息对应的回复回传。若同一会话已在 VS Code 或其他进程运行，Crew 不会再启动一份。

当前原生 VS Code/终端会话的路径是：把消息写成发给该会话的交接，并明确回复“下一轮收到”。它需要在该会话下一次提交提示或启动时由 Crew hook 取走，不会因为飞书来信而立即开始一轮，也不保证自动回传原生会话的最终回复。网页会话转到 VS Code 后也受原生会话边界约束。

## 服务与登录自启

普通启动只需运行网页服务，不需要额外启动一个通知进程：

```powershell
crew web --port 47811
```

完成代码合入主检出后，可登记当前 Windows 用户的登录自启：

```powershell
crew autostart install
crew autostart status
```

安装只登记任务，下次登录后隐藏启动，不会立即启动或重启现有服务。任务使用当前用户身份，不保存 Windows 密码，日志在 Crew HOME 的 `web-autostart.log`。默认端口已有 Crew 服务时会保留它，不重复启动。取消自启使用 `crew autostart uninstall`，同样不会停止当前服务。

每种飞书事件各有一个 `lark-cli event consume` 子进程，复用 CLI 同一个事件 bus。CLI 以 stderr 的 ready 标记报告就绪，Crew 据此决定是否显示卡片按钮；正常关闭自己的 consumer 时关闭 stdin 并等待退出，不停止其他程序共用的 bus。不要同时为同一应用另开独立 SDK WebSocket 客户端：飞书多连接按集群分发，可能让事件被另一连接取走。

## 验收范围与原生入口调查

隔离测试使用模拟进程和飞书客户端，覆盖合并、去重、账号/原消息校验、编号路由、保存与交付状态；它们不代替真人飞书收件、卡片点击、锁屏及完整对话验收。消费者的 ready 和正常 EOF 退出已经做过有限实机检查；按钮订阅失败已单独记录。

本机 Claude Code 2.1.306 已提供官方跨会话收件入口：hook 可取得 `CLAUDE_CODE_MESSAGING_SOCKET` 和 `CLAUDE_CODE_MESSAGING_TOKEN`，Windows 使用认证命名管道。安装包带有 auth 行加 user 消息行的发送示例；本轮只读核查，未向任何现有会话发消息。

这个入口不会在同一连接返回消息接收 ACK；当前 `UserPromptSubmit` 和 `Stop` hook 输入也没有共同的消息/轮次编号。直接把某次 Stop 当作某条飞书来信的最终答复，会受到中断、其他提示和 Stop hook 继续执行的影响。现有实现因此保留原生交接边界，没有把管道写入描述成可靠的完整远程对话。

官方资料：[飞书消息发送](https://open.feishu.cn/document/server-docs/im-v1/message/create)、[飞书长连接事件](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)、[Claude 会话收件入口](https://code.claude.com/docs/en/cross-session-messaging#the-sessions-inbox-socket)、[Claude hook 输入与触发时机](https://code.claude.com/docs/en/hooks)、[Claude Channels 的启动要求](https://code.claude.com/docs/en/channels-reference)。
