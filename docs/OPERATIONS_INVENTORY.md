> 来源：架构重审主题会话派 Codex `gpt-6.2-sol`（medium，只读）盘点，主控抽查 `web.py:2051` 缺失静态文件、`codex_tools.py:461` `crew_channel` 读写标记、`web.py:1842` GET 文档列表持写锁、`cli.py:520` 身份回退 `CLAUDE_CODE_SESSION_ID` 四条，均与源码一致。基线 `3fdfbec`。本文件是 [操作清单设计](OPERATIONS_REGISTRY_DESIGN.md) 映射表的事实底稿；行号只对该基线有效。

# Crew 操作入口全量盘点

**基线为 `claude/arch-review-afe4eb` 的 `3fdfbec`，桌面版本 `0.3.18`。** 当前 `web.py` 有 **50 个 GET API 路径、104 个 POST API 路径、76 个静态白名单条目**。远程监听另处理登录和退出。`codex_tools.py` 有 **31 个动态工具定义**，全局助手有 **10 个读动作、22 个写动作**。行号均来自本次检出的文件。盘点只读，结束时工作树干净，验证采用源码分派、参数注册和调用点交叉检查。

- **统一清单应登记业务动作，而非 URL。** `/api/goals/agent`、`/api/assistant/native-action`、`crew_goal`、`crew_personal` 都在一个入口上分派多个动作。CLI 与 Codex 的同一动作有时共享底层函数，有时走不同的授权和副作用链路。典型例子是拍板答复、任务转交、会话改名。`web.py:1384,1550`、`goal_api.py:112`、`cli.py:542,758`、`codex_tools.py:446,518,595`
- **HTTP 方法和工具的读写标记都需要复核。** GET 文档列表会刷新索引，GET 助手状态会确保助手存在，GET 标签库会登记旧标签，GET 用量会扫描入库。`crew_channel` 被工具门禁归为读，但会推进已读位置。`web.py:1842,1969,2316,2318`、`web_api/personal.py:12,59`、`personal.py:372`、`codex_tools.py:434,461`、`channel.py:255`
- **身份需要独立建模。** 本机入口限制来源，远程入口校验设备 Cookie 或 Bearer。`session_id` 常是操作目标，`caller_session_id`、`actor_session_id`、`execution_id` 才参与对应动作的调用者或执行权限校验。Codex 工具由宿主绑定身份。`web.py:2103,2232,1456,1501`、`remote_access.py:274,345`、`remote_routes.py:54`、`codex_tools.py:315,337,374`
- **当前有一项失配的静态注册。** `web.py:2051` 白名单列出 `/goal-versions-ui.js`，当前 `frontend/` 没有该文件。目标版本动作仍可从目标动作分派到达。`goal_actions.py:8,266`、`goal_artifacts.py:385`

以下 `web.py`、`cli.py`、`codex_tools.py` 及其他 Python 短文件名相对于 `plugin/crew/`。`F/` 表示 `frontend/`，`D/` 表示 `desktop/src/`。HTTP 表的“无”指当前前端及桌面范围内未找到该方法的直接调用方，CLI 和工具调用在各自表及第 7 节标注。读写按动作的业务副作用标注，连接数据库时通用的建库、迁移和远程设备活跃时间更新不重复计入各行。

## 1. HTTP 路由

本机所有 API 先过 `Handler._handle` 和 `_connection_access`，POST 还要求 `X-Crew: 1`、非空 JSON 和精确路径白名单。远程所有业务 API 先过设备认证。身份列的 `—` 表示无额外 Crew 会话身份参数，`目标` 表示该 ID 选定对象，`调用者` 表示参数参与调用授权，`主机` 表示 `_owner_access` 限本机或已认证远程桌面，再叠加远程拒绝清单。HTTP 分派本身不读取 `CREW_SESSION_ID`。`web.py:1995–2027,2093–2095,2103–2118,2208–2243,2473–2505`

### GET

| 方法 | 路径 | 处理函数与分支 | 读写 | 用途 | 前端或桌面调用方 | 会话身份或访问条件 |
|---|---|---|---|---|---|---|
| GET | `/api/access/context` | `Handler._dispatch` `web.py:2244`；远程 `remote_access.py:429` | 读 | 返回本机或远程客户端能力 | `F/access-context.js:45`；`D/core.cjs:426`、`D/main.cjs:130`、`D/launch-settings.cjs:134` | 远程设备 |
| GET | `/api/remote-access` | `remote_access.snapshot` `web.py:2247` | 读 | 查看远程访问设置和设备 | `F/app.js:1441`；`D/main.cjs:682` | 主机 |
| GET | `/api/workspace-data/history` | `storage.history` `web.py:2249` | 读 | 查询工作区可移植历史 | `F/storage-history.js:8`；`D/main.cjs:234` | 主机，`workspace_id` |
| GET | `/api/workspace-data/artifact` | `Handler._dispatch` `web.py:2255` | 读 | 读取历史版本资料 | `F/storage-history.js:10` | 主机，工作区及版本 ID |
| GET | `/api/workspace-data/blob` | `Handler._dispatch` `web.py:2258` | 读 | 流式返回历史附件 | `F/storage-history.js:42,48` | 主机，工作区及 blob ID |
| GET | `/api/workspace-data/download` | `Handler._dispatch` `web.py:2261` | 读 | 下载已导出的工作区包 | `F/storage-history.js:75`；`D/main.cjs:279` | 主机，下载 ID |
| GET | `/api/files/content` | `Service.resolve_file/read_file` `web.py:2266,2282` | 读 | 在 Crew 内读取文件或报告内容 | `F/chat.js:228,287` | 目标 `session_id` 或 `document_id`，本机 Host 或远程文件能力 |
| GET | `/api/files/download` | `Service.resolve_file/read_file` `web.py:2266,2276` | 读 | 下载文件 | `F/chat.js:232`、`F/documents.js:30` | 同上 |
| GET | `/api/admin/connection/plan` | `Service.connection_plan` `web.py:2285,375` | 读 | 检查连接变更涉及的会话 | `D/launch-settings.cjs:123`、`D/main.cjs:368` | —；远程拒绝 |
| GET | `/api/admin/chat` | `operations_chat.snapshot` `web.py:2287` | 读 | 读运维对话快照 | `F/app.js:1433` | —；远程拒绝 |
| GET | `/api/admin/config` | `operations_settings.snapshot` `web.py:2289` | 读 | 读运维配置 | `F/app.js:1434` | —；远程拒绝 |
| GET | `/api/admin/health` | `Handler._dispatch` `web.py:2291` | 读 | 读服务版本、进程、实例和工作线程状态 | `D/core.cjs:428`、`D/crew-updates.cjs:49`、`D/launch-settings.cjs:106,136,146`、`D/main.cjs:132,508`、`D/remote-backends.cjs:25` | — |
| GET | `/api/workspaces/activity` | `Handler._dispatch` `web.py:2300` | 读 | 批量读取工作区活跃状态 | `D/main.cjs:877` | — |
| GET | `/api/behavior-prompts` | `web_api/behavior_prompts.get` `web.py:2309` | 读 | `list`、`get`、`history`、`applied` | `F/app.js:1441`、`F/assistant.js:64`；`D/main.cjs:654` | 按范围传 `identity` 或 `scope_id` |
| GET | `/api/personal/notes/recovery` | `web_api/personal.note_recovery` `web.py:2311` | 读 | 查想法已有讨论及可恢复绑定 | `F/notes.js:284` | `note_id` |
| GET | `/api/personal/notes` | `web_api/personal.notes` `web.py:2313` | 读 | 列出想法 | `F/notes.js:47`、`F/personal-notes-ui.js:15`、`F/assistant.js:58` | — |
| GET | `/api/personal/tags` | `web_api/personal.tags` `web.py:2316`；`personal.py:372` | 读写 | 列标签并补登记旧标签 | `F/notes.js:47` | — |
| GET | `/api/assistant/state` | `web_api/personal.state` `web.py:2318`；`web_api/personal.py:12` | 读写 | 确保助手存在，返回助手和个人白板状态 | `F/assistant.js:42` | 服务端选全局助手 |
| GET | `/api/state` | `Service.state` `web.py:2321,646` | 读 | 读工作区主白板 | `F/app.js:1371` | — |
| GET | `/api/session-library` | `session_library.listing` `web.py:2323,2328` | 读 | 列出会话库 | `F/session-library.js:60` | — |
| GET | `/api/session-library/preview` | `session_library.preview` `web.py:2323,2327` | 读 | 预览会话库记录 | `F/session-library.js:71` | `scope`、`key` 选定对象 |
| GET | `/api/goals` | `goals.list_goals` `web.py:2333` | 读 | 列工作区目标 | 无 | — |
| GET | `/api/goal` | `goal_api.detail` `web.py:2335` | 读 | 读单个目标及执行上下文 | `F/goal-ui.js:114` | 目标 `id` |
| GET | `/api/goals/report` | `Handler._dispatch` `web.py:2330,2338–2347` | 读 | 读报告 HTML 或 JSON 元数据 | `F/goal-ui.js:13,242`、`F/stage-report-ui.js:10` | `goal_id`、`report_id` |
| GET | `/api/runtimes` | `Handler._dispatch` `web.py:2348` | 读，`refresh=1` 刷新目录 | 查原生载体、模型档位和可用状态 | `F/app.js:161,1431`；`D/main.cjs:659` | — |
| GET | `/api/connections` | `connections.snapshot` `web.py:2351` | 读 | 查连接器设置及状态 | `F/app.js:1145`；`D/main.cjs:678` | 主机 |
| GET | `/api/settings` | `Service.settings_state` `web.py:2353,998` | 读 | 查 AI、执行及声音设置 | `F/app.js:1431,1440`；`D/main.cjs:660` | — |
| GET | `/api/storage` | `storage_cleanup.snapshot` `web.py:2355` | 读 | 查存储、回收站和清理策略 | `F/app.js:1154`；`D/main.cjs:688` | 主机 |
| GET | `/api/project/settings` | `Service.workspace_state` `web.py:2357,1003` | 读 | 查工作区显示名、目录和修订号 | `F/app.js:1426`；`D/main.cjs:724` | — |
| GET | `/api/host/folders` | `host_workspace.folders` `web.py:2359` | 读 | 浏览主机目录 | `D/core.cjs:411` | 主机 |
| GET | `/api/projects` | `Handler._dispatch` `web.py:2361` | 读 | 列工作区及目录状态 | `F/global-rail.js:210`、`F/notes.js:59`；`D/core.cjs:182` | — |
| GET | `/api/documents` | `Service.document_list` `web.py:2366,1842` | 读写 | 刷新资料索引后返回文档 | 无 | 可选目标 `leader_session_id`、`goal_id` |
| GET | `/api/trust/report` | `trust_mode.report` `web.py:2369` | 读 | 导出 leader 授权报告 Markdown | 无 | 目标 `leader_session_id` 必填 |
| GET | `/api/comments` | `Handler._dispatch` `web.py:2374` | 读 | 列工作区留言，可按会话筛选 | 无 | 可选目标 `session_id` |
| GET | `/api/maintenance/jobs` | `maintenance.jobs` `web.py:2386` | 读 | 查看维护任务及归档边界 | `F/maintenance-ui.js:222`；`D/main.cjs:718` | 可选目标 `leader_session_id` |
| GET | `/api/maintenance/archive` | `maintenance.archive` `web.py:2392` | 读 | 查看已归档维护内容 | `F/maintenance-ui.js:273`；`D/main.cjs:718` | `job_id` 等对象标识 |
| GET | `/api/todos` | `todo.list` `web.py:2394` | 读 | 查工作区人工待办 | 无 | — |
| GET | `/api/channel` | `Service.channel_messages` `web.py:2398,1837` | 读 | 分页读交流区消息和摘要 | `F/channel.js:95,123,143,159` | — |
| GET | `/api/git` | `Service.git` `web.py:2402,945` | 读 | 查仓库、分支、请求和 Git 助手状态 | `F/git.js:143` | — |
| GET | `/api/domains` | `domains.snapshot` `web.py:2404` | 读 | 读工作区分区图 | `F/architecture.js:293` | — |
| GET | `/api/git/settings` | `Service.git_settings` `web.py:2407,971` | 读 | 读 Git 设置 | `F/app.js:1423` | — |
| GET | `/api/git/commit` | `Service.git_commit` `web.py:2409,989` | 读 | 查指定提交 | 无 | 提交 `sha` |
| GET | `/api/sounds/custom/file` | `Handler._dispatch` `web.py:2411` | 读 | 读自定义提示音 | `F/sounds.js:75`，URL 由 `event_sounds.py:63` 下发 | 音效 `id` |
| GET | `/api/session/avatar` | `Service.session_avatar` `web.py:2413,1922` | 读 | 读会话头像 | `F/leader-look.js:8` | 目标 `session_id` |
| GET | `/api/chat/skills` | `Service.chat_skills` `web.py:2416,1821` | 读，可刷新发现 | 列出聊天可用原生技能 | `F/chat.js:1132` | 目标 `session_id`，可选目标 |
| GET | `/api/chat/file` | `Service.chat_file` `web.py:2419,1926` | 读 | 读聊天附件 | `F/chat.js:659` | 目标 `session_id`，可选 `goal_id/execution_id` |
| GET | `/api/chat/history` | `Service.chat_history` `web.py:2424–2446,1812` | 读 | 读聊天历史及分页 | `F/chat.js:927,976,1201,1204,1216` | 目标 `session_id`，可选 `goal_id` |
| GET | `/api/chat/events` | `Handler._chat_events` `web.py:2447–2451,2120` | 读 | 流式订阅聊天事件 | `F/chat.js:883` | 目标 `session_id`，可选目标执行 |
| GET | `/api/usage` | `Service.usage` `web.py:2452,2463,1969` | 读写 | 扫描用量后汇总 | `F/app.js:913` | — |
| GET | `/api/usage/export` | `Service.usage`、`usage_cli.write_csv` `web.py:2454–2461` | 读写 | 扫描用量后导出 CSV | `F/app.js:906` | — |

`/api/state` 另被 `spawn.py:230`、`autostart.py:168` 用于探测服务。`/api/goals`、`/api/todos`、`/api/documents` 的数据在当前 UI 中也可随主状态或目标详情下发，应和独立 GET URL 的调用情况分开看。`web.py:646,718,2321,2335`

### POST

表中 `写` 包括落库、启动或停止执行、改变文件或运行中状态。`读＋临时状态` 包括预览凭证、队列或导出凭证。

