# Crew 后端接入与隔离体验

接口字段以 [共享契约](CREW_API_CONTRACT.md) 为准；验收和当前基线以 [执行记录](CREW_IMPLEMENTATION_EXECUTION_2026-10-28.md) 为准。下文不会启动或升级正式 Crew，也不要求用真实 AI 开发前端。

## 前端立即可用的真实 API

在仓库根目录运行，HOME 必须为全新空目录：

```powershell
python plugin/tests/serve_task_api.py --home C:\Temp\crew-ui-api-demo --port 0
```

首行 JSON 给出 `url/home/task_id/project/project_root/source_version/synthetic`。本服务用当前真实 Crew HTTP、数据库、权限、报告、附件和任务逻辑，载体回复明确标为合成；不启动真实 Claude/Codex，不读取正式原生历史或机器人账号。普通项目目录在 HOME 外的同级唯一 `crew-api-project-<UUID>` 目录创建，绝对位置见 `project_root`，退出保留用户编辑文件，不生成 `.git`。已有非空 HOME 或保留端口 `47811`、`7122` 会被拒绝。

服务保持在此终端，Ctrl+C 退出。再次运行请换一个新的空目录；这不是导入或复用正式数据的入口。夹具仅模拟 Claude stream-json；Codex 原生协议由测试夹具独立验证，不在服务断线时切到模拟数据。

前端可以将 URL 配为开发 API base URL，使用开发代理保持同源；代理的 Host/Origin 应匹配实际本机服务，不修改生产来源规则。也可直接使用此后端提供 `plugin/crew/web/` 静态文件。浏览器核心操作不要依赖 `window.crewDesktop`；本地打开目录由桌面桥接或显示可复制路径处理。

示例读取：

```powershell
$crewApi = '粘贴首行 JSON 的 url'
$crewTasks = Invoke-RestMethod "$crewApi/api/tasks"
$crewTask = $crewTasks.tasks[0]
Invoke-RestMethod "$crewApi/api/task?task_id=$($crewTask.id)"
```

发送讨论（只读讨论限制由后端执行；夹具不调用真实 AI）：

```powershell
$crewBody = @{
  goal_id = $crewTask.id
  session_id = $crewTask.leader_session_id
  text = '先聊清楚完成标准'
  message_id = [guid]::NewGuid().ToString()
} | ConvertTo-Json
Invoke-RestMethod "$crewApi/api/chat/send?project=api-demo" -Method Post `
  -Headers @{'X-Crew'='1'} -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($crewBody))
```

SSE 地址为 `/api/chat/events?project=api-demo&goal_id=<task.id>&session_id=<人员ID>&after=0`，断线按最后 seq 重连。切执行代次先读 history，再按新 execution_id 的序号恢复。

按任务列表、对话/history/SSE、两种停止、模式/设置、个人事项、报告/附件的顺序接入。所有按钮先对真实 API 验证，错误保留用户草稿；HTTP 409 不覆盖。模式开关只作用 task；配置权限与模式分开；模型/原生权限在 next_launch 生效，Crew 提示词在 next_turn 生效。原生能力来源不明时显示未知，并允许用户配置实际模型。

成员重要补充可在 `/api/chat/send` 加 `affects_task:true`；后端从真实 journal 保留原话，向同任务 leader 转达一次。普通成员问答省略此字段，避免重复播报。详情的 `user_forwards` 给来源和送达状态。

## 迁移、导出与恢复

任务 ID 直接沿用 goal ID，运行事实仍在 `crew.db`；新字段在同库扩展表，不建立第二任务引擎。已有目标按需映射成 manual 模式；旧 leader trust 值只保留来源，不变成新任务的自动授权。旧总会话继续独立可读。

`/api/workspace-data/` 是历史迁移/导出/导入接口，`/api/storage/` 是空间清理接口，不能混用。迁移通过现有 StorageService 暂存、校验和回执；任务扩展以既有 artifact 对象携带真实报告、附件和文件快照，不直接修改 storage-v2 v2 schema 摘要。详见 [既有存储说明](storage-v2/implementation.md)。

先用该工作区的完整导出生成 `.crewpack`，再做目录重绑或版本恢复。包导入绑定新目录后仍为停止/未授权，不恢复原 native ID，也不发送旧通知。关联的外工作区只保留明确引用；共享 TODO 只携当前任务明确引用的操作切片，不夹带外项目聊天。

阶段版本通过 `/api/tasks/versions` 保存真实字节；恢复先 preview，再提交同一 expected_files 和稳定 request_id。冲突返回 409、保留当前文件及旧版本内容，GET `/api/tasks/version-file` 可读取目标/基准版本。恢复不执行整库 reset 或整目录覆盖；Git commit 只作为可核对证据，不替代文件对象。

新工作区迁移前应保留原 home 的独立备份；不要在运行中的正式数据库上手工执行设计 SQL。合成历史、失败中断、重复迁移、导入与实际内容恢复已有隔离测试，执行记录列出实际范围；这些验证不等于正式用户库已经迁移。

## 独立后台

当前后台由 HOME 级 Supervisor 持有，桌面退出界面只断开连接；后续桌面/网页复用同一服务和数据。独立启动也可运行：

```powershell
python plugin/crew_supervisor.py --home C:\Temp\crew-backend-private --port 0 --isolated-sources
```

这是隔离源码服务，没有注入假载体，验收时不要对它发送启动真实 AI 的请求。前端日常开发用上一节夹具即可。正式服务切换需另行安排。

GET `/api/admin/service` 查询当前实例是否有可停止的管理员；POST `/api/admin/service/stop` 只向管理本服务 PID 的实例发停止请求。退出前端、停当前对话、停任务、停整个后台是四种不同操作。烟雾测试仍回收其自有后台。

## 远程入口接入

代码和隔离代理已具备登录、会话、允许来源、SSE 与文件归属验证；尚未接入真实 Cloudflare 账号、域名或公网。前端登录页使用 GET `/api/auth/session` 查询会话，POST `/api/auth/login` 登录、POST `/api/auth/logout` 退出。

截至 2026-10-28，官方发布应用流程要求 Cloudflare 账号、托管域名和运行 cloudflared 的执行机；公开隧道本身不要求付费的 Access 方案，若采用 Access 身份策略则按该账号的实际服务条件配置。[官方开始指南](https://developers.cloudflare.com/tunnel/get-started/)、[发布应用说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/)。

外部接入步骤：

1. 用户提供自己的账号/域名，在执行机创建命名隧道，服务目标为所选 HOME 的真实 loopback URL；不把服务绑定到公网网卡。
2. 通过本机 `/api/remote/configure` 配置 HTTPS origin、口令和当前 version。口令只保存加盐摘要；已登录远程修改还需 current_passphrase。配置文件不随工作区导出。
3. 隧道保留公网 Host，或将 httpHostHeader 明确设为同一允许域名；不能改为 localhost。来源参数见 [官方 origin 参数](https://developers.cloudflare.com/tunnel/reference/origin-parameters/)。不要靠 X-Forwarded-* 头授予本机身份。
4. 完成前端登录接入后，经实际 HTTPS 入口验收未登录拒绝、登录、任务直达、SSE、文件读取、退出登录及重连。关闭执行机时显示断连，重连不能重复提交操作。

只剩账号/域名部分不能由合成数据代替；当前没有可交付的公网链接。登录保护使用 HttpOnly/Secure/SameSite=Strict cookie、允许列表和请求头校验；发出的在途字节无法撤回，后续 SSE 批次及重连会重新校验会话。
