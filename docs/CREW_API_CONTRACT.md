# Crew 任务与前端共享接口

2026-10-28 · 后端及最终集成负责人维护。实现进度见 [执行记录](CREW_IMPLEMENTATION_EXECUTION_2026-10-28.md)。

工作区已接续模块化主干，并快进到原型导航调整后的 `4294977`，本轮桌面/插件源码为 `0.2.42`（未发布）。旧基线上的未提交实现已封存，有效增量已按新模块接口迁入。本契约按当前真实实现更新，不使用旧备份代替现状。

本文是本次改造的共同契约。`existing` 已有接口继续可用；`implementing` 正在施工，前端可据此实现显式 mock，接入前由后端更新验证证据。生产断连必须显示错误，不能自动切回 mock。示例见 `docs/fixtures/crew-api.json`。

当前接入：`verified` 表示已在此新基线用真实 HTTP、临时 HOME 和假载体验证；`implemented` 表示代码已登记、仍需相应联合验收。后端入口已迁到 `web_api/` 的功能登记，不恢复旧 `web.py` 业务分支。`0.2.42` 后端阶段包已完成隔离验证，待新 UI 合流重建；未升级正式实例。

## 身份、状态与请求

- `task.id` 与已有 `goal_id` 完全相同，UUID 字符串；`execution_id` 是一次原生执行身份，`session_id` 是 Crew 人员身份，均不能互相替代。换载体不换 task ID。
- `project` 暂沿用现有工作区 API 的名称键；`GET /api/projects` 返回 storage-v2 `workspace_id`（稳定身份）与本机 root。重命名经过现有工作区服务，不直接改名称键。任务 `primary_project` 是主归属，`projects` 是引用入口；关联项目列表不得复制任务或历史。
- 时间为 UTC ISO-8601；版本为正整数。更新须带 `version`，冲突 HTTP 409，保留用户草稿。列表中排序只影响显示。
- 任务 `state`：`discussion`（未授权讨论）、`queued`、`running`、`waiting_user`、`stopped`（用户停任务）、`blocked`（故障）、`completed`、`archived`、`trashed`。`execution_status` 保留底层 goal 状态；`review_status` 独立表达 `none/pending/accepted`，待体验不等于停止执行。
- `mode` 为 `manual/automatic`，只影响此任务的决策，独立于原生工具权限。`authorized` 表示是否已授权执行；保存讨论、改模式、恢复垃圾箱都不构成执行授权。
- POST 同源、JSON、`X-Crew: 1`。新增变更接口带 `request_id`；同 ID 同正文回放原结果，同 ID 不同正文 409。对话用已有 `message_id`。本地服务默认 loopback；远程认证接入另列，不能直接暴露无认证 API。
- 业务错误保持既有结构 `{"error":"具体原因"}`，配合 HTTP 400/409 等状态处理；认证错误另有 `code:unauthorized/forbidden`。客户端不能要求所有错误都有 code。

## 第一批接口