| 方法 | 路径 | 处理函数与分支 | 读写 | 用途 | 前端或桌面调用方 | 会话身份或访问条件 |
|---|---|---|---|---|---|---|
| POST | `/api/admin/update/prepare` | `Service.prepare_update` `web.py:1125,348` | 写 | 为桌面更新进入暂停门禁 | `D/crew-updates.cjs:152,214` | `instance_id` |
| POST | `/api/admin/update/cancel` | `Service.cancel_update` `web.py:1127,368` | 写 | 取消更新门禁 | `D/crew-updates.cjs:192,210` | 实例及门禁 ID |
| POST | `/api/admin/connection/prepare` | `Service.prepare_connection` `web.py:1129,390` | 写 | 准备连接切换及停止相关执行 | `D/launch-settings.cjs:254` | `instance_id` |
| POST | `/api/report-preview/worker/register` | `report_previews.action` `web.py:1140` → `report_preview.py:118,199` | 写 | 注册桌面截图 worker | `D/report-preview.cjs:98,114` | 主机，登记后发 worker token |
| POST | `/api/report-preview/worker/next` | `report_preview.py:132,199`；`web.py:1140` | 读写 | 领预览任务 | `D/report-preview.cjs:98,141` | 主机，`worker_id/token` |
| POST | `/api/report-preview/worker/result` | `report_preview.py:156,199`；`web.py:1140` | 写 | 回传截图或失败结果 | `D/report-preview.cjs:98,164` | 主机，`worker_id/token/job_id` |
| POST | `/api/report-preview/worker/close` | `report_preview.py:192,199`；`web.py:1140` | 写 | 注销 worker 并结束挂起任务 | `D/report-preview.cjs:98,107,175` | 主机，`worker_id/token` |
| POST | `/api/remote-access` | `remote_access.action` `web.py:1148`；`remote_access.py:166` | 写 | 管理远程监听、配对和设备 | `F/app.js:1441`；`D/main.cjs:683` | 主机，远程清单另拒绝 |
| POST | `/api/behavior-prompts` | `web_api/behavior_prompts.post` `web.py:1150`；`web_api/behavior_prompts.py:47` | 写 | 保存或重置行为提示词 | `F/app.js:1441`、`F/assistant.js:64`；`D/main.cjs:655` | 对象范围和身份 |
| POST | `/api/personal/notes` | `web_api/personal.save_note` `web.py:1152`；`web_api/personal.py:73` | 写 | 执行想法墙动作 | `F/notes.js:26,644`、`F/personal-notes-ui.js:25` | `request_id`，按动作传 `note_id` |
| POST | `/api/personal/tags` | `web_api/personal.save_tag` `web.py:1154`；`web_api/personal.py:79` | 写 | 执行标签库动作 | `F/notes.js:27` | `request_id` |
| POST | `/api/personal/notes/recovery` | `web_api/personal.recover_note` `web.py:1156`；`web_api/personal.py:90` | 写 | 将想法绑定到核实过的原讨论 | `F/notes.js:294` | 目标 `session_id`、`note_id/request_id` |
| POST | `/api/personal/notes/start` | `chat.start_note` `web.py:1158` | 写 | 将想法发起为 leader 工作 | `F/notes.js:271` | `note_id/request_id` |
| POST | `/api/assistant/open` | `web_api/personal.open_assistant` `web.py:1161`；`web_api/personal.py:7` | 写 | 确保全局助手并设置上下文 | `F/assistant.js:41` | 服务端选全局助手 |
| POST | `/api/assistant/native-action` | `chat.personal_action` `web.py:1163`；`chat.py:1627` | 读写 | 宿主执行助手动作 | 无 | 调用者 `actor_session_id`，写需原始 `message_id/request_id` |
| POST | `/api/workspace-data/discover` | `storage.discover` `web.py:1177` | 读 | 发现目录及历史身份 | `D/storage.cjs:28,33` | 主机 |
| POST | `/api/workspace-data/open` | `Service._post` `web.py:1168,1179` | 写 | 打开、建联或迁移工作区历史 | `D/storage.cjs:10,12,16,23` | 主机 |
| POST | `/api/workspace-data/export` | `storage.export` `web.py:1186` | 读＋写包 | 导出工作区包到文件或下载区 | `F/storage-history.js:75`；`D/main.cjs:276,281` | 主机 |
| POST | `/api/workspace-data/preview` | `storage.preview` `web.py:1190` | 读 | 检查工作区包内容 | `D/main.cjs:289` | 主机 |
| POST | `/api/workspace-data/import` | `storage.import_package` `web.py:1192` | 写 | 导入包为只读恢复或分叉 | `D/main.cjs:294` | 主机 |
| POST | `/api/workspace-data/handoff` | `storage.handoff` `web.py:1195` | 写 | 生成交接包并执行交接流程 | `D/main.cjs:325` | 主机 |
| POST | `/api/workspace-data/activate` | `storage.activate` `web.py:1198` | 写 | 将历史工作区激活到目录 | `D/main.cjs:311` | 主机 |
| POST | `/api/storage/preview` | `storage_cleanup.preview` `web.py:1201,1077` | 读＋临时状态 | 生成清理候选和预览 ID | `F/app.js:1155`；`D/main.cjs:689` | 主机 |
| POST | `/api/storage/clean` | `storage_cleanup.clean` `web.py:1201,1079` | 写 | 执行预览确认的清理 | `F/app.js:1156`；`D/main.cjs:690` | 主机，`preview_id` |
| POST | `/api/storage/restore` | `storage_cleanup.restore` `web.py:1201,1081` | 写 | 恢复指定清理批次 | `F/app.js:1157`；`D/main.cjs:691` | 主机，`batch_id` |
| POST | `/api/storage/trash/empty` | `storage_cleanup.empty_trash` `web.py:1201,1083` | 写 | 清空回收站或指定批次 | `F/app.js:1158`；`D/main.cjs:692` | 主机 |
| POST | `/api/storage/policy` | `storage_cleanup.save_policy` `web.py:1201,1073,1085` | 写 | 保存自动清理及保留策略 | `F/app.js:1159`；`D/main.cjs:693` | 主机 |
| POST | `/api/project/update` | `Service.update_workspace` `web.py:1203,1020,1041` | 写 | 改名或迁移目录 | `F/app.js:1427`；`D/main.cjs:727` | 工作区修订，迁移需 `stop` |
| POST | `/api/project/remove` | `Service.update_workspace` `web.py:1203,1033` | 写 | 停止工作区执行并可恢复移除 | `F/app.js:1427`；`D/main.cjs:731` | 工作区修订及 `stop` |
| POST | `/api/maintenance/department/preview` | `maintenance.department_preview` `web.py:1205,1057` | 读＋临时状态 | 预览部门可压缩会话 | `F/maintenance-ui.js:244` | 目标 `leader_session_id` |
| POST | `/api/maintenance/department/compact` | `maintenance.department_start` `web.py:1205,1059` | 写 | 启动选中部门会话的压缩 | `F/maintenance-ui.js:261` | 目标、`preview_id` |
| POST | `/api/maintenance/cleanup/preview` | `maintenance.cleanup_preview` `web.py:1205,1061` | 读＋临时状态 | 预览工作区可归档内容 | `F/maintenance-ui.js:245`；`D/main.cjs:718` | — |
| POST | `/api/maintenance/cleanup` | `maintenance.cleanup_start` `web.py:1205,1063` | 写 | 执行已确认的工作区归档清理 | `F/maintenance-ui.js:262`；`D/main.cjs:718` | `preview_id/confirm` |
| POST | `/api/admin/chat/send` | `operations_chat.send` `web.py:1207` | 写 | 向运维对话发送输入 | `F/app.js:1433` | 服务端运维身份；远程拒绝 |
| POST | `/api/admin/chat/interrupt` | `operations_chat.interrupt` `web.py:1209` | 写 | 中断运维对话 | `F/app.js:1434` | 同上 |
| POST | `/api/admin/config` | `operations_settings.save` `web.py:1211` | 写 | 保存运维设置 | `F/app.js:1435` | —；远程拒绝 |
| POST | `/api/connections/configure` | `connections.configure` `web.py:1213` | 写 | 保存连接器配置 | `F/app.js:1146`；`D/main.cjs:679` | 主机 |
| POST | `/api/connections/action` | `connections.action` `web.py:1215`；`connections.py:183` | 读写 | 执行连接器操作 | `F/app.js:1147`；`D/main.cjs:680` | 主机 |
| POST | `/api/connections/preferences` | `connections.save_preferences` `web.py:1217` | 写 | 保存连接偏好 | `F/app.js:1148`；`D/main.cjs:681` | 主机 |
| POST | `/api/member-defaults` | `member_defaults.save` `web.py:1219` | 写 | 保存 leader 的默认成员配置 | `F/member-ui.js:79` | 目标 `leader_session_id` |
| POST | `/api/sounds/custom` | `event_sounds.add_custom` `web.py:1228` | 写 | 上传自定义提示音 | `F/sound-settings-ui.js:75` | — |
| POST | `/api/sounds/custom/delete` | `event_sounds.delete_custom` `web.py:1230` | 写 | 删除自定义提示音 | `F/sound-settings-ui.js:84` | 音效 ID |
| POST | `/api/claude-budget` | `Service._post` `web.py:1235` | 写 | 保存 Claude 预算 | `F/app.js:1281` | — |
| POST | `/api/settings` | `Service._post` `web.py:1242` | 写 | 保存 AI、执行或声音偏好 | `F/app.js:1431,1440`；`D/main.cjs:665` | — |
| POST | `/api/session/runtime/prepare` | `chat.prepare_runtime` `web.py:1253,1259,1270` | 读＋写预览 | 检查切换范围并建立预览 | `F/app.js:1462` | 目标 `session_id` |
| POST | `/api/session/runtime/switch` | `chat.switch_runtime` `web.py:1253,1264,1272` | 写 | 确认切换会话载体 | `F/app.js:1463` | 目标 `session_id`、`preview_id/confirm` |
| POST | `/api/files/resolve` | `Service.resolve_file` `web.py:1273,1276,1278,1853` | 读 | 解析文件位置和可打开状态 | `F/chat.js:273`、`F/documents.js:28` | 目标 `session_id` 或 `document_id` |
| POST | `/api/files/open` | `Service._post` `web.py:1273,1284` | 外部动作 | 用主机程序打开文件 | `F/chat.js:283,314`、`F/documents.js:51` | 同上；远程拒绝 |
| POST | `/api/files/reveal` | `Service._post` `web.py:1273,1290` | 外部动作 | 在主机文件管理器定位文件 | `F/chat.js:314` | 同上；远程拒绝 |
| POST | `/api/goals/report/folder` | `Service._post` `web.py:1300` | 外部动作 | 打开目标报告目录 | `F/goal-ui.js:343` | `goal_id/report_id`；远程拒绝 |
| POST | `/api/git/fetch` | `git_view.fetch` `web.py:1323,1330` | 写 | 刷新 Git 远程引用和拉取信息 | `F/git.js:156,169` | — |
| POST | `/api/git/adopt` | `git_overview.adopt` `web.py:1331,1335` | 写 | 将无人负责的分支交给 leader | `F/git.js:156,181` | 目标 `leader_session_id` |
| POST | `/api/project/init` | `Service._init_project` `web.py:1340,1114` | 写 | 注册工作区目录 | 无 | — |
| POST | `/api/host/workspace/existing` | `host_workspace.existing` `web.py:1342,1349` | 读写 | 识别或注册主机已有目录 | `D/core.cjs:402` | 主机 |
| POST | `/api/host/workspace/new` | `host_workspace.new` `web.py:1342,1346` | 写 | 在主机新建并注册目录 | `D/core.cjs:406` | 主机 |
| POST | `/api/notifications/away` | `Service.set_away` `web.py:1350,1966` | 写 | 设置用户离开状态 | `F/app.js:1149,1594`；`D/main.cjs:686` | — |
| POST | `/api/git/settings` | `git_admin.configure` `web.py:1359,1363` | 写 | 保存 Git 主干、集成分支或解除管理绑定 | `F/app.js:1423`、`F/git-settings.js:53,71` | 工作区及修订 |
| POST | `/api/git/enable` | `git_admin.enable` `web.py:1364,1370` | 写 | 启用并配置 Git 助手 | `F/git.js:156,177` | — |
| POST | `/api/git/disable` | `git_admin.disable` `web.py:1364,1373` | 写 | 解除 Git 助手管理绑定 | 无 | — |
| POST | `/api/native-approval` | `Service.resolve_native_approval` `web.py:1382,1094` | 写 | 批准或拒绝挂起的原生操作 | `F/native-approval-ui.js:28` | 审批 `id` |
| POST | `/api/goals/create` | `goal_api.post` `web.py:1384`；`goal_api.py:129` | 写 | 创建工作区目标 | `F/goal-ui.js:320` | 目标 `leader_session_id` |
| POST | `/api/goals/action` | `goal_api.post` `web.py:1384`；`goal_api.py:136` | 写 | 目标生命周期和交付验收 | `F/goal-ui.js:281` | `goal_id`，按动作校验版本、报告及接收者 |
| POST | `/api/goals/context` | `goal_api.post` `web.py:1384`；`goal_api.py:201` | 写 | 添加、更正或标记上下文失效 | 无 | `goal_id`，已有条目用 `item_id/version` |
| POST | `/api/goals/agent` | `goal_api.post` `web.py:1131,1142,1384`；`goal_api.py:114` | 读写 | 执行通用目标、上下文、报告及版本动作 | `F/goal-ui.js:334`、`F/stage-report-ui.js:16` | 调用者 `actor_session_id` 可为 `user`，AI 可绑定 `execution_id` |
| POST | `/api/documents` | `documents.register` `web.py:1386,1389` | 写 | 登记交付文件 | 无 | 必填归属 `session_id`，可选目标 |
| POST | `/api/documents/pin` | `documents.pin` `web.py:1394` | 写 | 切换交付文件的重要标记 | `F/documents.js:37` | 文档 `id` |
| POST | `/api/documents/refresh` | `documents.refresh` `web.py:1397` | 写 | 重扫交付文件索引 | 无 | — |
| POST | `/api/trust` | `trust_mode.set` `web.py:1399` | 写 | 修改 leader 授权设置 | `F/app.js:1106,1536` | 目标 `leader_session_id` |
| POST | `/api/decision/discuss` | `decision_flow`、`Service._post` `web.py:1401,1407–1424` | 写 | 开始围绕拍板的讨论 | `F/app.js:1588` | 拍板 `id` |
| POST | `/api/decision/ready` | `decision_flow.ready` `web.py:1405` | 写 | 将拍板标记为可答复 | `F/app.js:1588` | 拍板 `id` |
| POST | `/api/decision/clarify` | `Service._post` `web.py:1425` | 写 | 请归属 leader 补充说明 | `F/app.js:1585` | 拍板 `id` |
| POST | `/api/comments` | `Service._post` `web.py:1434` | 写 | 登记会话留言及关联通知 | 无 | 目标 `session_id` |
| POST | `/api/todo/resolve` | `todo.resolve` `web.py:1449,1454` | 写 | 完成或取消人工待办 | `F/app.js:808` | 待办 `id`，可校验版本 |
| POST | `/api/todo/undo` | `todo.undo` `web.py:1452` | 写 | 撤销人工待办处理 | `F/app.js:812` | 待办 `id/before/version` |
| POST | `/api/messages` | `chat.send_message` `web.py:1456,1463` | 写 | 按 Crew 身份路由跨会话消息 | 无 | 必填 `caller_session_id`，可绑定 `execution_id` |
| POST | `/api/leaders/order` | `Service._post` `web.py:1466` | 写 | 保存 leader 顺序和置顶 | `F/app.js:292` | leader ID 列表 |
| POST | `/api/domains` | `domains` 批量更新 `web.py:1469` | 写 | 保存工作区分区图操作 | `F/architecture.js:320` | 分区图版本及操作列表 |
| POST | `/api/project/theme` | `Service._post` `web.py:1471` | 写 | 保存工作区主题 | `F/app.js:1597` | — |
| POST | `/api/channel` | `channel.post` `web.py:1473` | 写 | 以用户身份发交流区消息 | `F/channel.js:183` | 固定 `board.HUMAN` |
| POST | `/api/channel/read` | `channel.mark_read` `web.py:1477` | 写 | 更新用户交流区已读位置 | `F/channel.js:113` | 固定 `board.HUMAN` |
| POST | `/api/channel/digest` | `channel_digest.start` `web.py:1479` | 写 | 请求生成交流区摘要 | `F/channel.js:80` | — |
| POST | `/api/attention/dismiss` | `Service._post` `web.py:1483` | 写 | 隐藏或恢复提醒标记 | 无 | 按提醒种类验证目标和工作区 |
| POST | `/api/session/appearance` | `appearance.save` `web.py:1505` | 写 | 设置会话头像和外观 | `F/app.js:1067` | 目标 `session_id` |
| POST | `/api/session/rename` | `Service._post` `web.py:1507` | 写 | 重命名给定会话 | `F/app.js:1080` | 目标 `session_id` |
| POST | `/api/session/archive` | `Service.archive_session` `web.py:1511,1733` | 写 | 归档会话并处理相关执行 | `F/app.js:1400,1663` | 目标 `session_id`，可选调用者 |
| POST | `/api/session/delete` | `Service.delete_session` `web.py:1514,1747` | 写 | 删除会话及受约束的本地记录 | `F/app.js:1666` | 目标 `session_id`，调用者须缺省或用户 |
| POST | `/api/session/restore` | `session_ops.restore` `web.py:1518` | 写 | 恢复归档会话 | 无 | 目标 `session_id` |
| POST | `/api/session/undo` | `session_ops.undo` `web.py:1520` | 写 | 还原会话操作前状态 | `F/app.js:1401` | 目标 `session_id`、`before` |
| POST | `/api/session/stop` | `chat.stop_identity` `web.py:1522` | 写 | 停止托管会话执行 | `F/app.js:1670`、`F/chat.js:481` | 目标 `session_id`，可选调用者 |
| POST | `/api/session/compact` | `chat.compact` 或目标执行压缩 `web.py:1524–1545` | 写 | 请求压缩会话上下文 | `F/app.js:1205` | 目标 `session_id`，可选调用者及目标执行 |
| POST | `/api/session/options` | `Service._post` `web.py:1546` | 写 | 更新会话模型、推理或速度选项 | `F/app.js:1651` | 目标 `session_id` |
| POST | `/api/answer` | `Service._post`、`board.answer` `web.py:1550–1568` | 写 | 记用户拍板并路由后续处理 | `F/app.js:1396` | 固定用户，拍板 `id` |
| POST | `/api/wake` | `chat.wake` `web.py:1569,1578` | 写 | 唤醒指定会话并传入文本 | `F/app.js:1556` | 目标 `session_id` 或 `name`，可选调用者 |
| POST | `/api/spawn` | `chat.start` `web.py:1580,1592` | 写 | 新建托管会话或目标内执行 | 无 | 可选调用者、调用执行、目标 ID |
| POST | `/api/leader` | `chat.start` 或讨论创建分支 `web.py:1603–1638` | 写 | 新建 leader，可带成员及初始要求 | `F/app.js:1696` | 可选调用者；候选成员含目标 ID |
| POST | `/api/chat/send` | `Service._post`、`chat.send`、目标发送 `web.py:1640–1680` | 写 | 发用户聊天输入，可进入助手、目标或想法讨论 | `F/chat.js:1032` | 目标 `session_id`，可选目标执行或请求 ID |
| POST | `/api/chat/interrupt` | `chat.interrupt` `web.py:1682–1695` | 写 | 中断指定聊天或目标执行的当前轮 | 无 | 目标 `session_id`，可选目标执行 |
| POST | `/api/chat/read` | `goals.mark_read` 或会话已读 `web.py:1696–1703` | 写 | 推进聊天已读序号 | `F/chat.js:859` | 目标 `session_id`，可选目标 |
| POST | `/api/roster/move` | `Service._post` `web.py:1704` | 写 | 将成员移到另一组 | `F/app.js:1661` | 目标 `session_id` 或姓名 |
| POST | `/api/roster/remove` | `Service._post` `web.py:1711` | 写 | 成员退组 | `F/app.js:1662` | 目标 `session_id` 或姓名 |
| POST | `/api/group/close` | `session_ops`、`Service.stop_archived` `web.py:1718–1722` | 写 | 结束 leader 组，可同时结束成员 | `F/app.js:1552` | 组名，可选调用者 |
| POST | `/api/group/undo` | `session_ops.undo_group` `web.py:1723` | 写 | 撤销结束组操作 | `F/app.js:1554` | 组名、`before` |
| POST | `/api/roster/adopt` | `Service.adopt` `web.py:1725,608` | 写 | 收编待收编的原生会话 | 无 | 候选 `session_id` |
| POST | `/api/session-library/adopt` | `session_library.adopt` `web.py:1728` | 写 | 从会话库收编记录 | `F/session-library.js:111` | 会话库 `key` |

