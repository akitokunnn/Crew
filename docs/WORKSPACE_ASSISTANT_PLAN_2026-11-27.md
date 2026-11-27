# 系统管理员与工作区助手 · 方案（2026-11-27）

依据：`Temp/tasks/助手归属重整_盘点任务书_last.md`（Codex 只读盘点，文中 Q1–Q7 指它）。

## 用户裁决（2026-11-27，原话要点）

1. 右上角的「系统管理员」和进入页面时左边第一个「系统管理员」，都作为真正的全局助手（系统管理员）的入口；全局助手的功能保留，改名叫系统管理员。
2. 助手、想法、思维导图都分配给各个工作区。想法和导图仍是一个界面、可切换；导图是核心能力，要明显、默认出现；想法墙只是记录灵感的地方。
3. 给一套测试流程：所有新功能和基础能力，代码验证不了的交给用户手测。
4. （补充）运维对话也是系统管理员的范畴。

## 主控裁决（本方案自定，评审可推翻）

| # | 裁决 | 理由 |
|---|---|---|
| M1 | 系统管理员 = 现有全局助手那条会话（personal_assistant 单例），身份、历史、载体、外部来信接收人全部不变，只改名称与入口。 | Q1：沿用原会话最稳，历史不丢。 |
| M2 | 桌面右上角只留一个「系统管理员」按钮（盾牌），打开系统管理员 AI 标签；去掉原「全局助手」气泡按钮。桌面首页左栏第一项「系统管理员」同样打开 AI 标签。应用菜单「系统管理员…」同向。 | 用户裁决 1。 |
| M3 | 原「系统管理员」健康页并入系统管理员，改称「系统管理员 · 运维」：内容为后台状态、恢复、事故处理和运维对话。系统管理员 AI 页头有「运维」入口（有桌面桥接时打开原生运维页；浏览器里打开本机 Web 的运维面板）。首页左栏只保留一项「系统管理员」（进 AI），菜单「系统管理员…」同向，另有菜单「系统管理员 · 运维…」。后台起不来时，点任何系统管理员入口都落到原生运维页并说明原因。远程桌面照旧只读显示所连主机健康，本机恢复按钮仍走本机 IPC。设置里原「运维对话」入口改为跳到系统管理员 · 运维。 | 用户 2026-11-27：运维对话也是系统管理员的范畴；评审 M3。 |
| M4 | 运维对话归入系统管理员（用户裁决），以「系统管理员 · 运维」子页呈现；技术上仍是独立的只读诊断线程（自己的载体选择、存储、worker），因为后台坏了也要能用、权限也不同。文案统一叫「系统管理员 · 运维」，行为配置名「系统管理员 · 运维」。不合并两份聊天历史、不给业务对话加主机运维权限。 | 用户 2026-11-27；评审 M4、Q4。 |
| M5 | 每区一位工作区助手：表 `workspace_assistants(project PK REFERENCES projects, session_id UNIQUE REFERENCES sessions)`；会话行 project=该区、crew_name「助手」（仅展示，内部一律按 SID 寻址，与区内同名成员共存）、role member、cwd=工作区根、entrypoint `crew-workspace-assistant`。首开 BEGIN IMMEDIATE + 锁内复查，并发只产生一个 SID；打开不启动 AI。session library 标为专用类别、不可收编 / 移动 / 普通删除；成员卡、未分组计数、空区判定排除它。工作区移除时停用（拒绝旧 token 与待确认），重新登记同项目时复用原 SID 与历史；工作区清理预览列明助手会停止、身份与想法导图保留。 | 评审 M5、§6。 |
| M6 | 工作区助手权限：新注册表角色 `workspace_assistant`，**封闭清单**（不继承 leader 默认集合、不吃 assistant_cover 补授、无 as_user / interaction.resolve / HOME 管理 / 跨区委派）。清单见下文「能力清单」。项目在三个时点生效：工具入口从可信会话映射取项目并拒绝冲突的 project / data.project；入确认队列与取幂等回执前用该项目规范化参数；执行与确认回放时重新核对身份、项目活动状态与对象归属。 | 评审 §2：leader 能力带组长语义，套用会出错或扩权。 |
| M7 | 守则：新增角色守则 `prompts/roles/workspace_assistant.md`（由 assistant.md 改写为本区视角）；系统管理员守则改名措辞。改完要真 AI 实测（Codex gpt-6-luna low + CC claude-haiku-5-5，隔离 HOME）。 | 全局规则：AI 行为改动要实测。 |
| M8 | 作战室 / 开局 / 重整：由工作区助手发起时它是牵头人（campaign._launch 传真实 SID，不再落到 board.HUMAN）；旧房间认已存 lead_session_id。三个谓词分开：`is_admin`（原单例，管理员启动分支、外部来信）、`can_lead_as_assistant`（管理员或工作区助手，不占 leader 席）、`leads_room(room, sid)`（房内授权）。预算修改复核本人牵头；任务回报、拍板回告、后台唤醒查询纳入工作区助手。 | 评审 M8：全局替换 is_assistant 会让工作区助手走无项目启动分支。 |
| M9 | 想法按工作区：新建想法必须带 project（人用当前页面工作区；工作区助手强制本区；系统管理员须显式给 project）。标签改为每区一套：新表 `workspace_note_tags(project,name,…) PK(project,name)`，迁移时按「该区想法实际用到的标签」复制颜色 / 置顶 / 顺序，旧全局标签表停用并删除读写路径。 | 用户裁决 2；Q5。 |
| M10 | 导图按工作区：图头每区一行各自 version；节点带非空 project，父子同区。迁移以整树为单位：树内已归属想法全属 A 且无 NULL 想法 → 整树归 A；含 NULL 想法或无任何想法证据 → 整树进遗留池；A/B 混合树 → **确定性拆分**：想法节点各回其想法所属区，纯主题节点在每个有后代的区各保留一份（原 ID 留给后代最多的区，平局取项目名字典序最小，其余区生成新 ID），父子、排序、折叠照搬；想法归属一律不变。转换链接全部保留为历史，本区再次转换只认本区仍有效的目标。 | 评审：按整树判定 + 不改想法归属；拆分规则写死，免去手工拆分界面。 |
| M11 | 遗留池（project NULL 的想法与遗留导图树、只被 NULL 想法使用或未被使用的旧标签）：任一工作区想法页顶部一行「有 N 条旧想法 / M 棵导图还没归属工作区 · 查看」，打开后逐项勾选、带版本原子认领到本区（`unassigned.list/claim`，仅人与获准设备可用，助手不可见）。认领树时树内 NULL 想法一并归本区。本机正式库现有 2 条（1 条已归档）、导图为空；另一台电脑数量未知，迁移不得依赖数据量。 | 评审：按项认领，不一键全搬。 |
| M12 | 页面：系统管理员 = `/assistant.html`（无 project）；工作区助手 = `/assistant.html?project=X`；想法 = `/notes.html?project=X`。同一页面文件两种模式。显式空 / 重复 / 未知 / 已移除的 project 报错页，不回落到管理员。 | 少改文件、链接语义清楚。 |
| M13 | 左栏：工作区左栏（app.js 两套 rail + global-rail.js 的工作区模式）在架构图与 Git 之间放「助手」「想法」，链接带当前 project，以 URL 的稳定项目名高亮；左栏不再出现跨区入口。浏览器（非桌面外框）里系统管理员入口放左栏底部设置上方；桌面里在右上角，不重复。手机底栏、工作区总览快捷入口（系统管理员直达；想法需先进工作区）、随手记弹窗（区内用当前项目；总览 / 管理员页需先选工作区）、空区「助手开局」、语音页返回都改到对应目标。 | 用户：放在左边不割裂；评审 M13。 |
| M14 | 桌面标签：`GLOBAL_PAGES` 只留系统管理员；`/`、`/assistant.html`、`/notes.html` 带且仅带一个非空 `project=X` 时归工作区 X 的标签，目标 `{name,page,hash}` 全程传递（`_openProject`→`main.openProject`→`tabs.open`），目标标签页不同则加载目标 URL、相同则只交接 hash；普通点工作区标签仍只选中。裸 `/notes.html` 按发起标签工作区→最近有效工作区→总览改写。重复 / 空 project、未知查询、带凭证 URL 在这些路径上 blocked。`did-navigate`、标题、重载用同一分类器；`storage:<id>` 只读历史禁止进入助手页。 | 评审 §4。 |
| M15 | 想法页默认导图：默认 layout=map（显式 `#layout=wall` 仍直达卡片墙）；导图模式下只有一条细工具栏（视图切换 / 回顾 / 管理标签）+ 整页导图，不显示大输入框与筛选条；导图托盘有常驻可见的小输入框，记下后可挂到选中节点，触屏可用（点选放置，不只拖拽）；卡片墙与托盘共用按区草稿键，失败保留文本；「记并挂」第二步失败重试只补挂载。 | 用户裁决 2；评审 M15。 |
| M16 | 「全局助手」文案全部改为「系统管理员」（前端、桌面、提示音名、行为配置名、通知文案）；内部标识（表名、op 名、CSS 类）不改名，避免无谓迁移。 | 只改用户看得到的。 |