| 状态 | 方法与路径 | 输入 / 返回 |
| --- | --- | --- |
| existing | GET `/api/projects` | `{projects:[{name,root,workspace_id,...}]}`；普通目录有效，不创建 Git |
| existing | POST `/api/project/init` | `{name,root}` 注册目录 |
| verified | GET `/api/tasks?project=名称&include_inactive=1` | `{tasks:[Task]}`；不传 project 查全局；关联入口返回相同 ID |
| verified | GET `/api/tasks/attention?project=名称&offset=0&limit=50` | 全局或工作区的程序待处理事项，返回 `{items,total,offset,limit,next_offset}`；无 AI 唤醒，详见下节 |
| verified | GET `/api/task?task_id=UUID&project=名称` | `{task:Task,executions:[],members:[],reports:[],documents:[],scheduler:{...},notifications:{receipts:[]}}` |
| verified | POST `/api/tasks/create` | `{project,text,title?,leader_session_id?,acceptance?:[],constraints?:[],projects?:[],request_id}` → `{task}`；只建稳定讨论，不启动 AI |
| implemented | POST `/api/tasks/update` | `{task_id,version,title?,request?,acceptance?,constraints?,projects?,request_id}` → `{task}`；保留需求版本和原话；旧 version 的 HTTP 冲突已验证 |
| verified | POST `/api/tasks/mode` | `{task_id,version,mode,request_id}` → `{task}`；不改全局权限 |
| verified | POST `/api/tasks/pin` | `{task_id,pinned:boolean,request_id}` → `{task}`；card 增加 pinned/pinned_at，服务重连后保留，仅影响导航，不改任务 version、授权或执行优先级 |
| verified | POST `/api/tasks/start` | `{task_id,version,request_id}` → `{task,receipt}`；执行明确授权，与普通讨论分开 |
| verified | POST `/api/tasks/stop` | `{task_id,version,request_id}` → `{task}`；停此任务所有执行与自动派发 |
| implemented | POST `/api/tasks/resume` | `{task_id,version,request_id}` → `{task,receipt}`；明确恢复原任务 |
| verified | POST `/api/tasks/archive`、`trash`、`restore` | `{task_id,version,request_id}` → `{task}`；运行中先保存停止/隐藏意图，`lifecycle_pending` 表示尚在等待实际停止；restore 后保持停止 |
| existing | GET `/api/chat/history?project=名称&goal_id=任务ID&session_id=人员ID` | `{events:[],execution_id,busy,running,read_seq,...}`；空历史也可读，不启动模型 |
| existing | GET `/api/chat/events?...&after=序号` | SSE，正文与 history 中单条 event 同结构；详见下节 |
| implemented | POST `/api/chat/send?project=名称` | `{goal_id,session_id,text,message_id,images?:[{media_type,data}],intent?:dialogue\|work,affects_task?:boolean}` → 原生接收回执；默认 dialogue。明确继续修改用 work；未授权草稿仍需显式 start。affects_task 仅用于成员重要补充，保留原文并向 leader 转达一次 |
| existing | POST `/api/chat/interrupt?project=名称` | `{goal_id,session_id}`；只停当前对话本轮，不能调用任务 stop 代替 |
| implemented | POST `/api/tasks/schedule` | `{task_id,version,session_id?,text,evidence,request_id}`；显式可执行下一步或用户排队，不靠空“继续”唤醒。原生 `schedule` 动作至真实完成边界的 HTTP 链路已验证 |
| existing | GET `/api/settings`、`/api/project/settings?project=名称`、`/api/runtimes` | 原设置、工作区设置与原生能力目录，保留 existing 字段 |
| verified | GET `/api/settings/effective?project=名称&session_id=人员ID` | 有效 ai/execution、每字段来源、scope revisions；`applies=next_launch`，不擅自重配已有原生进程 |
| implemented | GET/POST `/api/settings/scope` | GET `?scope=global\|project\|session&identity=…` 读当前 version；POST `{scope,identity?,ai?,execution?,version,reset?,request_id}` 保存/取消覆盖 |
| verified | GET `/api/prompts?project=名称&session_id=人员ID` | 六类可编辑 Crew 提示词及版本；加 `key,scope,identity` 可读对应可写 scope version |
| verified | GET `/api/prompts/history?key=common&scope=global&identity=…&before=版本&limit=50` | `{history:[{scope,identity,prompt_key,version,content,effective_content,action,actor,created_at}],next_before}`；before/identity 可省略，limit 为 1–200。reset 的 content=null，effective_content 保留当时有效正文；应用/恢复仍走 save/reset 的 CAS |
| verified / implemented | POST `/api/prompts/save`、`/api/prompts/reset` | `{key,scope,identity?,text?,version,request_id}`；save 已走真实 HTTP；reset 已有领域验证 |

Task 字段见同版示例 JSON；在首批字段外增加 `workspace_references`（未绑定外工作区引用）、`lifecycle_pending`、`waiting_reason`。停止请求可以先返回 `execution_status=pausing`；不能把它冒充原生进程已经退出。详情的 notification receipts 展示每渠道 `sent/failed/unknown/not_configured`、真实原因及有限 attempts。

提示词修改为 `next_turn`，已有执行收到实际宿主注入后才记录 `sent`；`GET /api/prompts/applied?execution_id=…` 可检查内容/版本/来源。原生未提供加载证据时仍是 `not_confirmed`。模型目录中的 `configured/discovered/reported` 不可混称“已加载”；未知目录允许用户显式配置模型和 effort，前端不要按 HAIKU 等名字猜能力。