**匹配边界。** `web.py:2208–2243` 先用精确 `POST_PATHS` 拒绝未知 POST。`/api/workspace-data/`、`/api/storage/`、`/api/maintenance/`、`/api/goals/`、`/api/session/` 前缀是处理组或访问检查，表内列出的精确路径才可达。报告 worker 前缀也只生成四条精确路径。没有发现正则路由，也没有 `do_PUT`、`do_DELETE`、`do_PATCH`、`do_HEAD` 或 `do_OPTIONS` 实现。静态 GET 分支在 `web.py:2464–2470`，白名单定义在 `web.py:2028–2098`，共 **76** 条。`web.py:1134,1166,1201–1206,1384,1501,2094,2501–2505`、`report_preview.py:24–25`

### 远程登录和退出

| 方法 | 路径 | 处理 | 读写 | 用途 | 调用方 | 身份 |
|---|---|---|---|---|---|---|
| GET | `/remote/login` | `remote_access.py:382` | 读 | 返回配对登录页 | `F/access-context.js:9`，配对链接 `F/remote-access-ui.js:52` | 可匿名 |
| POST | `/remote/login` | `remote_access.py:405–418` | 写 | 消费配对码，签发设备身份 | `F/remote-login.html:11`；`D/core.cjs:164` | 配对码，浏览器 Cookie 或桌面 Bearer |
| POST | `/remote/logout` | `remote_access.py:420–425` | 写 | 注销当前设备并清理浏览器 Cookie | 无 | 当前设备身份 |
| GET | `/remote/logout` | `remote_access.py:420–422` | 拒绝 | 已认证请求返回 405 | 无 | 当前设备身份 |

`remote_http.py` 负责监听、连接边界和关闭流程，认证及分派在 `remote_access.py:345–440`。`remote_http.py:21,36–67,88–106`

### `remote_routes.py` 允许和拒绝清单

浏览器按允许清单放行。桌面按拒绝清单处理，已认证且通过该规则的其他 GET/POST 继续交给 `web.py` 匹配实际路由。两种客户端的登录和退出由上表处理。`remote_routes.py:46–51`、`remote_access.py:379–395,405–436`

| 方法 | 路径 | 放行或拒绝 | 行号 |
|---|---|---|---|
| GET | `/api/access/context` | 浏览器放行 | `remote_routes.py:8` |
| GET | `/api/state` | 浏览器放行 | `remote_routes.py:8` |
| GET | `/api/projects` | 浏览器放行 | `remote_routes.py:8` |
| GET | `/api/workspaces/activity` | 浏览器放行 | `remote_routes.py:8` |
| GET | `/api/assistant/state` | 浏览器放行 | `remote_routes.py:9` |
| GET | `/api/personal/notes` | 浏览器放行 | `remote_routes.py:9` |
| GET | `/api/personal/notes/recovery` | 浏览器放行 | `remote_routes.py:9` |
| GET | `/api/personal/tags` | 浏览器放行 | `remote_routes.py:9` |
| GET | `/api/goals` | 浏览器放行 | `remote_routes.py:10` |
| GET | `/api/goal` | 浏览器放行 | `remote_routes.py:10` |
| GET | `/api/goals/report` | 浏览器放行 | `remote_routes.py:10` |
| GET | `/api/documents` | 浏览器放行 | `remote_routes.py:10` |
| GET | `/api/comments` | 浏览器放行 | `remote_routes.py:10` |
| GET | `/api/chat/history` | 浏览器放行 | `remote_routes.py:11` |
| GET | `/api/chat/events` | 浏览器放行 | `remote_routes.py:11` |
| GET | `/api/chat/file` | 浏览器放行 | `remote_routes.py:11` |
| GET | `/api/session/avatar` | 浏览器放行 | `remote_routes.py:11` |
| GET | `/api/files/content` | 浏览器放行 | `remote_routes.py:11` |
| GET | `/api/files/download` | 浏览器放行 | `remote_routes.py:11` |
| GET | `/api/git` | 浏览器放行 | `remote_routes.py:12` |
| GET | `/api/git/commit` | 浏览器放行 | `remote_routes.py:12` |
| GET | `/api/domains` | 浏览器放行 | `remote_routes.py:12` |
| GET | `/api/sounds/custom/file` | 浏览器放行 | `remote_routes.py:12` |
| GET | `/api/usage` | 浏览器放行 | `remote_routes.py:12` |
| GET | `/api/usage/export` | 浏览器放行 | `remote_routes.py:12` |
| GET | `/api/channel` | 浏览器放行 | `remote_routes.py:12` |
| GET | `/api/todos` | 浏览器放行 | `remote_routes.py:12` |
| GET | `/api/runtimes` | 浏览器放行 | `remote_routes.py:12` |
| GET | `STATIC` 白名单 | 浏览器放行 | `remote_routes.py:49–50` |
| POST | `/api/assistant/open` | 浏览器放行 | `remote_routes.py:15` |
| POST | `/api/personal/notes` | 浏览器放行 | `remote_routes.py:15` |
| POST | `/api/personal/notes/start` | 浏览器放行 | `remote_routes.py:15` |
| POST | `/api/personal/tags` | 浏览器放行 | `remote_routes.py:15` |
| POST | `/api/personal/notes/recovery` | 浏览器放行 | `remote_routes.py:16` |
| POST | `/api/chat/send` | 浏览器放行 | `remote_routes.py:16` |
| POST | `/api/chat/interrupt` | 浏览器放行 | `remote_routes.py:16` |
| POST | `/api/chat/read` | 浏览器放行 | `remote_routes.py:16` |
| POST | `/api/leader` | 浏览器放行，拒绝 `candidate_members` | `remote_routes.py:17,60` |
| POST | `/api/wake` | 浏览器放行 | `remote_routes.py:17` |
| POST | `/api/spawn` | 浏览器放行 | `remote_routes.py:17` |
| POST | `/api/member-defaults` | 浏览器放行 | `remote_routes.py:17` |
| POST | `/api/roster/move` | 浏览器放行 | `remote_routes.py:17` |
| POST | `/api/roster/remove` | 浏览器放行 | `remote_routes.py:17` |
| POST | `/api/git/adopt` | 浏览器放行 | `remote_routes.py:17` |
| POST | `/api/notifications/away` | 浏览器放行 | `remote_routes.py:18` |
| POST | `/api/goals/create` | 浏览器放行 | `remote_routes.py:18` |
| POST | `/api/goals/action` | 浏览器放行 | `remote_routes.py:18` |
| POST | `/api/goals/context` | 浏览器放行 | `remote_routes.py:18` |
| POST | `/api/goals/agent` | 浏览器及桌面仅放行用户动作 `mode.set`、`report.publish`、`versions.list`、`versions.preview`、`version.capture`、`version.restore` | `remote_routes.py:19,28–29,62–70` |
| POST | `/api/answer` | 浏览器放行 | `remote_routes.py:19` |
| POST | `/api/decision/discuss` | 浏览器放行 | `remote_routes.py:20` |
| POST | `/api/decision/ready` | 浏览器放行 | `remote_routes.py:20` |
| POST | `/api/decision/clarify` | 浏览器放行 | `remote_routes.py:20` |
| POST | `/api/native-approval` | 浏览器放行 | `remote_routes.py:20` |
| POST | `/api/todo/resolve` | 浏览器放行 | `remote_routes.py:21` |
| POST | `/api/todo/undo` | 浏览器放行 | `remote_routes.py:21` |
| POST | `/api/comments` | 浏览器放行 | `remote_routes.py:21` |
| POST | `/api/channel` | 浏览器放行 | `remote_routes.py:22` |
| POST | `/api/channel/read` | 浏览器放行 | `remote_routes.py:22` |
| POST | `/api/channel/digest` | 浏览器放行 | `remote_routes.py:22` |
| POST | `/api/attention/dismiss` | 浏览器放行 | `remote_routes.py:22` |
| POST | `/api/leaders/order` | 浏览器放行 | `remote_routes.py:23` |
| POST | `/api/domains` | 浏览器放行 | `remote_routes.py:23` |
| POST | `/api/project/theme` | 浏览器放行 | `remote_routes.py:23` |
| POST | `/api/session/appearance` | 浏览器放行 | `remote_routes.py:24` |
| POST | `/api/session/rename` | 浏览器放行 | `remote_routes.py:24` |
| POST | `/api/session/archive` | 浏览器放行 | `remote_routes.py:24` |
| POST | `/api/session/delete` | 浏览器放行 | `remote_routes.py:24` |
| POST | `/api/session/restore` | 浏览器放行 | `remote_routes.py:24` |
| POST | `/api/session/undo` | 浏览器放行 | `remote_routes.py:24` |
| POST | `/api/session/stop` | 浏览器放行 | `remote_routes.py:25` |
| POST | `/api/session/compact` | 浏览器放行 | `remote_routes.py:25` |
| POST | `/api/session/options` | 浏览器放行 | `remote_routes.py:25` |
| POST | `/api/files/resolve` | 浏览器放行 | `remote_routes.py:26` |
| POST | `/api/documents` | 浏览器放行 | `remote_routes.py:26` |
| POST | `/api/documents/pin` | 浏览器放行 | `remote_routes.py:26` |
| POST | `/api/documents/refresh` | 浏览器放行 | `remote_routes.py:26` |
| GET/POST | 上述浏览器允许项之外的业务路径 | 浏览器拒绝 | `remote_routes.py:46–51` |
| GET | `/api/remote-access` | 桌面拒绝 | `remote_routes.py:34` |
| GET | `/api/admin/connection/plan` | 桌面拒绝 | `remote_routes.py:34` |
| GET | `/api/admin/chat` | 桌面拒绝 | `remote_routes.py:34` |
| GET | `/api/admin/config` | 桌面拒绝 | `remote_routes.py:34` |
| POST | `/api/remote-access` | 桌面拒绝 | `remote_routes.py:37` |
| POST | `/api/admin/update/prepare` | 桌面拒绝 | `remote_routes.py:37` |
| POST | `/api/admin/update/cancel` | 桌面拒绝 | `remote_routes.py:37` |
| POST | `/api/admin/connection/prepare` | 桌面拒绝 | `remote_routes.py:38` |
| POST | `/api/admin/chat/send` | 桌面拒绝 | `remote_routes.py:38` |
| POST | `/api/admin/chat/interrupt` | 桌面拒绝 | `remote_routes.py:38` |
| POST | `/api/admin/config` | 桌面拒绝 | `remote_routes.py:39` |
| POST | `/api/files/open` | 桌面拒绝 | `remote_routes.py:39` |
| POST | `/api/files/reveal` | 桌面拒绝 | `remote_routes.py:39` |
| POST | `/api/workspace-data/preview` | 桌面拒绝 | `remote_routes.py:39` |
| POST | `/api/workspace-data/import` | 桌面拒绝 | `remote_routes.py:40` |
| POST | `/api/workspace-data/handoff` | 桌面拒绝 | `remote_routes.py:40` |
| POST | `/api/messages` | 桌面拒绝 | `remote_routes.py:40` |
| POST | `/api/assistant/native-action` | 桌面拒绝 | `remote_routes.py:41` |
| POST | `/api/goals/report/folder` | 桌面拒绝 | `remote_routes.py:41` |
| POST | `/api/report-preview/worker/register` | 桌面拒绝 | `remote_routes.py:42`、`report_preview.py:25` |
| POST | `/api/report-preview/worker/next` | 桌面拒绝 | `remote_routes.py:42`、`report_preview.py:25` |
| POST | `/api/report-preview/worker/result` | 桌面拒绝 | `remote_routes.py:42`、`report_preview.py:25` |
| POST | `/api/report-preview/worker/close` | 桌面拒绝 | `remote_routes.py:42`、`report_preview.py:25` |
| GET/POST | 上述桌面拒绝项之外的路径 | 桌面放行到后端分派 | `remote_routes.py:46–48` |
| 其他方法 | 任意路径 | 当前监听器未实现 | `remote_routes.py:46–51`、`web.py:2501–2505` |

远程 POST 的通用请求体规则拒绝 `caller_session_id/caller_execution_id`，普通路由拒绝 `actor_session_id`。`/api/goals/agent` 将 actor 固定为 `user`，同时检查内层 `data` 的 actor、caller、execution 字段。`remote_routes.py:54–73`

## 2. CLI 子命令

全局参数是 `--home PATH`、`--session ID`，帮助由 argparse 提供。参数表中 `!` 表示必填选项，`?` 表示可选位置参数。`S` 表示 `--session` → `CREW_SESSION_ID` → `CLAUDE_CODE_SESSION_ID`。`A` 用相同顺序，缺省为 `board.HUMAN`。`P` 表示工作区可由绑定会话解析，或由显式项目和当前目录解析。`E/G` 表示读取 `CREW_EXECUTION_ID/CREW_GOAL_ID`。`宿主 S` 表示额外校验宿主绑定。`cli.py:49,78–88,519–539,579–627`