## 能力清单（workspace_assistant，首版封闭）

以后新增同前缀操作需单独评审，不自动授予。校验落点见评审 `Temp/tasks/助手归属重整_方案评审任务书_last.md` §2。

| 操作 | 要点 |
|---|---|
| `session.whoami`、`workspace.state`、`domains.get` | whoami 返回 `kind=workspace_assistant`、本区；workspace.state 走 overview 固定本区。 |
| `chat.*` 对自身会话 | 自己的历史、文件、技能；不得读写本区其他会话（与普通 member 同）。 |
| `campaign.preview/launch` | launch 确认后复核项目与 board_version，传真实 SID 当牵头人。 |
| `domains.restructure.preview/apply`、`leader.merge/retire` | 校验嵌套 changes 的节点、leader、接任者均属本区；高风险，人确认。 |
| `leader.create` | 真实原话凭证；入口固定 project。 |
| `war_room.list/get/open/members/post/item/contract/decide/close/mark_read/budget` | 列查限本区；新房本人牵头；房内写按真实牵头人授权；budget 人确认 + 复核本人牵头。 |
| `notes.list`、`note.get` | 先按项目查主记录，合并来源 / 讨论 / leader 关联不越区。 |
| `note.capture/update/pin/snooze/archive/restore/annotate/keep/unmerge/merge/bulk` | capture 强制本区；按 ID 先校验归属与版本；整批先全量校验再写，任一不符整批回滚；`bulk(op=project)` 删除。 |
| `note.start/recovery.get` | 三方项目一致；start 需原话授权。`note.recover` 不给助手（只人）。 |
| `tags.list`、`tag.*` | 键 `(project,name)`，所有影响面限本区。 |
| `mindmap.get/edit/preview/convert` | 节点、父、想法、roots、目标架构节点、leader、转换链接均属本区；执行时复核图版本与架构版本。 |