未显式配置时角色默认：leader 偏好 Claude，member 偏好 Codex；模型/effort 保持 null 走原生。`effective.ai` 仍为配置三字段，不探测 CLI；新增 `role_preference` 为 null 或 `{runtime,model:null,effort:null,source:'builtin-role-preference',availability:'not_inspected',loaded:'not_confirmed',applies:'next_launch'}`，对应未配置字段的 source.scope 为 builtin-role-preference。已有 AI 不重配；per-leader 显式成员覆盖、工作区/全局显式选择优先。实际启动时才检查 CLI 可发现性，缺首选时 `selection_resolution={source,preferred_runtime,selected_runtime,available,fallback,reason,applies:'next_launch',loaded:'not_confirmed'}` 给真实原因；available 是发现的 CLI，不代表已登录或确认模型加载。原生认证/权限错误保持真实，显式用户选择不自动切换。新执行回执、history 的 `runtime_selection` 及既有事件 `runtime:selected` 可回查；重复消息回放其原始选择来源。

## 任务待处理事项与导航

`GET /api/tasks/attention` 的 `project` 可省略；省略时查全局，不套用当前默认工作区。`offset` 为非负整数，`limit` 为 1–100，默认 50。按 `created_at,id` 升序分页；无下一页时 `next_offset=null`。无效项目或分页返回 HTTP 400 和真实原因；已移除工作区返回原业务冲突。该查询读取已记录状态，不启动、恢复或排程 AI；首次遇到未投影的旧 goal 时，沿用任务列表的幂等兼容映射，不改变执行状态。

每项都有 `{id,kind,project,task_id,goal_id,session_id,task_ids,title,summary,created_at}`。`title`、`summary` 是最多 300、1000 字符的预览，完整内容由原业务详情读取。

| kind | 稳定 id | 其他字段 | 来源与导航 |
| --- | --- | --- | --- |
| decision | `decision:数字ID` | `decision_id,blocking,held` | 未答复的拍板；包含已挂起且仍 open 的记录。仅用已保存 goal_links 证明任务归属，`session_id` 是提出者 |
| todo | `todo:数字ID` | `todo_id,blocking` | 原 TODO ID；legacy goal_links 与共享关联去重合并。完成沿用 `/api/personal/todos/complete`，不按导航工作区猜原待办归属 |
| blocked | `blocked:任务UUID` | — | 当前任务状态 blocked，summary 保留实际受阻原因，session_id 为该任务 leader |
| review | `review:任务UUID` | `report_id`（可空） | 当前轮次等待人工验收；有匹配报告时给真实 report_id，session_id 为该任务 leader |

新接口中 `task_id=goal_id`，均为可导航主任务 UUID；`task_ids` 保留全部仍可见的关联 UUID。共享 TODO 只出现一条：过滤掉 archived/trashed 关联，停止任务仍可处理待办；工作区筛选按任务的 `projects` 匹配，优先选择筛选范围内的任务作为主入口，仍保留其他可见关联。`project/session_id` 对应主入口的主工作区和可导航会话，共享 TODO 用该任务的 leader。关联任务全部隐藏时，事项不会伪装成无任务的总对话事项。

无已证明任务归属的旧总对话拍板和 TODO 保持 `task_id=null,goal_id=null,task_ids=[]`，`project/session_id` 为原始会话归属；不从负责人当前任务反推。已关闭或归档的旧总会话、已移除工作区的任务和总会话事项不作为入口返回。共享 TODO 仍有其他可见任务需要时保留一条，删除已移除工作区的关联入口。

旧 `GET /api/state` 的 `attention` 仅为 decision 增补已证明的 `goal_id/task_id`；其他无法证明的 `goal_id=null`。为兼容原 UI，旧 `codex_failed.task_id` 仍是原生 run/rollout ID，并另给同值 `codex_task_id`，不能把它当 Crew 任务 UUID。导航优先使用已证明的 `goal_id`；统一 `task_id=goal_id` 的承诺只适用于新 `/api/tasks/attention`。

该接口已由 `tests.test_task_attention` 的真实 HTTP、临时 HOME、假载体测试验证；前端交互验收另行记录。

## 对话与流

SSE 使用 `id: seq` 和 `data: JSON`，不依赖命名 event。每本 execution journal 的 seq 严格递增；前端按 `(execution_id,seq)` 去重。支持 `after` 和 `Last-Event-ID`，取较大者重放；心跳是注释行。切代后重新读取 history 的 execution_id，不能将不同 journal 的 seq 混用。