| 子命令 | 参数与注册 | 处理位置 | 读写 | 传输或存储 | 身份 | 用途 |
|---|---|---|---|---|---|---|
| `install` | 无 `cli.py:90` | `cli.py:1094` | 写 | `install.install`＋库事件 | A | 安装本地 Crew 集成 |
| `init` | `--name --root` `cli.py:91` | `cli.py:703` | 写 | 库 `board.init_project` | — | 注册工作区 |
| `start` | `--cwd --name` `cli.py:94` | `cli.py:1001`、`startup.py:25` | 读写 | 库注册、GET `/api/state` 探测、自启服务和打开页面 | — | 打开或启动当前工作区的 Crew |
| `projects` | 无 `cli.py:97` | `cli.py:715` | 读 | 库 `board.projects` | — | 列工作区 |
| `archive` | `name` `cli.py:98` | `cli.py:953,961` | 写 | POST `/api/session/archive` | 宿主身份或 A，P | 归档成员会话 |
| `git enable` | `--base --integration --runtime {claude,codex}` `cli.py:101` | `cli.py:878` | 写 | POST `/api/git/enable` 后读配置 | P、A | 启用 Git 助手 |
| `git disable` | 无 `cli.py:105` | `cli.py:879` | 写 | POST `/api/git/disable` 后读配置 | P、A | 解除 Git 助手管理 |
| `git status` | 无 `cli.py:106` | `cli.py:904` | 读 | 库 `board.git_config` | P | 查看 Git 管理配置 |
| `git policy` | `--set TEXT` `cli.py:107` | `cli.py:885` | 读写 | 库 `board.git_policy/set_git_policy` | P、A | 查或写分支规则 |
| `git request` | `description --branch --blocking` `cli.py:108` | `cli.py:888` | 写 | 库 `board.request_git_merge` | P、S | 提交合并请求 |
| `git requests` | `--all` `cli.py:112` | `cli.py:893` | 读 | 库 `board.git_requests` | P | 列合并请求 |
| `git branches` | `--json` `cli.py:113` | `cli.py:896` | 读 | 库及 `git_overview.branches` | P | 列待合并、待推送分支及负责人 |
| `git adopt` | `branch` `cli.py:114` | `cli.py:898` | 写 | 直接 `git_overview.adopt` | P、S | 当前 leader 认领无在岗负责人的分支 |
| `git resolve` | `id status:{merged,rejected,waiting_user,merging} --note! --pr INT` `cli.py:115` | `cli.py:901` | 写 | 库 `board.resolve_git_request` | P、S | 登记合并处理结果 |
| `resource add` | `name --kind {device,dir,app,other} --desc --guard` `cli.py:121` | `cli.py:907` | 写 | 库 `board.add_resource` | P、A | 新增共享资源定义 |
| `resource list` | 无 `cli.py:126` | `cli.py:912` | 读 | 库 `board.resources` | P | 列共享资源 |
| `resource rm` | `name` `cli.py:127` | `cli.py:910` | 写 | 库 `board.remove_resource` | P、A | 删除共享资源定义 |
| `join` | `name --task --role {member,leader} --group --scope --done-when` `cli.py:128` | `cli.py:717` | 写 | 库 `board.join`、任务卡和 leader 通知 | 宿主 S | 收编当前会话 |
| `whoami` | `--json` `cli.py:135` | `cli.py:743` | 读 | 库 `board.whoami`＋卡片 | S | 查自己的 Crew 身份 |
| `rename` | `name --json` `cli.py:136` | `cli.py:745` | 写 | 库 `session_ops.rename_self` | 宿主 S | leader 自改名 |
| `task` | `text` `cli.py:137` | `cli.py:752` | 写 | 库 `board.set_task` | S | 更新自己的工作描述 |
| `card` | `--scope --done-when --json`，至少给一个内容字段 `cli.py:138` | `cli.py:754,667` | 写 | 库任务卡 | S | 更新自己的范围与完成条件 |
| `status` | `--project --json` `cli.py:141` | `cli.py:913` | 读 | 库及原生状态读取 | P | 看白板和会话状态 |
| `who` | `name --json` `cli.py:143` | `cli.py:922` | 读 | 库及会话上下文 | P | 查同事会话 |
| `compact` | `name? --json` `cli.py:144` | `cli.py:1076` | 写 | POST `/api/session/compact` | 无姓名时 S，有姓名时 A、P | 请求压缩会话 |
| `lease take` | `resource --for! --until --overwrites` `cli.py:146` | `cli.py:934` | 写 | 库 `board.take` | P、S | 领取租约 |
| `lease give` | `resource --note --force --reason` `cli.py:151` | `cli.py:941` | 写 | 库 `board.give` | P、A | 释放租约 |
| `lease list` | `--json` `cli.py:156` | `cli.py:932` | 读 | 库 `board.leases` | P | 查租约 |
| `claim` | `pattern --note` `cli.py:157` | `cli.py:831` | 写 | 库 `board.claim` | S | 声明文件范围 |
| `unclaim` | `pattern? --all` `cli.py:160` | `cli.py:833` | 写 | 库 `board.unclaim` | S | 解除文件范围声明 |
| `claims` | `--json` `cli.py:164` | `cli.py:942` | 读 | 库 `board.claims` | P | 列声明范围 |
| `ask` | `question --option* --recommend --blocking`＋摘要参数 `B` `cli.py:165` | `cli.py:779` | 写 | 库 `board.ask`，可挂目标执行 | S、E/G | 新建拍板 |
| `decisions` | `--all --json` `cli.py:171` | `cli.py:944` | 读 | 库 `board.decisions`＋摘要 | P | 列拍板 |
| `decision revise` | `id --question --option* --recommend --json`＋`B` `cli.py:172` | `cli.py:791,805` | 写 | 库 `decision_briefs.revise` | S | 原位补充已有拍板 |
| `trust status` | `--leader --json` `cli.py:180–186` | `cli.py:806,830` | 读 | 库 `trust_mode.get` | P、A | 读 leader 授权 |
| `trust report` | `--leader --json` `cli.py:180–186` | `cli.py:828` | 读 | 库 `trust_mode.report` | P、A | 生成授权报告 |
| `trust set` | `enabled:{on,off} --goal --leader --json` `cli.py:187` | `cli.py:825` | 写 | 库 `trust_mode.set` | P，写时宿主环境优先 | 修改授权 |
| `answer` | `id text` `cli.py:192` | `cli.py:758` | 写 | 库 `board.answer` | 有绑定时需该题原归属 leader，缺省用户 | 记拍板答复 |
| `withdraw` | `id` `cli.py:195` | `cli.py:777` | 写 | 库 `board.withdraw` | S | 撤回自己的拍板 |
| `handoff` | `--to! text` `cli.py:196` | `cli.py:835` | 写 | 库 `board.handoff` | S | 记交接 |
| `handoffs` | `--json` `cli.py:199` | `cli.py:837` | 读 | 库 `board.handoffs` | S | 查看待交接 |
| `say` | `text --to* --reply-to INT --detail --json` `cli.py:200` | `cli.py:871` | 写 | 库 `channel.post` | S，leader | 发交流区消息 |
| `channel` | `--limit INT --raw --json`，限量默认 20 `cli.py:205` | `cli.py:1085` | 读写 | 库 `channel.digest/read` | P、A | 读交流区并推进已读 |
| `report` | `text` `cli.py:208` | `cli.py:839` | 写 | 库 `board.report` | S | 提交进度报告 |
| `reports` | `--since DATE --group --json` `cli.py:209` | `cli.py:947` | 读 | 库 `board.reports` | P | 查进度报告 |
| `todo` | `--all --json` `cli.py:212` | `cli.py:841,851` | 读 | 库 `todo.list` | P | 默认列人工待办 |
| `todo list` | `--all --json` `cli.py:215` | `cli.py:841,851` | 读 | 库 `todo.list` | P | 显式列人工待办 |
| `todo add` | `title --steps! --verify! --blocking --operation-key --json` `cli.py:218` | `cli.py:845` | 写 | 库 `todo.add_native` | S、E/G | 登记人工执行事项 |
| `todo withdraw` | `id --reason! --json` `cli.py:225` | `cli.py:849` | 写 | 库 `todo.withdraw` | S | 撤回人工执行事项 |
| `todos` | `--all --json` `cli.py:229` | `cli.py:841,851` | 读 | 库 `todo.list` | P | 列人工待办的兼容命令 |
| `doc` | `--json` `cli.py:230` | `cli.py:852,870` | 读 | 库 `documents.list` | P、可选 E/G | 默认列交付文档 |
| `doc add` | `path --title! --summary! --kind {summary,report,guide,other} --important --json` `cli.py:231` | `cli.py:855` | 写 | 库 `documents.register` | S、E/G | 登记交付文件 |
| `doc list` | `--leader --json` `cli.py:238` | `cli.py:863–870` | 读 | 库 `documents.list` | P、可选 E/G | 按 leader 或当前任务筛文档 |
| `doc refresh` | `--json` `cli.py:241` | `cli.py:860` | 写 | `documents.refresh` 后读库 | P | 刷新交付文件索引 |
| `group list` | `--json` `cli.py:242–243` | `cli.py:949` | 读 | 库 `board.groups` | P | 列分组 |
| `roster add` | `group names+` `cli.py:244–248` | `cli.py:951` | 写 | 库 `board.roster` | P、A | 批量加入组 |
| `roster remove` | `group names+` `cli.py:244–248` | `cli.py:951` | 写 | 库 `board.roster` | P、A | 批量退组 |
| `roster move` | `name --to!` `cli.py:249` | `cli.py:951` | 写 | 库 `board.roster` | P、A | 转组 |
| `leader close` | `group --with-members` `cli.py:253` | `cli.py:953,957` | 写 | POST `/api/group/close` | 宿主身份或 A，P | 结束 leader 组 |
| `leader start` | `--name! --model --effort --speed --runtime {claude,codex} --members --task --cwd 或 --new-worktree --dry-run` `cli.py:256–267` | `cli.py:1006`、`spawn.py:261,293` | 写，dry-run 读计划 | POST `/api/leader`，dry-run 走本地计划 | 宿主 S 或显式会话，P | 创建并启动 leader |
| `spawn` | `--goal --name! --model --effort --speed --runtime {claude,codex} --task! --group --role {member,leader} --cwd 或 --new-worktree --base --dry-run --json` `cli.py:268–282` | `cli.py:1006`、`spawn.py:261,316` | 写，dry-run 读计划 | POST `/api/spawn`，dry-run 走本地计划 | 宿主 S 或显式会话，E、P | 启动成员或目标内执行 |
| `wake` | `name text --json` `cli.py:283` | `cli.py:1019` | 写 | POST `/api/wake` | P、A | 唤醒会话 |
| `stop` | `name --json` `cli.py:286` | `cli.py:1066` | 写 | POST `/api/session/stop` | P、A | 停止托管执行 |
| `send` | `name`，`text` 或 `--text-file PATH`，`--kind {task,result,blocker,decision,user_answer,information,ack} --message-id --reply-to --json` `cli.py:288–296` | `cli.py:1031` | 写 | POST `/api/messages` | S、E、P | 跨载体发送协作消息，`-` 可从 stdin 读正文 |
| `personal` | `action --message-id --request-id --data JSON 或 --file PATH --json` `cli.py:297–304` | `cli.py:599,1058` | 读写 | 读直接 `personal_actions.dispatch`；写 POST `/api/assistant/native-action` | 宿主 S，须有效全局助手 | 第 4 节的动作 |
| `web` | `--port INT` 默认 47811，`--open` `cli.py:332` | `cli.py:1014` | 启动服务 | `web.serve` | — | 提供本机 Web 后端及可配置远程监听 |
| `autostart install` | `--json` `cli.py:335–337` | `cli.py:992` | 写 | `autostart.configure`，系统自启 | — | 安装登录自启 |
| `autostart status` | `--json` `cli.py:335–337` | `cli.py:992` | 读 | `autostart.configure`，系统状态 | — | 检查自启 |
| `autostart uninstall` | `--json` `cli.py:335–337` | `cli.py:992` | 写 | `autostart.configure`，系统自启 | — | 卸载登录自启 |
| `menu install` | 无 `cli.py:338–340` | `cli.py:997` | 写 | `menu.configure`，系统菜单 | — | 安装系统入口 |
| `menu status` | 无 `cli.py:338–340` | `cli.py:997` | 读 | `menu.configure`，系统状态 | — | 检查系统入口 |
| `menu uninstall` | 无 `cli.py:338–340` | `cli.py:997` | 写 | `menu.configure`，系统菜单 | — | 删除系统入口 |
| `events` | 子命令自己的 `--session NAME --limit INT`，默认 50 `cli.py:341` | `cli.py:966` | 读 | 库 `board.events` | P；子命令参数是筛选对象 | 列白板事件 |
| `doctor` | 无 `cli.py:344` | `cli.py:973,1100` | 读 | 本地库、规则和运行配置 | — | 检查 Crew 环境 |

摘要参数 `B` 为 `--title --context --why-user --recommend-reason --impact --consequence`，后两项按解析器累积。`cli.py:53–75`

### `goal` 与 `context` 子命令

每行均经 `cli.py:542–596,1062`。参数 `G` 为 `--project --execution-id --json --data JSON 或 --file PATH --title --request --leader --to --task --text --reason --item --kind --source --acceptance/--accept --constraint --dependency --priority INT --confirmed --start`，非 `create` 再有 `goal_id?`。其中验收条件、约束、依赖可重复传。`H` 为 `--html-file PATH`，`Q` 为 `--request-id`。注册在 `cli.py:305–331`。各动作走 A、P，执行和目标可从 E/G 补齐，具体所有者限制在 `goal_actions.dispatch`。`cli.py:579–593`、`goal_actions.py:165–237`

| 子命令 | 参数 | 实际动作实现 | 读写 | 传输或存储 | 用途 |
|---|---|---|---|---|---|
| `goal create` | G，无位置目标 | `goal_actions.py:212` | 写 | POST `/api/goals/agent` | 创建目标，可同时启动 |
| `goal status` | G | `goal_actions.py:200` | 读 | 直接 `dispatch` | 查目标状态 |
| `goal start` | G | `goal_actions.py:349` | 写 | 同一 POST | 启动目标 |
| `goal pause` | G | `goal_actions.py:353` | 写 | 同一 POST | 暂停目标 |
| `goal resume` | G | `goal_actions.py:355` | 写 | 同一 POST | 恢复目标 |
| `goal cancel` | G | `goal_actions.py:392` | 写 | 同一 POST | 取消目标 |
| `goal reopen` | G | `goal_actions.py:390` | 写 | 同一 POST | 重开目标 |
| `goal amend` | G | `goal_actions.py:309` | 写 | 同一 POST | 修订目标要求 |
| `goal assign` | G | `goal_actions.py:334` | 写 | 同一 POST | 分配执行人 |
| `goal transfer` | G | `goal_actions.py:375` | 写 | 同一 POST | 把目标交给另一 leader |
| `goal report-template` | G | `goal_actions.py:277` | 读 | 直接 `dispatch` | 读交付报告模板 |
| `goal report.get` | G | `goal_actions.py:279` | 读 | 直接 `dispatch` | 读指定目标报告 |
| `goal report.preview` | G＋H | `goal_actions.py:244` | 读＋临时状态 | POST `/api/goals/agent` | 在桌面生成预览截图 |
| `goal report.publish` | G＋H＋Q | `goal_actions.py:291` | 写 | 同一 POST | 发布报告版本 |
| `goal mode.set` | G | `goal_actions.py:288` | 写 | 同一 POST | 切换目标决策模式 |
| `goal finish` | G＋H＋Q | `goal_actions.py:398` | 写 | 同一 POST | 交付目标并提交报告 |
| `goal work.list` | G | `goal_actions.py:268` | 读 | 直接 `dispatch` | 查 AI 工作项 |
| `goal work.add` | G | `goal_actions.py:270` | 写 | POST `/api/goals/agent` | 新增 AI 工作项 |
| `goal work.update` | G | `goal_actions.py:273` | 写 | 同一 POST | 更新 AI 工作项状态或证据 |
| `goal versions.list` | G | `goal_artifacts.py:387` | 读 | 直接 `dispatch` | 列目标文件快照 |
| `goal versions.preview` | G | `goal_artifacts.py:389` | 读 | 直接 `dispatch` | 预览快照差异 |
| `goal versions.file` | G | `goal_artifacts.py:391` | 读 | 直接 `dispatch` | 读取快照文件 |
| `goal version.capture` | G | `goal_artifacts.py:405` | 写 | POST `/api/goals/agent` | 创建文件快照 |
| `goal version.restore` | G | `goal_artifacts.py:402` | 写 | 同一 POST | 按用户授权恢复快照 |
| `context inspect` | G | `goal_actions.py:295` | 读 | 直接 `dispatch` | 查看上下文摘要及来源 |
| `context retrieve` | G | `goal_actions.py:237` | 读 | 直接 `dispatch` | 检索可移植历史上下文 |
| `context put` | G | `goal_actions.py:297` | 写 | POST `/api/goals/agent` | 登记上下文条目 |
| `context correct` | G | `goal_actions.py:300,304` | 写 | 同一 POST | 修订上下文条目 |
| `context expire` | G | `goal_actions.py:300,307` | 写 | 同一 POST | 标记上下文失效 |

