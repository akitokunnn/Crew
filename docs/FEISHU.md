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
| `current_leader_session_id` | 飞书私聊当前在和哪个 leader 说话，由 `/1`、`/2` 切换，Crew 自己维护。 |
| `leader_numbers` | 每个 leader 的全局编号，首次出现时按项目创建时间、项目名、会话开始时间分配；之后只追加，归档的 leader 保留号码不复用。Crew 自己维护。 |

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

## 私聊命令

所有项目的 leader 都在同一个机器人私聊里。不带 `/` 的文字发给当前 leader；以 `/` 开头的是命令，Crew 自己回答，不交给模型：

| 命令 | 作用 |
| --- | --- |
| `/h` | 帮助 |
| `/ls` | 列出所有项目的 leader：编号、`项目·leader`、状态、未读、待拍板，当前 leader 有标记 |
| `/1`、`/2`… | 切换当前 leader（编号来自 `/ls`，长期不变） |
| `/q` | 列出所有未答的待拍板，按 `项目·leader` 分组，带题号、提问者、推荐 |
| `/back` | 我回来了：关掉手动离开，并说明是否仍因锁屏被视为离开 |

还没选 leader 时发普通文字，机器人会提示先 `/ls` 再 `/1`，不会替你猜。leader 的回复总会回传到飞书，开头带 `项目·leader`；"我已离开"只决定 Crew 是否主动推送提醒，不影响对话回传。

网页后台的 leader 会收到这条消息（聊天记录里标"来自飞书"），忙时排队，空闲时发出；已经停止的网页 leader 会按原会话 ID 恢复。leader 若在 VS Code 或其他进程里运行，机器人回"这个 leader 不在网页后台运行，收不到飞书消息"，不会再起第二份。leader 一轮结束却没有文字时，回"（这一轮没有文字回复）"，不卡住后面的消息。

## 答复待拍板

直接发：

```text
#42 先补测试，再继续发布
```

不需要引用卡片，但题号必须是 Crew 发给你过的（推送卡片或 `/q` 列出过的）。自由文字答复会保留原文。答复保存后，Crew 给提问者的 leader 排一条通知，leader 立刻用会话间消息把答复转给提问的下属、叫它继续；提问者自己就是 leader 时直接通知它。提问者已归档或移出时不保存，并提示先恢复。

卡片按钮可用时也可以直接点选项；已答的题移除按钮，其余照旧可答。

### 卡片按钮当前需要启用回调

2026-10-22 本机实际启动 CLI consumer 核查结果：普通消息事件达到 ready 并正常退出；`card.action.trigger` consumer 以退出码 2 报 `failed_precondition`，明确指出该回调尚未在应用后台订阅。当前按钮回调尚未通过真人验收。

因此，回调未就绪时发送的卡片会显示文字选项和"回复 #编号 答复"，不显示点击后无法生效的按钮。要使用按钮，需要应用管理员在[飞书开放平台](https://open.feishu.cn/app)进入该应用的"事件与回调 → 回调配置"，启用 `card.action.trigger` 长连接回调；按平台提示完成发布后，再让 Crew 重新建立 consumer，并由真人点击一张新卡验证。修改订阅没有自动执行。

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

## 验收范围

隔离测试使用模拟进程和飞书客户端，覆盖命令、编号、路由、答题授权、leader 通知、回传与合并去重；它们不代替真人飞书收件、卡片点击、锁屏及完整对话验收。

官方资料：[飞书消息发送](https://open.feishu.cn/document/server-docs/im-v1/message/create)、[飞书长连接事件](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)。