主要事件为 `message`（输入原话及来源）、`assistant`（公开回复）、`text_delta`/流式增量（以真实 journal 类型为准，后端后续补全夹具）、`tool_use/tool_result`、`status`、`input_delivery`、`error`。输入回执必须区分 `submitted/accepted/rejected/unknown`；HTTP 200 不代表原生已接收，更不代表任务完成。unknown 不得自动用新 ID 重发。

成员使用任务 detail 的 session_id、同 task ID 读取对话；返回 leader 保留任务、输入草稿与阅读位置。负责人总会话作为历史入口继续存在，不把旧聊天强行归入新任务。

## 后续业务接口

以下业务已经接入当前后端；表格区分独立后端验证和待联合验收范围：

- 全局个人事项 `/api/personal/items`、`/api/personal/capture`，保存原话与可空 project；TODO 沿用原 ID，明确操作合并键及 task 关联，不按相似标题误合并。
- 持续报告 `/api/tasks/report`（GET/POST），每 task 一份、版本校验、分节更新，409 保留用户文本与待应用提议。
- 附件 `/api/tasks/attachments`，真实字节、文件信息、task 归属、认证下载；不提供任意绝对路径读取入口。
- 成果版本 `/api/tasks/versions`、`/api/tasks/versions/preview`、`/api/tasks/versions/restore`，真实内容与 current hash 检查，冲突不覆盖后续文件。
- 助手调用已有任务、事项、设置服务，永久清空不向 AI 暴露。通知按 task 和每渠道结果独立去重。

当前已接入的后续业务：

| 状态 | 接口 | 约定 |
| --- | --- | --- |
| verified | GET `/api/personal/items?project=…&include_done=1` | `{items:[],todos:[]}`，project 可省略；`unassigned=1` 筛未分配灵感 |
| verified | POST `/api/personal/capture` | `{text,project?,tags?:[],request_id}` → `{item}`，快速保存原话 |
| implemented | POST `/api/personal/update` | `{id,version,text?,project?,tags?,state?,request_id}`，state 为 open/archived/trashed |
| verified | POST `/api/personal/items/link` | `{id,version,task_id,request_id}` → `{item,task}`；灵感返回 task_ids、task_links、未绑定 task_references，保留原文与项目字段 |
| verified | POST `/api/personal/items/chat` | `{id,version,project?,task_id?,title?,new_task?:false,request_id}` → `{item,task,created,conversation:{project,goal_id,session_id,text}}`；复用关联或建 discussion，不启动 AI。多关联时明确 task_id，前端按 conversation 导航后正常发送 |
| verified | POST `/api/personal/todos` | `{session_id,title,steps,verification,operation_key,task_ids:[],tags?:[],blocking?,request_id}`；明确操作键合并，保留原 todo.id |
| verified | POST `/api/personal/todos/update` | `{id,metadata_version,tags:[str],request_id}` → `{todo}`；标签有独立 metadata_version（初始 0），不改变完成状态 version |
| verified | POST `/api/personal/todos/complete` | `{id,version,note?,status?:done\|cancelled,attachments?:[{goal_id,attachment_id}],request_id}`；先上传任务附件，再提交引用。返回 todo.evidence 含 MIME/大小/sha/认证 URL；每个仍允许推进的关联任务只排程一次 |
| verified | GET/POST `/api/tasks/report` | GET `?task_id=…` → `{report,pending}`；POST `{task_id,version,markdown 或 sections,request_id}`。409 带 pending_id，双方内容都保留 |
| verified | POST `/api/tasks/attachments` | `{task_id,filename,mime_type?,data:base64,request_id}` → `{attachment}`；GET 同路径列表；GET `/api/tasks/attachment?task_id=…&attachment_id=…` 认证读取真实字节 |
| implemented | GET/POST `/api/tasks/versions` | GET `?task_id=…`；POST `{task_id,label,files:[相对路径],commit?,request_id}`，实际内容快照，不只存 commit 或路径 |
| verified | POST `/api/tasks/versions/preview`、`restore` | `{task_id,version_id}` 预览返回 expected_files；restore 增加 `{expected_files,request_id}`。只恢复明确文件，冲突 409 保留双方。真实非 Git/Git 文件恢复均已验证 |
| verified | GET `/api/tasks/version-file?task_id=…&version_id=…&path=…&side=delivered\|baseline` | 返回对应版本真实字节；删除文件返回缺失说明，不允许越界路径 |
| verified | POST `/api/assistant/open`、`send` | open `{project?}` → `{assistant}`：固定全局 session，不启动 AI。send `{project?,text,message_id,images?}`；返回的 session.project 是原生会话锚点，聊天 history/SSE 复用该锚点和 session_id |
| verified | POST `/api/assistant/action` | 已认证用户直接指令 `{action,data,instruction,request_id}`；宿主生成来源并审计。可创建任务、转达、停止、明确设置修改；不提供永久清空 |
| verified | POST `/api/assistant/native-action` | `{action,data,actor_session_id,execution_id?,message_id?,request_id?}`；固定助手原生动作桥。变更要求 message_id 指向其真实用户 journal；只读动作不要求消息证明，仍检查固定助手身份 |