### `usage` 子命令

`cli.py:636–653` 截获 `usage` 并交给 `usage_cli.main`。共享参数 `U` 为 `--claude-root --codex-root --runs-file --period --project --json`，可在用量子解析器接受的位置指定，默认周期 `week`。用量子命令不依赖会话身份。`usage_cli.py:101–127`

| 子命令 | 参数 | 处理 | 读写 | 传输或存储 | 用途 |
|---|---|---|---|---|---|
| `usage scan` | U | `usage_cli.py:133` | 写 | 读原生日志并入库 | 扫描用量 |
| `usage summary` | U | `usage_cli.py:135,151` | 读 | 直接库查询 | 汇总总量 |
| `usage sessions` | U | `usage_cli.py:135–144` | 读 | 直接库查询 | 按会话汇总 |
| `usage daily` | U | `usage_cli.py:135–144` | 读 | 直接库查询 | 按日汇总 |
| `usage codex` | U | `usage_cli.py:138` | 读 | 直接库查询 | 列 Codex 运行用量 |
| `usage agents` | U | `usage_cli.py:142–144` | 读 | 直接库查询 | 按 agent 类型汇总 |
| `usage models` | U | `usage_cli.py:142–144` | 读 | 直接库查询 | 按模型汇总 |
| `usage export` | U＋`--what! {sessions,daily,codex,requests} --out!` | `usage_cli.py:145,158` | 读库、写文件 | 直接库查询＋CSV | 导出用量数据 |

## 3. Codex 动态工具

`L/E/G` 表示普通 leader、成员与目标执行、Git 助手的 Codex 新线程均取得该 schema。`P` 表示全局个人助手。Git 助手按 leader 建立，Git 管理写权限另由管理员绑定决定。注册只有 `assistant` 一个筛选维度，所以成员也可能看到运行时只允许 leader 的工具。旧线程走 `resume_thread`，该调用没有重新传 `dynamicTools`。独立运维线程走自己的 `start_thread(**params)`，没有接入这张表的工具注册。`codex_tools.py:235–238,337–357,392–403,444–490`、`codex_runtime.py:328–346,587–589`、`git_admin.py:118–142`、`operations_chat.py:215`

schema 中 `!` 表示必填。外层对象拒绝额外字段。`data` 在 `crew_personal`、`crew_goal`、`crew_context` 的外层 schema 允许动作特定字段；`crew_git` 另做动作级 schema 校验。`codex_tools.py:43–89,91–126,491–493`

| 工具 | 参数 schema 摘要与定义 | 实现 | HTTP、CLI 或内部映射 | 注册角色及运行限制 |
|---|---|---|---|---|
| `crew_personal` | `action! data{} message_id request_id` `codex_tools.py:91` | `codex_tools.py:446–453` | 注入 `chat.personal_action` → `personal_actions.dispatch`；CLI 对应 `personal`，写 HTTP 等价 `/api/assistant/native-action` | P；写需原始用户消息 |
| `crew_rename` | `name!` `codex_tools.py:106` | `codex_tools.py:469` | `session_ops.rename_self`；CLI `rename` | L/E/G；限当前 leader 自身 |
| `crew_archive` | `target!` `codex_tools.py:109` | `codex_tools.py:471–476` | POST `/api/session/archive`；CLI `archive` | L/E/G；限本组在岗 leader |
| `crew_git` | `action! data{}`。`status {}`；`policy {text}`；`requests {all}`；`branches {}`；`adopt {branch!}`；`request {description! branch blocking}`；`resolve {id! status! note! pr_number}` `codex_tools.py:68–89,111–120` | `codex_tools.py:491–517` | `board.git_config/git_policy/set_git_policy/git_requests/request_git_merge/resolve_git_request`、`git_overview.branches/adopt`；CLI `git` 对应子命令 | L/E/G；写规则和处理请求须 Git 管理员，认领须 leader |
| `crew_goal` | `action! goal_id data{}` `codex_tools.py:121` | `codex_tools.py:518–535` | `goal_controller.agent_action` → `goal_actions.dispatch`，动作见 §2 的 `goal`；HTTP 对应 `/api/goals/agent`，正常宿主路径直接调用 | L/E/G；目标所有者、执行门禁及动作权限 |
| `crew_context` | `action! goal_id data{}` `codex_tools.py:124` | `codex_tools.py:518–535` | 同 `crew_goal`，动作加 `context.` 前缀；CLI `context` | L/E/G；上下文动作权限 |
| `crew_whoami` | `{}` `codex_tools.py:127` | `codex_tools.py:454–456,536` | `board.whoami` 或助手上下文；CLI `whoami` | L/E/G/P |
| `crew_status` | `{}` `codex_tools.py:128` | `codex_tools.py:540` | 直接读 `board`；CLI `status` | L/E/G |
| `crew_who` | `target!` `codex_tools.py:130` | `codex_tools.py:538` | `_target/_identity`；CLI `who` | L/E/G，限当前工作区 |
| `crew_task` | `text!` `codex_tools.py:132` | `codex_tools.py:548` | `board.set_task`；CLI `task` | L/E/G，写自己 |
| `crew_card` | `scope done_when` `codex_tools.py:134` | `codex_tools.py:551` | 空参数读卡，给字段写卡；CLI `card` 只提供写，`whoami` 含卡 | L/E/G，操作自己的卡 |
| `crew_ask` | `question! options[] recommendation blocking brief{}` `codex_tools.py:138–145` | `codex_tools.py:557` | `board.ask`，可绑定执行；CLI `ask` | L/E/G |
| `crew_decisions` | `all:bool` `codex_tools.py:146` | `codex_tools.py:566` | `board.decisions`；CLI `decisions` | L/E/G |
| `crew_answer` | `id!:int text!` `codex_tools.py:148` | `codex_tools.py:595–604` | `board.answer`；CLI `answer`；HTTP 有独立的用户答复路由 | L/E/G；限原归属组在岗 leader |
| `crew_withdraw` | `id!:int` `codex_tools.py:152` | `codex_tools.py:605` | `board.withdraw`；CLI `withdraw` | L/E/G；须为原提问者 |
| `crew_decision_revise` | `id!:int question options[] recommendation brief{}` `codex_tools.py:154` | `codex_tools.py:568` | `decision_briefs.revise`；CLI `decision revise` | L/E/G；原提问者或原归属 leader |
| `crew_todo_add` | `title! steps! verification! blocking operation_key` `codex_tools.py:160` | `codex_tools.py:571` | `todo.add_native`；CLI `todo add` | L/E/G |
| `crew_todo_list` | `all:bool` `codex_tools.py:168` | `codex_tools.py:575` | `todo.list`；CLI `todo list`；GET `/api/todos` 有用户读视图 | L/E/G |
| `crew_todo_withdraw` | `id!:int reason!` `codex_tools.py:170` | `codex_tools.py:577` | `todo.withdraw`；CLI `todo withdraw` | L/E/G；提议者或拥有该事项的组 leader |
| `crew_doc_add` | `path! title! summary! kind:{summary,report,guide,other} important:bool` `codex_tools.py:174` | `codex_tools.py:579` | `documents.register`；CLI `doc add`；对应 POST `/api/documents` | L/E/G；当前身份和执行归属 |
| `crew_doc_list` | `leader` `codex_tools.py:182` | `codex_tools.py:584` | `documents.list`；CLI `doc list`；GET `/api/documents` 会额外刷新索引 | L/E/G |
| `crew_doc_refresh` | `{}` `codex_tools.py:184` | `codex_tools.py:593` | `documents.refresh`；CLI `doc refresh`；对应 POST `/api/documents/refresh` | L/E/G |
| `crew_handoff` | `to! text!` `codex_tools.py:186` | `codex_tools.py:609` | `board.handoff`；CLI `handoff` | L/E/G |
| `crew_handoffs` | `{}` `codex_tools.py:190` | `codex_tools.py:618` | `board.handoffs`；CLI `handoffs` | L/E/G |
| `crew_report` | `text!` `codex_tools.py:191` | `codex_tools.py:620` | `board.report`；CLI `report` | L/E/G |
| `crew_reports` | `group` `codex_tools.py:193` | `codex_tools.py:623` | `board.reports`；CLI `reports` | L/E/G |
| `crew_send_message` | `to! text! kind:{task,result,blocker,decision,user_answer,information,ack} reply_to` `codex_tools.py:195` | `codex_tools.py:628`、`codex_runtime.py:580–586` | 宿主消息路由；CLI `send` 经 POST `/api/messages` | L/E/G，同工作区、禁止发给自己 |
| `crew_say` | `text! to[] reply_to:int detail:bool` `codex_tools.py:205` | `codex_tools.py:458` | `channel.post`；CLI `say`；POST `/api/channel` 是用户身份入口 | L/E/G；执行限 leader |
| `crew_channel` | `limit:int ≥1`，默认 20 `codex_tools.py:217` | `codex_tools.py:461` | `channel.digest/read`；CLI `channel`；Web 读和已读分开 | L/E/G；执行限 leader |
| `crew_stop` | `target!` `codex_tools.py:220` | `codex_tools.py:477–482` | POST `/api/session/stop`；CLI `stop` | L/E/G；自己或本组成员 |
| `crew_spawn` | `name! task! goal_id runtime:{claude,codex} model effort new_worktree base` `codex_tools.py:222` | `codex_tools.py:477,483–490` | POST `/api/spawn`；CLI `spawn` | L/E/G；执行限 leader，按目标执行授权 |

`brief` 必填 `title`、`why_user`、`recommendation_reason`、`options`。可带 `context`、`impact`。`options` 有 2–12 项，每项要求 `label`、`consequence`。`codex_tools.py:55–67`

## 4. 助手动作

模型入口为 `crew_personal` 或 `crew personal`。Codex 正常路径调用 `chat.personal_action`。CLI 读直接进 `personal_actions.dispatch`，写经 `/api/assistant/native-action` 进入同一宿主。读写均验证有效全局助手。以下“原消息”表示强制提供原始用户 `message_id` 和稳定 `request_id`，宿主从自己的会话日志中核实唯一消息，来源须为 `human`、`qq` 或 `lark`，角色须为 `user`。代码没有统一的第二次确认弹窗，也没有从消息内容推导“哪一句授权了哪个动作”的通用校验。`personal_actions.py:57–76`、`personal.py:887–901,1028–1077`、`chat.py:1627–1642`、`cli.py:599–618`

| 动作 | `data` 参数 | 实现 | 对应路由或函数 | 用户确认 | 主要副作用或风险 |
|---|---|---|---|---|---|
| `summary` | `project? limit?`，默认 100 | `personal_actions.py:16` | `personal.overview` | 无 | 跨工作区概览 |
| `session.detail` | `session_id` | `personal_actions.py:33` | `personal._target`、`goals.list_goals/get` | 无 | 读取目标会话及任务 |
| `goal.detail` | `goal_id` | `personal_actions.py:41` | `goals.get` | 无 | 读取目标内容 |
| `documents.list` | `project`，`offset? limit? leader_session_id? goal_id?` | `personal_actions.py:45` | `documents.list`，无刷新副作用 | 无 | 读取工作区交付文档 |
| `notes.list` | `tags? mode? untagged? stage? stale_only? include_archived? project? limit? offset?` | `personal_actions.py:23`、`personal.py:280` | `personal.query_notes` | 无 | 可包含归档想法 |
| `notes.get` | `note_id`，拒绝额外键 | `personal_actions.py:25` | `personal.get_note` | 无 | 读取一条想法 |
| `tags.list` | `{}` | `personal_actions.py:9,29,67`、`personal.py:372` | `personal.list_tags`；GET `/api/personal/tags` 同底层 | 无 | 首读把既有标签登记进标签库 |
| `prompt.get` | `key scope? identity?` | `personal_actions.py:18` | `behavior_prompts.get`；GET `/api/behavior-prompts?action=get` | 无 | 读取当前提示词 |
| `prompt.history` | `key scope? identity?` | `personal_actions.py:18` | `behavior_prompts.history`；GET `/api/behavior-prompts?action=history` | 无 | 读取提示词历史 |
| `prompt.applied` | `scope_id` | `personal_actions.py:21` | `behavior_prompts.inspect_applied`；GET `/api/behavior-prompts?action=applied` | 无 | 读取实际应用的提示词记录 |
| `leader.create` | `project message name?` | `personal.py:920,988`、`chat.py:1627` | 宿主 `create_leader`；相近 POST `/api/leader` | 原消息 | 新建并可能启动 leader；外部失败记 `unknown` |
| `session.message` | `session_id text` | `personal.py:928,991`、`chat.py:1631` | 宿主 `send_message` 回调 → `chat.send`；相近 POST `/api/chat/send` | 原消息 | 以用户来源向目标输入，可唤醒执行；失败可能未知 |
| `session.stop` | `session_id` | `personal.py:928,993`、`chat.py:1637` | `chat.stop_identity`；对应 POST `/api/session/stop` | 原消息 | 停止目标执行；失败可能未知 |
| `settings.save` | `ai` | `personal.py:934,995` | `settings.save`；POST `/api/settings` 的 AI 分支 | 原消息 | 改未来会话使用的 AI 配置 |
| `prompt.save` | `key content expected_version scope? identity?` | `personal.py:936,997` | `behavior_prompts.save`；POST `/api/behavior-prompts` | 原消息 | 改提示词并生成版本 |
| `prompt.reset` | `key expected_version scope? identity?` | `personal.py:936,997` | `behavior_prompts.reset`；POST `/api/behavior-prompts` | 原消息 | 将提示词恢复为默认 |
| `decision.answer` | `decision_id answer` | `personal.py:942,1003,967` | `board.answer`＋`_route_answer`；相近 POST `/api/answer` | 原消息 | 记录拍板，通知原目标或 leader |
| `todo.resolve` | `todo_id status:{done,cancelled} project? note? expected_version?` | `personal.py:950,1012` | `todo.resolve`；对应 POST `/api/todo/resolve` | 原消息 | 完成或取消用户待办 |
| `note.capture` | `text title? tags? project?` | `personal.py:39,504,1019` | `_note_write`；想法墙 `capture` | 原消息 | 建新想法，记录授权来源 |
| `note.update` | `note_id expected_version text? title? tags? project? state?` | `personal.py:40,524,1019` | `_note_write`；想法墙 `update` | 原消息 | 改想法正文或元数据 |
| `note.annotate` | `note_id text` | `personal.py:49,519,1019` | `_note_write`；想法墙 `annotate` | 原消息 | 追加讨论结论及作者证据 |
| `note.archive` | `note_id expected_version` | `personal.py:44,553,1019` | `_note_write`；想法墙 `archive` | 原消息 | 从活跃想法移入归档 |
| `note.restore` | `note_id expected_version` | `personal.py:45,557,1019` | `_note_write`；想法墙 `restore` | 原消息 | 把归档想法移回 |
| `note.pin` | `note_id expected_version pinned` | `personal.py:41,545,1019` | `_note_write`；想法墙 `pin` | 原消息 | 改置顶顺序 |
| `note.snooze` | `note_id expected_version until` | `personal.py:42,549,1019` | `_note_write`；想法墙 `snooze` | 原消息 | 延后或恢复提醒 |
| `note.merge` | `primary_id note_ids expected_versions` | `personal.py:46,447,514,1019` | `_merge`；想法墙 `merge` | 原消息 | 合并多条想法及索引状态 |
| `note.bulk` | `note_ids expected_versions op value?`；`op` 为 `tag_add/tag_remove/archive/project` | `personal.py:48,58,468,516,1019` | `_bulk`；想法墙 `bulk` | 原消息 | 同时影响多条想法 |
| `tag.create` | `name color?` | `personal.py:50,616,1019` | `_tag_write`；标签页 `create` | 原消息 | 新建标签 |
| `tag.rename` | `name new_name expected_version` | `personal.py:54,646,1019` | `_tag_write`；标签页 `rename` | 原消息 | 改标签名并影响引用 |
| `tag.merge` | `from into` | `personal.py:55,656,1019` | `_tag_write`；标签页 `merge` | 原消息 | 合并标签及想法引用 |
| `tag.recolor` | `name color expected_version` | `personal.py:51,626,1019` | `_tag_write`；标签页 `recolor` | 原消息 | 改标签颜色 |
| `tag.remove` | `name expected_version` | `personal.py:56,613–674,1019` | `_tag_write`；标签页 `remove` | 原消息 | 删除标签并同步清理引用 |