风险：`confirm` 的原话要求、MCP `message_id` 参数、守则说明目前特判 `role=='assistant'`，要同样接入 `workspace_assistant`。开局 / 重整 / 合并 / 退役 / 预算仍交人确认。

## 接口约定（冻结；前后端以此为准）

### 助手
- `POST /api/op/assistant.open` body `{project?}`；`GET /api/op/assistant.state?project=X`。缺省 = 系统管理员；显式空、重复、未知、已移除的 project → 400/404 带原因。旧 `context_project` 仅管理员导航用，与 `project` 同传 → 400。
- `open` 返回身份对象（同现有，加 `kind`、`project`）。`state.assistant.kind ∈ {assistant, workspace_assistant}`；`state.assistant.project` 为 `null` 或项目名字符串；**`state.project` 为 `null` 或项目对象（含 `name`、`display_name`、`root`、`theme`）**，与 frontend/chat.js 读 `state.project.name` 一致。其余字段（members/leaders/goals/ai/sounds/models/efforts/activity/context/last_seq）保留；管理员 `scope:'personal'`，工作区 `scope:'workspace'`。
- 工作区助手首次显式发送时用 `settings.resolve(conn,'assistant')` 的载体选择；已绑定 SID 不随开页重绑。chat.send/history/read/interrupt/skills/file/events、runtime.switch 对该 SID 照常工作。附想法：服务端核对 note 属本区，消息被接受后才记录讨论。

### 想法与标签
- `GET notes.list?project=X&include_archived=1` → `{notes,stale_days,model_calls}`（不再内嵌全量标签）。`GET tags.list?project=X` → `{tags,palette,model_calls}`。`GET note.get?project=X&note_id=…`（人也可用，用于 `#note` 直达）。
- 写操作沿用统一入口：`POST /api/op/note.<act>`，`X-Crew-Request-Id` 头，body `{project, data:{…}}`；`note.start/recover` 保持扁平参数加 `project`。`data.project` 若出现必须等于外层。版本字段沿用 `expected_version/expected_versions`。同请求 ID 同参返回同结果；不同参 → 409。
- 管理员（MCP）可对任意区操作，但每次必须显式给 project。