原生助手 `crew_personal` / `crew personal <action> --data … --message-id … --request-id …` 仅接受固定助手身份；宿主从真实用户 journal 找原话。模型自己填写 source 或 instruction 不能获得授权。任务内原生 `crew_goal` / `crew goal` 已补 `schedule,user.forward,task.detail,task.refine,report.get,report.update,attachments.list,attachment.add,versions.list,versions.preview,versions.file,version.capture,version.restore`；写成果与详情带 request_id，报告带当前 version。versions.file 返回 base64 分页，每页最多 65536 字节并给 next_offset。

固定助手只读动作：summary、task.detail、report.get、documents.list、attachments.list、versions.list、versions.file。除 summary 外均传 data.task_id；列表 offset/limit 上限 100，版本文件列表另有 file_offset/file_limit。report.get 按字符 offset/length 分页（上限 65536），versions.file 按字节 offset/length（上限 65536，须 version_id/path/side），返回真实 source、版本及 next_offset；二进制 text=null。它们可按需读其他项目资料，不把全部历史常驻注入。助手变更另增 item.update/item.link/item.chat/todo.update，与 HTTP 同一字段和业务；CC `todo add --operation-key … --tag …`、Codex crew_todo_add 的 operation_key/tags 共用多任务操作合并链路。

## 独立后台和远程登录

新版桌面普通退出调用 disconnect，不发送 shutdown；明确“停止后台与全部执行并退出”才停服务。隔离 smoke 仍清理自己的后台。GET `/api/admin/service` 给真实管理能力；POST `/api/admin/service/stop` 只向持有当前 PID 的管理员发实例绑定请求。

远程 API 已完成隔离代理验证，未配置实际公网域名/账号，也未上线：

- GET `/api/remote` 读设置；POST `/api/remote/configure` `{version,enabled,origins:[https origin],passphrase?}`。本机首次设置；已登录远程修改需 current_passphrase。口令仅保存加盐摘要，不回显。
- POST `/api/auth/login` `{passphrase}`，得到 HttpOnly/Secure/SameSite=Strict cookie；GET `/api/auth/session` 读当前登录；POST `/api/auth/logout` `{}` 撤销会话。
- 所有远程业务、SSE、附件与历史受同一登录保护；Host/origin 必须匹配允许列表。不能把代理 Host 改为 localhost，也不从 X-Forwarded-* 猜用户身份。未登录 401，来源错误 403。
- 当前配置信息、cookie、原生设备绑定不随工作区包导出。账号、域名及公网实测留在真实外部接入步骤，不能把合成测试 origin 当上线链接。

## 桌面与联调边界

前端拥有 `plugin/crew/web/` 展示、客户端适配及 mock。后端拥有 `plugin/crew/*.py`、测试和本契约；跨层 desktop core/main/preload 先记录具体修改范围。现有 `window.crewDesktop` 不作为浏览器核心业务前提；打开本地目录返回真实 desktop 能力或可复制路径。

`GET /api/auth/session` 返回 `{local,authenticated,origin}`。本机桌面项目 WebContentsView 也没有首页 preload，不能只靠 `window.crewDesktop` 判断本机能力。现有 `/api/files/resolve`、`/api/files/open`、`/api/files/reveal` 使用工作区/session/document 归属解析；open/reveal 操作的是执行机上的程序/资源管理器，不是远程手机的文件夹。远程默认提供认证内容/网页或可复制路径，不能把路径复制提示为“已打开”。桌面工作区注册、重定位和历史仍走已有首页 IPC，本轮不添加第二套数据位置规则。

第一批联调顺序：任务列表 → detail → 对话/history/SSE → conversation interrupt/task stop → mode/settings → TODO/report/attachment。现有接口与 implementing 接口要在客户端显式区分；后端验证后将对应行改为 verified 并给测试命令。