`personal.py` 另外登记四个 **仅 UI 入口可提交的动作**。它们走 HTTP `/api/personal/notes` 或 `/api/personal/tags` 的 `action/data/request_id` 载荷。`personal.py:17–21,38–57,676–713`、`web_api/personal.py:67–82`

| 动作 | 参数 | 处理 | 路由 | 用户确认 | 副作用 |
|---|---|---|---|---|---|
| `note.keep` | `note_id expected_version` | `personal.py:551,699,703` | POST `/api/personal/notes`，`action=keep` | UI 点击，支持快捷键 | 重启回顾计时 |
| `note.unmerge` | `note_id expected_version` | `personal.py:497–569,699,703` | POST `/api/personal/notes`，`action=unmerge` | UI 撤销动作 | 拆回原想法 |
| `tag.pin` | `name pinned expected_version` | `personal.py:613–644,700,709` | POST `/api/personal/tags`，`action=pin` | UI 点击 | 改标签置顶 |
| `tag.reorder` | `names` | `personal.py:634,700,709` | POST `/api/personal/tags`，`action=reorder` | UI 移动标签 | 改标签顺序 |

## 5. 身份传递

### 环境变量的值读取

下表列生产源码和仓库集成脚本中本次查到的值读取。清除继承身份的操作随后单列。`CREW_HOME` 是数据目录选择，作用不同于身份。

| 文件:行号 | 原文 |
|---|---|
| `cli.py:520` | `sid = args.session_id if args.session_id is not None else (os.environ.get("CREW_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID"))` |
| `cli.py:527` | `sid = args.session_id if args.session_id is not None else (os.environ.get("CREW_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID"))` |
| `cli.py:534` | `if args.session_id is not None or os.environ.get("CREW_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID"):` |
| `cli.py:579` | `selected = getattr(args, 'goal_id', None) or os.environ.get('CREW_GOAL_ID')` |
| `cli.py:584` | `execution_id = args.execution_id or os.environ.get('CREW_EXECUTION_ID')` |
| `cli.py:601` | `bound = os.environ.get('CREW_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID')` |
| `cli.py:622` | `execution_id = os.environ.get('CREW_EXECUTION_ID')` |
| `cli.py:627` | `if os.environ.get('CREW_GOAL_ID') and os.environ['CREW_GOAL_ID'] != execution['goal_id']:` |
| `cli.py:722` | `host = os.environ.get('CREW_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID')` |
| `cli.py:748` | `host = os.environ.get('CREW_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID')` |
| `cli.py:759` | `bound = (args.session_id is not None or os.environ.get("CREW_SESSION_ID")` |
| `cli.py:760` | `or os.environ.get("CLAUDE_CODE_SESSION_ID"))` |
| `cli.py:827` | `actor=os.environ.get('CREW_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID') or _actor(args))` |
| `cli.py:954` | `host = os.environ.get('CREW_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID')` |
| `cli.py:1053` | `'execution_id': os.environ.get('CREW_EXECUTION_ID'), 'kind': args.kind,` |
| `spawn.py:271` | `host_caller = os.environ.get('CREW_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID')` |
| `spawn.py:304` | `if os.environ.get('CREW_EXECUTION_ID'):` |
| `spawn.py:305` | `body['caller_execution_id'] = os.environ['CREW_EXECUTION_ID']` |
| `hooks.py:69` | `execution_id = environ.get('CREW_EXECUTION_ID')` |
| `hooks.py:76` | `or execution['session_id'] != environ.get('CREW_SESSION_ID')):` |
| `hooks.py:154` | `if "CREW_NAME" in environ:` |
| `hooks.py:155` | `identity = {"name": environ["CREW_NAME"], "role": environ.get("CREW_ROLE", "member"),` |
| `hooks.py:156` | `"group_name": environ.get("CREW_GROUP"), "spawn_id": environ.get("CREW_SPAWN_ID")}` |
| `hooks.py:161` | `if event == 'user-prompt-submit' and not environ.get('CREW_SESSION_ID'):` |
| `hooks.py:168` | `if not environ.get('CREW_SESSION_ID'):` |
| `integrations/claude-dispatch/scripts/run_claude.ps1:117` | `codex_thread_id = $env:CODEX_THREAD_ID` |
| `integrations/claude-dispatch/scripts/run_claude.ps1:119` | `crew_session_id = $env:CREW_SESSION_ID` |
| `integrations/claude-dispatch/scripts/run_claude.ps1:120` | `crew_execution_id = $env:CREW_EXECUTION_ID` |
| `integrations/codex-dispatch/run_codex.ps1:100` | `claude_session_id = $env:CLAUDE_CODE_SESSION_ID` |
| `integrations/codex-dispatch/run_codex.ps1:101` | `crew_session_id   = $env:CREW_SESSION_ID` |
| `integrations/codex-dispatch/run_codex.ps1:102` | `crew_execution_id = $env:CREW_EXECUTION_ID` |

### 启动、守卫和 HTTP 绑定

| 环节 | 传递或判定 | 源码 |
|---|---|---|
| CLI 创建执行 | `spawn.cli_dispatch` 从宿主读取调用者，核对显式 `--session`，通过本机 HTTP 传 `caller_session_id`。目标内启动传 `caller_execution_id`。计划为新会话创建独立 ID。 | `spawn.py:128,244–316` |
| CC 总对话 | `spawn.command` 为 CC 构造 `--session-id` 或 `--resume`。`chat.child_environment` 构建子环境并注入角色和 spawn 信息，`chat` 再注入本次逻辑 `CREW_SESSION_ID` 和 `CREW_HOME`。 | `spawn.py:26–54`、`chat.py:194–215,1048–1053` |
| CC 目标执行 | `goal_runtime` 将逻辑会话、目标执行及目标 ID 分别注入 `CREW_SESSION_ID`、`CREW_EXECUTION_ID`、`CREW_GOAL_ID`。原生执行另有自己的 CC ID。 | `goal_runtime.py:313–316`、`hooks.py:67–78` |
| Codex | `CodexRuntime` 启动 app-server 时注入 `CREW_HOME`、`CREW_SESSION_ID`、`CREW_NAME`、`CREW_ROLE`；目标执行再注入 E/G。守卫命令行也固定 `--home --session` 及可选 `--execution`。 | `codex_runtime.py:170–172,290–295,325–329` |
| Codex 工具身份 | app-server 请求须匹配宿主线程，工具 handler 以宿主对象的 session/execution 绑定，校验工作区、执行所属、载体及归档状态，传输层工具参数不决定调用者。 | `codex_runtime.py:570–591`、`codex_tools.py:315–358,374–403` |
| CC hooks 普通会话 | hook 以载体 payload 的 `session_id` 进入 `guard.check`。环境中缺少 `CREW_SESSION_ID` 时按外部 CC 会话处理部分用户输入和授权上下文。 | `hooks.py:97–114,150–170` |
| CC hooks 目标执行 | 先匹配 E、原生 CC ID 和逻辑会话 S，校验结果后用逻辑会话交给守卫和分支登记。不能匹配时不会当成有效目标执行。 | `hooks.py:67–94` |
| `crew` 命令守卫 | `hooks.py` 调用 `guard.check`。总对话已把任务交给目标执行时，`guard._crew_coordination` 识别规范的单条 `crew` 命令或指向当前 HOME 的 `crew.py` 调用，保留协作入口。其他 Git、资源、文件与目标门禁按各自规则继续处理。 | `hooks.py:79–91,108–115`、`guard.py:25–53,172–233` |
| Codex shell 守卫 | 载体 hook 先验证线程及绑定会话、执行，再把 payload 的 `session_id` 代换为逻辑 Crew ID，交给通用守卫。 | `codex_guard.py:98–118,131–152` |
| 本机 HTTP | 校验本机入口来源。CLI 用请求体传稳定 Crew ID，`X-Crew: 1` 是请求边界条件。`/api/messages` 和目标 agent 路由各自查库验证调用者。 | `spawn.py:244–252`、`web.py:1456,2103,2232,2473`、`goal_api.py:114–128` |
| 远程 HTTP | 浏览器读取 Cookie，远程桌面读取 `Authorization: Bearer …`，校验设备种类、有效期及存储摘要。远程业务请求随后按用户身份做载荷限制。 | `remote_access.py:263–295,379–436`、`remote_routes.py:54–73`、`D/core.cjs:99–105,164` |
| 剥离继承身份 | 托管子进程、运维和独立工具进程清理外层身份，再按需要重新绑定。集成派发脚本先记录来源，再清理传给新进程的会话身份。 | `chat.py:194–212`、`codex_runtime.py:290`、`channel_digest.py:45,83`、`native_skills.py:30`、`operations_chat.py:103`、`operations_worker.py:29`、`D/operations.cjs:19`、`integrations/claude-dispatch/scripts/run_claude.ps1:133`、`integrations/codex-dispatch/run_codex.ps1:117` |

## 6. 前端和桌面非 GET 调用

### `frontend/*.js`

`F/app.js:200–202`、`F/assistant.js:11–12`、`F/notes.js:20–21` 根据有无请求体选择 POST 或 GET。UI 组件常通过 `options.api` 或 `options.save` 把数据传给这些封装。下表把请求位置与触发点同时列出。当前这一范围的非 GET 请求均为 POST。