### 遗留池
- `GET unassigned.list` → `{counts:{notes,archived_notes,trees,tags}, notes:[{id,text,state,version,tags}], trees:[{root_id,label,node_count,note_ids,version}], version}`。
- `POST unassigned.claim` body `{project, note_ids:[], root_ids:[], expected_version}` → `{claimed:{notes,trees}, remaining:{…}, version}`；版本过期 409。仅人与获准设备；普通 `note.update` 不能改归属。

### 导图
- `GET mindmap.get?project=X` → `{project,version,nodes,links}`，空图 version 0。
- `POST mindmap.edit {project,base_version,ops}`；`POST mindmap.preview {project,roots,parent_id,decisions,names,leaders}`；`POST mindmap.convert {project,map_version,board_version,request_id,…}`。`edit` 的 parent_id 是导图节点，`convert` 的 parent_id 是本区架构节点。过期 409 可重新预览。

### 等你确认
- `GET interaction.list?project=X`（缺省 = 全局收件箱，仅管理员页）返回 `id,op,project,session_id,status,args` 与预览字段；`POST interaction.resolve {id,approve,project?}`：带 project 时核实卡属于该区；执行时复核请求者与版本，结果回原会话。旧卡无项目只在全局页处理。

### 错误
- 统一机器可判别 `error_code`：`project_unavailable`、`not_visible`、`version_conflict`、`request_conflict`、`outcome_unknown`，HTTP 状态分别 404/404/409/409/202。

### 深链
- 新讨论 `/assistant.html?project=X#note=<id>`；历史讨论按返回的 `session_id,last_seq` 打开原会话（管理员或原工作区助手）。`#opening=Y` 与页面 project 不一致时不预填；已有草稿不覆盖、不自动发送。

## 业务库迁移（M9–M11）

在 `store.connect()` 基础 schema 之后、变更触发器之前注册一次性迁移：轻查 → `BEGIN IMMEDIATE` → 锁内复查完成标记 → 建新表 / 索引 → 搬标签（按区实际引用，含归档想法；未引用与只被 NULL 想法用的进遗留）→ 导图按 M10 → 核对数量、父子、外键 → 写完成标记 → 提交。同一连接、不用 executescript；失败保持旧状态可重试；不以「新表已存在」推定完成。移除 personal / mindmap 每次打开惰性建旧表与写旧标签表的代码。须验证：旧库、空库、未启用想法功能的库、并发首开、中断重试、Windows/Mac。

## 工作区包（storage_v2）

可移植库 4→5：投影本区标签、图头与版本、节点及父子、节点→想法、有效同区转换链接、助手→本区映射、本区讨论定位；旧包接受 2/3/4/5。fork 重映射全部关系 ID；遗留池、管理员、令牌、待确认、其他区内容不随包；激活不启动 AI。同步激活 `TABLES`、指纹与恢复检查。

## 施工拆分（定稿）

| 块 | 内容 | 会话 |
|---|---|---|
| 后端 | M1、M5、M6、M7（守则与接入）、M8、M9–M11、迁移、工作区包、遗留池、确认范围、错误码；对应测试（盘点 Q7） | 会话「后端」：Claude 主控 + Codex astra 施工，分支 `claude/wa-backend` |
| 前端与桌面 | M2、M3、M12–M16、文案；前端 / 桌面测试；浅深 · 宽窄截图 | 会话「前端」：Claude，分支 `claude/wa-frontend`；后端未好前用接口约定的样例响应桩测 |
| 合并验收 | 合并两分支、相关测试、真 AI 实测（CC haiku + Codex luna low，隔离 HOME）、三要点、补手测流程 | 本会话 |

## 契约补充（施工中由后端会话提出、主会话认可，2026-11-27）

1. 错误响应沿用 `{error, code, detail}`，另加顶层 `error_code`（五类之一，HTTP 404/404/409/409/202）；其他错误不带。
2. 人用 `GET note.get?project=X&note_id=…` → `{note:{…}}`，与 notes.list 元素同形。
3. `interaction.list` 保持 `{requests:[…]}`，元素含 id, op, project, session_id, status, args, summary；GET 或 POST `{project?}`。
4. `assistant.open` 身份对象的 `project` 为字符串或 null；`assistant.state.project` 为项目对象。