| 文件:行号 | 方法 | 路径 | 触发动作 |
|---|---|---|---|
| `F/app.js:292` | POST | `/api/leaders/order` | 拖动或改变 leader 置顶顺序 |
| `F/app.js:808` | POST | `/api/todo/resolve` | 完成或取消人工待办 |
| `F/app.js:812` | POST | `/api/todo/undo` | 点击待办处理后的撤销 |
| `F/app.js:1067` | POST | `/api/session/appearance` | 保存会话外观 |
| `F/app.js:1080` | POST | `/api/session/rename` | 提交重命名 |
| `F/app.js:1106` | POST | `/api/trust` | 保存授权表单 |
| `F/app.js:1146` | POST | `/api/connections/configure` | 保存连接配置 |
| `F/app.js:1147` | POST | `/api/connections/action` | 执行连接器面板操作 |
| `F/app.js:1148` | POST | `/api/connections/preferences` | 保存连接偏好 |
| `F/app.js:1149` | POST | `/api/notifications/away` | 连接设置中切换离开状态 |
| `F/app.js:1155` | POST | `/api/storage/preview` | 预览待清理数据 |
| `F/app.js:1156` | POST | `/api/storage/clean` | 执行清理 |
| `F/app.js:1157` | POST | `/api/storage/restore` | 恢复清理批次 |
| `F/app.js:1158` | POST | `/api/storage/trash/empty` | 清空回收站 |
| `F/app.js:1159` | POST | `/api/storage/policy` | 保存存储策略 |
| `F/app.js:1205` | POST | `/api/session/compact` | 点击压缩上下文，使用 `after_turn:true` |
| `F/app.js:1281` | POST | `/api/claude-budget` | 保存 Claude 预算 |
| `F/app.js:1396` | POST | `/api/answer` | 提交用户拍板 |
| `F/app.js:1400,1663` | POST | `/api/session/archive` | 会话菜单归档 |
| `F/app.js:1401` | POST | `/api/session/undo` | 归档反馈中的撤销 |
| `F/app.js:1423`、`F/git-settings.js:53,71` | POST | `/api/git/settings` | 保存 Git 设置或确认停用 |
| `F/app.js:1427` | POST | `/api/project/update` | 保存工作区设置或迁移目录 |
| `F/app.js:1427` | POST | `/api/project/remove` | 确认移除工作区 |
| `F/app.js:1431` | POST | `/api/settings` | 保存 AI 或执行设置 |
| `F/app.js:1433` | POST | `/api/admin/chat/send` | 发送运维对话请求 |
| `F/app.js:1434` | POST | `/api/admin/chat/interrupt` | 中断运维对话 |
| `F/app.js:1435` | POST | `/api/admin/config` | 保存运维配置 |
| `F/app.js:1440` | POST | `/api/settings` | 保存声音设置 |
| `F/app.js:1441` | POST | `/api/remote-access` | 启停远程访问、管理配对或设备 |
| `F/app.js:1441` | POST | `/api/behavior-prompts` | 保存或重置行为配置 |
| `F/app.js:1462`、`F/runtime-switch-ui.js:84` | POST | `/api/session/runtime/prepare` | 检查切换方案 |
| `F/app.js:1463`、`F/runtime-switch-ui.js:91` | POST | `/api/session/runtime/switch` | 确认切换执行载体 |
| `F/app.js:1536` | POST | `/api/trust` | 从会话操作切换 leader 授权 |
| `F/app.js:1552` | POST | `/api/group/close` | 结束 leader 或整个组 |
| `F/app.js:1554` | POST | `/api/group/undo` | 撤销结束组 |
| `F/app.js:1556` | POST | `/api/wake` | 点醒会话 |
| `F/app.js:1585` | POST | `/api/decision/clarify` | 请 leader 补充说明 |
| `F/app.js:1588` | POST | `/api/decision/discuss` | 开始拍板讨论 |
| `F/app.js:1588` | POST | `/api/decision/ready` | 结束讨论并标为可拍板 |
| `F/app.js:1594` | POST | `/api/notifications/away` | 点击离开状态开关 |
| `F/app.js:1597` | POST | `/api/project/theme` | 切换工作区主题 |
| `F/app.js:1651` | POST | `/api/session/options` | 在会话菜单改模型、推理或速度 |
| `F/app.js:1661` | POST | `/api/roster/move` | 菜单选择转组 |
| `F/app.js:1662` | POST | `/api/roster/remove` | 菜单选择退组 |
| `F/app.js:1666` | POST | `/api/session/delete` | 确认删除会话 |
| `F/app.js:1670` | POST | `/api/session/stop` | 菜单停止会话 |
| `F/app.js:1696` | POST | `/api/leader` | 提交新建 leader 表单 |
| `F/architecture.js:320` | POST | `/api/domains` | 提交分区图编辑操作 |
| `F/assistant.js:41` | POST | `/api/assistant/open` | 首次打开全局助手页 |
| `F/assistant.js:64` | POST | `/api/behavior-prompts` | 助手行为配置保存或重置 |
| `F/channel.js:80` | POST | `/api/channel/digest` | 生成交流区摘要 |
| `F/channel.js:113` | POST | `/api/channel/read` | 阅读交流区时确认已读 |
| `F/channel.js:183` | POST | `/api/channel` | 发送交流区消息及点名 |
| `F/chat.js:273` | POST | `/api/files/resolve` | 解析聊天里的文件引用 |
| `F/chat.js:283` | POST | `/api/files/open` | 打开引用的主机文件 |
| `F/chat.js:314` | POST | `/api/files/open` | 文件操作选择打开 |
| `F/chat.js:314` | POST | `/api/files/reveal` | 文件操作选择目录定位 |
| `F/chat.js:481` | POST | `/api/session/stop` | 聊天中的停止操作 |
| `F/chat.js:859` | POST | `/api/chat/read` | 聊天已读位置同步 |
| `F/chat.js:1032` | POST | `/api/chat/send` | 提交聊天、目标或助手输入 |
| `F/documents.js:28` | POST | `/api/files/resolve` | 查看资料文件前解析路径 |
| `F/documents.js:37` | POST | `/api/documents/pin` | 置顶或取消资料重要标记 |
| `F/documents.js:51` | POST | `/api/files/open` | 打开资料原文件 |
| `F/git.js:156,169` | POST | `/api/git/fetch` | 点击拉取远端 |
| `F/git.js:156,177` | POST | `/api/git/enable` | 提交启用 Git 助手 |
| `F/git.js:156,181` | POST | `/api/git/adopt` | 下拉选中接手分支的 leader |
| `F/goal-ui.js:281` | POST | `/api/goals/action` | 目标状态、转交、观察和交付验收按钮 |
| `F/goal-ui.js:320` | POST | `/api/goals/create` | 提交新建目标 |
| `F/goal-ui.js:334` | POST | `/api/goals/agent` | 切换目标决策模式 |
| `F/goal-ui.js:343` | POST | `/api/goals/report/folder` | 打开报告目录 |
| `F/maintenance-ui.js:244` | POST | `/api/maintenance/department/preview` | 预览部门压缩对象 |
| `F/maintenance-ui.js:245` | POST | `/api/maintenance/cleanup/preview` | 预览工作区清理 |
| `F/maintenance-ui.js:261` | POST | `/api/maintenance/department/compact` | 确认压缩选中的部门会话 |
| `F/maintenance-ui.js:262` | POST | `/api/maintenance/cleanup` | 确认归档清理 |
| `F/member-ui.js:79` | POST | `/api/member-defaults` | 保存默认成员配置 |
| `F/native-approval-ui.js:28` | POST | `/api/native-approval` | 同意或拒绝原生审批请求 |
| `F/notes.js:26,252` | POST | `/api/personal/notes` | 编辑想法 |
| `F/notes.js:271` | POST | `/api/personal/notes/start` | 把想法发起为工作 |
| `F/notes.js:294` | POST | `/api/personal/notes/recovery` | 选中并绑定原讨论 |
| `F/notes.js:26,312` | POST | `/api/personal/notes` | 追加讨论结论 |
| `F/notes.js:26,344` | POST | `/api/personal/notes` | 合并想法 |
| `F/notes.js:26,380` | POST | `/api/personal/notes` | 单条想法加减标签 |
| `F/notes.js:26,381` | POST | `/api/personal/notes` | 多条想法加减标签 |
| `F/notes.js:27,414` | POST | `/api/personal/tags` | 新建标签 |
| `F/notes.js:27,419` | POST | `/api/personal/tags` | 改标签颜色 |
| `F/notes.js:27,420` | POST | `/api/personal/tags` | 切换标签置顶 |
| `F/notes.js:27,424` | POST | `/api/personal/tags` | 移动标签顺序 |
| `F/notes.js:27,429` | POST | `/api/personal/tags` | 删除标签 |
| `F/notes.js:27,439` | POST | `/api/personal/tags` | 重命名标签 |
| `F/notes.js:27,444` | POST | `/api/personal/tags` | 确认合并标签 |
| `F/notes.js:26,462` | POST | `/api/personal/notes` | 批量改想法所属工作区 |
| `F/notes.js:26,513` | POST | `/api/personal/notes` | 置顶或取消置顶想法 |
| `F/notes.js:26,522` | POST | `/api/personal/notes` | 归档想法或从反馈中撤销 |
| `F/notes.js:26,523` | POST | `/api/personal/notes` | 保留想法并重启回顾计时 |
| `F/notes.js:26,526` | POST | `/api/personal/notes` | 暂缓想法或取消暂缓 |
| `F/notes.js:26,579–580` | POST | `/api/personal/notes` | 快捷键 `K/A/H` 保留、归档、暂缓 |
| `F/notes.js:644` | POST | `/api/personal/notes` | 想法墙录入新想法 |
| `F/notes.js:26,665` | POST | `/api/personal/notes` | 标签选择器更新单条想法 |
| `F/notes.js:26,666` | POST | `/api/personal/notes` | 标签选择器更新多条想法 |
| `F/notes.js:26,702` | POST | `/api/personal/notes` | 恢复归档想法 |
| `F/notes.js:26,703` | POST | `/api/personal/notes` | 撤销想法合并 |
| `F/notes.js:26,712` | POST | `/api/personal/notes` | 批量归档 |
| `F/personal-notes-ui.js:25` | POST | `/api/personal/notes` | 快速记录面板录入想法 |
| `F/session-library.js:111` | POST | `/api/session-library/adopt` | 会话库收编 |
| `F/sound-settings-ui.js:75` | POST | `/api/sounds/custom` | 上传自定义提示音 |
| `F/sound-settings-ui.js:84` | POST | `/api/sounds/custom/delete` | 删除自定义提示音 |
| `F/stage-report-ui.js:16` | POST | `/api/goals/agent` | 保存用户编辑的报告版本 |
| `F/storage-history.js:75` | POST | `/api/workspace-data/export` | 从历史页申请导出再下载 |

范围补充，登录页脚本在 `F/remote-login.html:11`，提交配对表单时 POST `/remote/login`。它属于实际浏览器调用，文件扩展名在本节 JS 范围之外。

### `desktop/src/*.cjs` 与 `home*.js`

`D/core.cjs:99–121` 是通用 HTTP 入口，有请求体时发 POST。`home*.js` 的相关功能通过 preload/IPC 进入主进程，当前未发现它们直接发 Crew 非 GET HTTP。桌面运维聊天与运维设置的本机 IPC 走本地 worker，例如 `D/main.cjs:538–545`、`D/operations.cjs:14`。

| 文件:行号 | 方法 | 路径 | 触发动作 |
|---|---|---|---|
| `D/core.cjs:164` | POST | `/remote/login` | 配对远程桌面后端 |
| `D/core.cjs:402` | POST | `/api/host/workspace/existing` | 在主机打开或注册已有目录 |
| `D/core.cjs:406` | POST | `/api/host/workspace/new` | 在主机新建工作区 |
| `D/crew-updates.cjs:152` | POST | `/api/admin/update/prepare` | 更新前准备 |
| `D/crew-updates.cjs:192` | POST | `/api/admin/update/cancel` | 更新流程异常后的门禁清理 |
| `D/crew-updates.cjs:210` | POST | `/api/admin/update/cancel` | 释放前一个更新门禁 |
| `D/crew-updates.cjs:214` | POST | `/api/admin/update/prepare` | 重新准备更新 |
| `D/launch-settings.cjs:254` | POST | `/api/admin/connection/prepare` | 应用连接、数据目录或后台来源变更 |
| `D/main.cjs:276` | POST | `/api/workspace-data/export` | 从远程桌面后端申请导出包 |
| `D/main.cjs:281` | POST | `/api/workspace-data/export` | 本机导出到保存对话框指定位置 |
| `D/main.cjs:289` | POST | `/api/workspace-data/preview` | 打开导入包后预览 |
| `D/main.cjs:294` | POST | `/api/workspace-data/import` | 以只读恢复或分叉方式导入 |
| `D/main.cjs:311` | POST | `/api/workspace-data/activate` | 选目录并激活历史工作区 |
| `D/main.cjs:325` | POST | `/api/workspace-data/handoff` | 导出交接工作区 |
| `D/main.cjs:655` | POST | `/api/behavior-prompts` | 桌面 IPC 保存或重置提示词 |
| `D/main.cjs:665` | POST | `/api/settings` | 桌面设置 HTTP 分支保存 AI 设置 |
| `D/main.cjs:679` | POST | `/api/connections/configure` | 桌面 IPC 保存连接 |
| `D/main.cjs:680` | POST | `/api/connections/action` | 桌面 IPC 执行连接器操作 |
| `D/main.cjs:681` | POST | `/api/connections/preferences` | 桌面 IPC 保存连接偏好 |
| `D/main.cjs:683` | POST | `/api/remote-access` | 桌面远程访问设置操作 |
| `D/main.cjs:686` | POST | `/api/notifications/away` | 切换离开状态 |
| `D/main.cjs:689` | POST | `/api/storage/preview` | 存储清理预览 |
| `D/main.cjs:690` | POST | `/api/storage/clean` | 清理存储 |
| `D/main.cjs:691` | POST | `/api/storage/restore` | 恢复批次 |
| `D/main.cjs:692` | POST | `/api/storage/trash/empty` | 清空回收站 |
| `D/main.cjs:693` | POST | `/api/storage/policy` | 保存清理策略 |
| `D/main.cjs:718–720` | POST | `/api/maintenance/cleanup/preview` | 桌面维护 IPC 预览工作区清理 |
| `D/main.cjs:718–720` | POST | `/api/maintenance/cleanup` | 桌面维护 IPC 执行工作区清理 |
| `D/main.cjs:727` | POST | `/api/project/update` | 保存桌面工作区设置 |
| `D/main.cjs:731` | POST | `/api/project/remove` | 移除桌面工作区 |
| `D/report-preview.cjs:98,114` | POST | `/api/report-preview/worker/register` | 桌面截图 worker 启动登记 |
| `D/report-preview.cjs:98,141` | POST | `/api/report-preview/worker/next` | 截图 worker 轮询并领任务 |
| `D/report-preview.cjs:98,164` | POST | `/api/report-preview/worker/result` | 截图 worker 提交结果 |
| `D/report-preview.cjs:98,107,175` | POST | `/api/report-preview/worker/close` | 桌面关闭 worker |
| `D/storage.cjs:10` | POST | `/api/workspace-data/open` | 按身份选择打开目录 |
| `D/storage.cjs:12` | POST | `/api/workspace-data/open` | 发现状态为已就绪时直接打开 |
| `D/storage.cjs:16` | POST | `/api/workspace-data/open` | 用户选择重定位历史 |
| `D/storage.cjs:23` | POST | `/api/workspace-data/open` | 核实所选历史后打开 |
| `D/storage.cjs:28` | POST | `/api/workspace-data/discover` | 准备打开登记过的工作区 |
| `D/storage.cjs:33` | POST | `/api/workspace-data/discover` | 探测用户选中的目录 |

## 7. 交叉核对

### 有写入或状态副作用，但当前没有入口调用方的 HTTP 路由

这里的“调用方”覆盖前端、桌面、CLI、动态工具。共享底层函数不算调用该 URL。

| 路由 | 核对结果 | 依据 |
|---|---|---|
| POST `/api/project/init` | CLI `init` 与 `start` 直接注册；桌面使用主机目录或工作区数据入口 | `web.py:1340`、`cli.py:703`、`startup.py:31–37`、`D/core.cjs:402,406` |
| POST `/api/attention/dismiss` | 已注册，未找到上述入口发送 | `web.py:1483,2006` |
| POST `/api/chat/interrupt` | 已注册。当前聊天停止按钮调用 `/api/session/stop`，运维中断走自己的路由或 IPC | `web.py:1682`、`F/chat.js:481`、`F/app.js:1434`、`D/main.cjs:544` |
| POST `/api/comments` | 已注册，未找到上述入口发送 | `web.py:1434,2002` |
| POST `/api/documents` | CLI 和工具都直接调用 `documents.register` | `web.py:1389`、`cli.py:855`、`codex_tools.py:579` |
| POST `/api/documents/refresh` | CLI 和工具都直接调 `documents.refresh` | `web.py:1397`、`cli.py:860`、`codex_tools.py:593` |
| POST `/api/goals/context` | CLI 用 `/api/goals/agent`，Codex 调内部目标控制器 | `goal_api.py:201`、`cli.py:592`、`codex_tools.py:518–535` |
| POST `/api/roster/adopt` | 当前会话库 UI 调 `/api/session-library/adopt`，新建 leader 的原生候选成员也有独立分支 | `web.py:1620,1725,1728`、`F/session-library.js:111` |
| POST `/api/session/restore` | 归档后的当前 UI 撤销走 `/api/session/undo` | `web.py:1518,1520`、`F/app.js:1401,1663` |
| POST `/remote/logout` | 后端定义了设备退出，未找到上述入口发送 | `remote_access.py:420–425` |
| GET `/api/documents` | 含索引刷新副作用，当前未发现独立 GET 调用点 | `web.py:1842–1851,2366` |

以下 POST 有明确的非 UI 调用，不能列为无调用方。`/api/assistant/native-action` 被 `crew personal` 写动作调用，`/api/messages` 被 `crew send` 调用，`/api/spawn` 被 `crew spawn` 和 `crew_spawn` 调用，`/api/git/disable` 被 `crew git disable` 调用。`cli.py:616,884,1051`、`spawn.py:316`、`codex_tools.py:490`

### 直接写库且缺少动作等价 HTTP 入口

“动作等价”要求授权对象和主要副作用一致。能在 `/api/state` 看到结果、能由用户做相近操作、或有一个总入口能分派其他动作，都不足以算同一个 HTTP 动作。

| 业务 | CLI | Codex 工具 | 依据 |
|---|---|---|---|
| 收编当前宿主会话 | `join` | 无 | `cli.py:717–742`。Web 收编是选择候选或会话库记录，`web.py:1725–1729` |
| 更新自己的当前工作描述 | `task` | `crew_task` | `cli.py:752`、`codex_tools.py:548` |
| 写自己的任务卡 | `card` | `crew_card` | `cli.py:667,754`、`codex_tools.py:551` |
| 修改 Git 合并政策正文 | `git policy --set` | `crew_git policy` 有 `data.text` | `cli.py:885`、`codex_tools.py:498`；Web Git 设置职责在 `git_admin.py:87` |
| 申请合并 | `git request` | `crew_git request` | `cli.py:888`、`codex_tools.py:513` |
| 登记合并处理结果 | `git resolve` | `crew_git resolve` | `cli.py:901`、`codex_tools.py:516` |
| 新增资源 | `resource add` | 无 | `cli.py:907–909` |
| 删除资源 | `resource rm` | 无 | `cli.py:910–911` |
| 领取资源租约 | `lease take` | 无 | `cli.py:934–938` |
| 释放资源租约 | `lease give` | 无 | `cli.py:940–941` |
| 声明文件范围 | `claim` | 无 | `cli.py:831` |
| 解除文件范围声明 | `unclaim` | 无 | `cli.py:833` |
| 创建拍板 | `ask` | `crew_ask` | `cli.py:779–790`、`codex_tools.py:557–565` |
| 原位补写拍板 | `decision revise` | `crew_decision_revise` | `cli.py:791–805`、`codex_tools.py:568`。Web `clarify` 是请求 leader 补写，`web.py:1425` |
| 撤回拍板 | `withdraw` | `crew_withdraw` | `cli.py:777`、`codex_tools.py:605` |
| leader 代表自己的权限答复拍板 | `answer` 的绑定身份分支 | `crew_answer` | `cli.py:758–775`、`codex_tools.py:595`；Web `/api/answer` 是用户触发及后续路由 |
| 登记白板交接 | `handoff` | `crew_handoff` | `cli.py:835`、`codex_tools.py:609`。目标转交和工作区打包交接是不同动作 |
| 提交进度报告 | `report` | `crew_report` | `cli.py:839`、`codex_tools.py:620`。目标交付报告走目标动作 |
| 登记人工待办 | `todo add` | `crew_todo_add` | `cli.py:845–848`、`codex_tools.py:571` |
| 撤回人工待办 | `todo withdraw` | `crew_todo_withdraw` | `cli.py:849`、`codex_tools.py:577`。HTTP `resolve(cancelled)` 是用户处理 |
| 批量加人进组 | `roster add` | 无 | `cli.py:951`；Web 只有转组、退组和两种收编入口 |
| 以 leader 身份发交流区消息 | `say` | `crew_say` | `cli.py:871`、`codex_tools.py:458`；Web 走固定用户身份 |
| 以会话身份读交流区并推进其游标 | `channel` | `crew_channel` | `cli.py:1085`、`codex_tools.py:461`、`channel.py:255`；Web GET 与用户已读 POST 分开 |
| 独立扫描用量 | `usage scan` | 无 | `usage_cli.py:133`；HTTP 两个用量 GET 将扫描内嵌在读请求 |
| 安装 Crew 集成并记录事件 | `install` | 无 | `cli.py:1094–1097` |
| 安装或卸载系统自启、菜单 | `autostart install/uninstall`、`menu install/uninstall` | 无 | `cli.py:992–999`；系统状态变更，与普通数据库写不同 |

只读入口中，以下也缺少等价的独立 HTTP 动作。本机 `web` 是服务启动命令，`start` 是“注册＋探测＋启动＋打开页面”的组合，两项都没有一个可直接替换的业务 URL。`cli.py:1001–1018`、`startup.py:25–60`

| 只读动作 | CLI 或工具 | 依据 |
|---|---|---|
| 以绑定身份查自己及待办作用域 | `whoami`、`crew_whoami`；`crew_card {}` 查自己的卡 | `cli.py:743`、`codex_tools.py:454,536,551` |
| 精确查同工作区同事 | `who`、`crew_who` | `cli.py:922`、`codex_tools.py:538` |
| 查共享资源、租约、声明 | `resource list`、`lease list`、`claims` | `cli.py:912,932,942` |
| 分组专用查询 | `group list` | `cli.py:949`；主状态可附带分组 |
| 拍板专用查询及历史 | `decisions`、`crew_decisions` | `cli.py:944`、`codex_tools.py:566`；主状态有拍板视图 |
| 读自己的待交接 | `handoffs`、`crew_handoffs` | `cli.py:837`、`codex_tools.py:618` |
| 读进度报告 | `reports`、`crew_reports` | `cli.py:947`、`codex_tools.py:623` |
| 查 Git 政策和请求专用视图 | `git policy`、`git requests`、`crew_git policy/requests` | `cli.py:885,893`、`codex_tools.py:498–502`；GET `/api/git` 是综合视图 |
| 查 leader 当前授权的专用读取 | `trust status` | `cli.py:830`；HTTP 另有授权报告 |
| 查白板事件 | `events` | `cli.py:966`；聊天 SSE 是另一种日志 |
| 查系统菜单、自启和本地诊断 | `menu status`、`autostart status`、`doctor` | `cli.py:973,992–999`；HTTP 健康接口只描述运行中服务 |
| 助手按会话、目标、便笺或文档执行专用读动作 | `personal summary/session.detail/goal.detail/documents.list/notes.list/notes.get`、`crew_personal` 同动作 | `personal_actions.py:16–53`。部分有相近 GET，查询范围或副作用有差异；`/api/assistant/native-action` 也可分派这些读动作 |

### 业务动作的多路径对照

数量按本次范围内可区分的入口或语义分支计算。桌面和浏览器请求同一个 URL 时算一条 HTTP 路径。只有底层业务原语相同的入口也列出来，便于判断合并时要保留哪些差异。

| 业务动作 | 路径数与对应实现 | 合并清单时要保留的区别 |
|---|---|---|
| 注册、发现或打开工作区 | **6**：CLI `init`，CLI `start`，POST `/api/project/init`，`/api/host/workspace/existing`，`/api/host/workspace/new`，`/api/workspace-data/open`。`cli.py:703,1001`、`startup.py:25`、`web.py:1114,1179,1340–1349` | 同属工作区建立流程。可能只注册，也可能创建目录、迁移历史或拉起服务 |
| 查工作区列表 | **2**：GET `/api/projects`、CLI `projects`。`web.py:2361`、`cli.py:715` | HTTP 补充目录状态及已移除筛选 |
| 查工作区 / 会话状态 | **3**：GET `/api/state`、CLI `status`、`crew_status`。`web.py:646,2321`、`cli.py:913`、`codex_tools.py:540` | Web 综合状态、CLI 载体信息和工具记录视图各有字段 |
| 建 leader | **3 个入口**：Web/CLI 经 `/api/leader`，助手 `leader.create` 经 `chat.personal_action`，想法墙 `/api/personal/notes/start`。`web.py:1158,1603`、`spawn.py:293`、`personal.py:988`、`chat.py:1627` | 助手要原始用户消息，想法发起要绑定想法及请求回执 |
| 建成员或目标执行 | **1 个 HTTP 路径、2 个现行 AI 调用端**：CLI `spawn`、`crew_spawn` 均经 `/api/spawn`。`web.py:1580`、`spawn.py:316`、`codex_tools.py:483–490` | CLI dry-run 单独计算计划。工具由宿主绑定调用者及目标执行 |
| 改会话名称 | **3 个入口、2 个语义**：Web `/api/session/rename`、CLI `rename`、`crew_rename`。`web.py:1507`、`cli.py:745–751`、`codex_tools.py:469–470` | Web 按指定会话改名，AI 入口限制当前 leader 自改 |
| 收编与组员变化 | **8**：CLI `join`、`roster add/remove/move`，HTTP `/api/roster/adopt`、`/api/session-library/adopt`、`/api/roster/remove`、`/api/roster/move`。`cli.py:717,951`、`web.py:1704–1729` | 区分原生身份收编、批量直接改 roster、Web 单会话及撤销回执 |
| 归档会话 | **1 个业务 HTTP 入口，3 个调用端**：Web、CLI `archive`、`crew_archive`。`web.py:1511,1733`、`cli.py:961`、`codex_tools.py:471` | AI 入口附调用者并做本组权限校验 |
| 结束组 | **1 个业务 HTTP 入口，2 个调用端**：Web、CLI `leader close`。`web.py:1718`、`cli.py:957` | `with_members` 决定范围，撤销是 `/api/group/undo` |
| 停止会话 | **2 条实现链**：Web、CLI `stop`、`crew_stop` 经 `/api/session/stop`；助手 `session.stop` 经宿主直调。`web.py:1522`、`cli.py:1066`、`codex_tools.py:479`、`chat.py:1637` | 同一底层停止，个人助手使用原用户授权和动作回执。`/api/chat/interrupt` 是另一种操作 |
| 唤醒与压缩 | 每项 **1 个业务 HTTP 入口、2 个调用端**：Web 与 CLI `wake` 共用 `/api/wake`，Web 与 CLI `compact` 共用 `/api/session/compact`。`web.py:1524,1569`、`cli.py:1019,1076`、`F/app.js:1205,1556` | 压缩 Web 请求支持 `after_turn`，路由也可选择目标执行 |
| 人类聊天与代发 | **2 条宿主入口**：Web `/api/chat/send` 和助手 `session.message`。`web.py:1640–1680`、`chat.py:1631–1635` | 前者处理附件、目标与想法讨论，后者携带授权、稳定请求标识 |
| Crew 协作消息 | **2 条传输路径**：CLI `send` 经 `/api/messages`，`crew_send_message` 由 app-server 宿主调用消息路由。`cli.py:1051`、`web.py:1456`、`codex_tools.py:628`、`codex_runtime.py:580` | CLI 允许调用方提供稳定 `message_id`，Codex 由宿主工具调用绑定；保留 `kind/reply_to` |
| 交流区发言 | **3**：Web `/api/channel`，CLI `say`，`crew_say`。`web.py:1473`、`cli.py:871`、`codex_tools.py:458` | 共用 `channel.post`，身份与可传回复字段不同 |
| 交流区阅读 | **3**：Web GET `/api/channel`＋POST `/api/channel/read`，CLI `channel`，`crew_channel`。`web.py:1477,2398`、`cli.py:1085`、`codex_tools.py:461` | Web 以用户身份分两步；CLI/工具合成读及已读 |
| 拍板创建、原位修订、撤回 | 每项 **2**：CLI 与 Codex 工具直接调库。`cli.py:777–805`、`codex_tools.py:557–570,605–607` | Web 的讨论、就绪和请补充是独立动作 |
| 拍板答复 | **4 个入口**：Web `/api/answer`、CLI `answer`、`crew_answer`、助手 `decision.answer`。`web.py:1550`、`cli.py:758`、`codex_tools.py:595`、`personal.py:1003` | 共享 `board.answer`。Web 和助手有用户来源及后续路由，AI 原归属 leader 答复有单独校验 |
| 授权管理 | 改 **2**：Web `/api/trust`、CLI `trust set`。报告 **2**：GET `/api/trust/report`、CLI `trust report`。`web.py:1399,2369`、`cli.py:825–830` | 共用 `trust_mode`，写入 actor 的获取不同 |
| 待办登记、撤回 | 每项 **2**：CLI 与 Codex 工具调用 `todo.add_native/withdraw`。`cli.py:845–850`、`codex_tools.py:571–578` | 登记可绑定目标执行，撤回保留原因 |
| 待办查询、处理 | 查询 **3**：GET `/api/todos`、CLI `todo/todos`、`crew_todo_list`。处理 **2**：HTTP `/api/todo/resolve`、助手 `todo.resolve`。`web.py:1454,2394`、`cli.py:841`、`codex_tools.py:575`、`personal.py:1012` | 读端筛选范围不同；Web 另有 `/api/todo/undo` |
| 文档登记 | **3**：HTTP `/api/documents`、CLI `doc add`、`crew_doc_add`。`web.py:1389`、`cli.py:855`、`codex_tools.py:579` | 共用 `documents.register`，会话和执行归属的取得方式不同 |
| 文档列表 | **4**：GET `/api/documents`、CLI `doc list`、`crew_doc_list`、助手 `documents.list`。`web.py:1842,2366`、`cli.py:863–870`、`codex_tools.py:584`、`personal_actions.py:45` | Web 有刷新副作用，助手可跨工作区并分页 |
| 文档索引刷新 | **4**：POST `/api/documents/refresh`、GET `/api/documents` 的副作用、CLI `doc refresh`、`crew_doc_refresh`。`web.py:1397,1848`、`cli.py:860`、`codex_tools.py:593` | 是否校验后返回列表、是否允许隐式刷新具有差异 |
| 普通交接与进度报告 | 每项 **2**：CLI `handoff/report`、`crew_handoff/crew_report`。查询也各 **2**，CLI `handoffs/reports`、`crew_handoffs/crew_reports`。`cli.py:835–840,947`、`codex_tools.py:609–627` | 交接写白板记录；进度报告与目标交付各自独立 |
| Git 管理启用、停用 | 启用 **1 个业务路由**，CLI/Web 共用 `/api/git/enable`。停用 **2 个路由**，CLI `/api/git/disable`，Web `/api/git/settings` 的 `enabled:false`。`web.py:1359–1376`、`cli.py:878–884`、`F/git-settings.js:53,71` | 设置入口带修订号并保留配置校验 |
| Git 分支认领 | **3**：Web `/api/git/adopt`、CLI `git adopt`、`crew_git adopt`。`web.py:1331`、`cli.py:898`、`codex_tools.py:503–510` | 共用 `git_overview.adopt`，Web 指定接手 leader，AI 入口以自己为接手人 |
| Git 规则、合并请求 | 政策读写、请求登记、请求处理各 **2**，CLI 与工具直调。请求查询和分支查询另有 GET `/api/git` 综合视图，各 **3**。`web.py:945,2402`、`cli.py:885–903`、`codex_tools.py:491–517` | 综合视图和专用查询字段不同。处理请求只登记状态 |
| 创建目标 | **3**：专用 HTTP `/api/goals/create`；CLI 及 agent HTTP `/api/goals/agent`；Codex 宿主直调。`goal_api.py:114,129`、`cli.py:592`、`codex_tools.py:525` | agent `create` 支持额外创建字段及同时启动 |
| 目标启动、暂停、恢复、取消、重开、转交 | 每项 **3**：专用 `/api/goals/action`；CLI 及 agent HTTP；Codex 宿主直调。`goal_api.py:136–200`、`goal_actions.py:349–397`、`cli.py:592`、`codex_tools.py:525` | 用户专用路由带版本与验收语义，agent 分派检查目标执行身份 |
| 目标验收接受、拒绝及观察 | **1 个专用路径**：`/api/goals/action` 的 `accept/reject/observe`。`goal_api.py:139–169` | `goal finish` 属于提交交付，验收由用户入口处理 |
| 目标修订、分配、决策模式、工作项写、报告发布、交付、文件快照写 | 各 **2 条传输路径**：`/api/goals/agent` 和 Codex 宿主直调；CLI 走前者，Web 直接使用前者的 `mode.set/report.publish`。`goal_actions.py:266–398`、`goal_artifacts.py:385`、`cli.py:592`、`codex_tools.py:525`、`F/goal-ui.js:334`、`F/stage-report-ui.js:16` | 各 action 的权限独立，远程设备只可用允许的用户动作 |
| 目标上下文添加、更正、失效 | 各 **3**：专用 `/api/goals/context`；CLI 及 agent HTTP；Codex 宿主直调。`goal_api.py:201–219`、`goal_actions.py:297–307`、`cli.py:542,592`、`codex_tools.py:519` | 专用路由使用 `add/correct/expire`、用户来源和条目版本；通用路由使用 `context.put/correct/expire` |
| 目标和报告读取 | 目标详情 **4**：GET `/api/goal`、CLI `goal status`、`crew_goal status`、助手 `goal.detail`。报告 **3**：GET `/api/goals/report`、CLI `goal report.get`、`crew_goal report.get`。`web.py:2330–2347`、`goal_actions.py:200,279`、`cli.py:588`、`personal_actions.py:41`、`codex_tools.py:525–528` | UI 报告提供 HTML/元数据，agent 绑定可读范围。`report.preview` 另调桌面截图 |
| 保存 AI 执行配置 | **2 条业务函数入口**：HTTP `/api/settings` 的 `ai` 分支、助手 `settings.save`。桌面本机另有 IPC worker 读取和写入对应配置。`web.py:1242–1252`、`personal.py:995`、`D/main.cjs:658–665` | 助手只写 `ai`，HTTP 可写 `execution/sounds` |
| 提示词保存、重置 | 各 **2**：HTTP `/api/behavior-prompts` 与助手 `prompt.save/reset`。`web.py:1150`、`web_api/behavior_prompts.py:47`、`personal.py:997–1002` | 相同底层版本操作，actor 和指令证据来源不同 |
| 想法写入和标签写入 | 共享动作各 **2**：墙面 HTTP `action` 与助手 `note.*` / `tag.*`。想法 `capture/update/annotate/archive/restore/pin/snooze/merge/bulk`，标签 `create/rename/merge/recolor/remove`。仅墙面可 `keep/unmerge/tag.pin/tag.reorder`。`personal.py:17–21,38–58,676–713,1019–1025` | 共享底层写函数，UI 和助手使用各自回执与授权来源 |
| 想法、标签读取 | 想法列表 **2**：GET `/api/personal/notes`、助手 `notes.list`。标签列表 **2**：GET `/api/personal/tags`、助手 `tags.list`。`web.py:2313–2317`、`personal_actions.py:23–32` | 助手想法列表有额外筛选与分页，标签两边均可补登记 |
| 用量扫描、查询和导出 | **2 套入口**：HTTP `/api/usage`、`/api/usage/export`；CLI `usage scan/summary/sessions/daily/codex/agents/models/export`。`web.py:1969,2452–2463`、`usage_cli.py:114–162` | HTTP 查和导出先扫描，CLI 的扫描是独立子命令，CLI 导出写本地文件 |
| 运维聊天与配置 | **2 套入口**：Web `/api/admin/chat`、`/api/admin/chat/send`、`/api/admin/chat/interrupt`、`/api/admin/config`，桌面本机 IPC 经运维 worker。`web.py:1207–1212,2287–2290`、`D/main.cjs:538–545`、`D/operations.cjs:14` | 同类操作有 HTTP 和进程调用，独立运维会话没有普通 Crew 动态工具 |

本次清单以当前仓库能够静态追踪的客户端为界。`无` 表示没找到仓库内对应请求，外部脚本、浏览器书签和已发布旧客户端的使用情况未核实。

