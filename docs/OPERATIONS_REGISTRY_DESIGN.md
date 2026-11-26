# 操作清单与生成式 MCP 设计（方案 C）

> 日期：2026-11-27 · 作者：架构重审主题会话（Claude）· 基线 `3fdfbec`
> 依据：[总计划](REBUILD_PLAN_2026-11-27.md) 用户裁决 7、[Teamwork 设计](ONBOARDING_AND_TEAMWORK_DESIGN.md) 第 6 节、[入口全量盘点](OPERATIONS_INVENTORY.md)（事实底稿）、[架构重审](ARCHITECTURE_REVIEW.md)、[拆分边界](SPLIT_BOUNDARIES.md)
> 本文是阶段三“操作清单与助手（R5）”会话的施工依据。实测记录见第 12 节。

---

## 0. 结论

1. **清单是唯一事实来源。** 每个业务动作登记一次：名称、参数格式、读写级别、风险、各角色可用范围、加锁与准入、对外绑定（HTTP 路径 / CLI 命令 / MCP 工具）。HTTP 路由表、CLI、MCP 工具、助手指南、覆盖测试全部从清单生成或对照清单检查。
2. **AI 只走 MCP。** Crew 后端在本机 HTTP 端口上提供 `/mcp`（Streamable HTTP，无常驻流），每个托管会话启动时拿到自己的令牌（经启动参数 / 配置文件注入，不放环境变量）。后端按令牌认出调用者、按清单卡权限。Bash 调 `crew`、Codex `dynamicTools`、`/api/assistant/native-action` 三条旧路径整条删除。
3. **权限即工具集，由载体原生按需加载（已用真模型实测，第 12 节）。** 每个会话在 MCP 连接建立时拿到它有效权限内的**全部**操作工具（每个操作一个，带只读 / 破坏性 / 可重复注解）。两种载体都会把 MCP 工具藏在自带的工具搜索后面：60 个带长说明的工具，Claude Code 只多约 900 token（只列名字），Codex 几乎不增加（19706 vs 19511），两者都能按需找到并调用其中任意一个。所以“默认只给少数核心工具、其余按需加载”由载体完成，Crew 不需要自己做加载协议：
   - **Claude Code**：全部工具一次给出，CC 的 ToolSearch 负责延迟加载。
   - **Codex**：按 t3code 的做法注册两个服务名：`crew`（全部工具，走 Codex 的延迟加载）与 `crew_core`（只启用核心工具，`omit_tools_from = ["deferred"]` 直出），核心工具不用搜就能调。
   - `crew_search` 保留为只读的**用法指南**工具（中文检索“想做 X 用哪个操作”、返回风险与相近操作的区别），两种载体相同，不承担加载。
   - 实测还确认：Claude Code 支持中途 `tools/list_changed`，**Codex 不支持**（`list_changed`、`config/mcpServer/reload`、`thread/resume`、同进程新开线程都拿不到新工具）。本设计因此不依赖 `list_changed`：会话的工具集在启动时确定，权限收紧由后端每次调用时拒绝、立即生效；新增权限在会话下次启动（重签令牌）时出现在工具列表里。两种载体行为一致，没有按载体分叉的调用路径。
4. **权限在后端卡死，派出的权限不超过发起方**：令牌记录有效权限集 = 角色默认集 ∩ 签发者有效集；每次调用按 `(操作, 调用者, 目标)` 查表。
5. **写操作带 `request_id` 只执行一次**（借鉴 t3code 的 commandId）；高风险操作由 AI 发起时生成统一的“用户确认请求”，用户在界面确认后才执行。
6. **覆盖测试**：任何 HTTP 路由、前端或桌面里的改数据调用、CLI 子命令、MCP 工具，没有对应清单条目（或未列入带理由的“非操作入口”表）即测试失败。

---

## 1. 现状问题（数据来自盘点）

| 问题 | 证据 |
| --- | --- |
| 入口多、分散 | 50 个 GET + 104 个 POST API、约 85 个 CLI 子命令（含 `goal` / `context` / `usage`）、31 个 Codex 动态工具、32 个助手动作 + 4 个仅界面动作 |
| 同一动作多条实现 | 拍板答复 4 个入口、文档列表 4 个、收编与组员变化 8 个、建 leader 3 个；见盘点第 7 节“多路径对照” |
| 读写标注不可靠 | GET `/api/documents` 持写锁并刷新索引（`web.py:1842`）；GET `/api/assistant/state` 会建助手；GET `/api/personal/tags` 补登记旧标签；`crew_channel` 被工具门禁当读，却推进已读游标（`codex_tools.py:434,461`、`channel.py:255`） |
| 身份来自环境变量 | CLI 的会话身份依次取 `--session`、`CREW_SESSION_ID`、**`CLAUDE_CODE_SESSION_ID`**（`cli.py:520` 等 12 处）；任何在托管会话里起的子进程都继承这个身份，测试也必须先清变量 |
| Codex 工具注册粗糙 | 只按“是不是助手”筛（`codex_tools.py:235`），成员能看到只允许 leader 的工具；`resume_thread` 不重传 `dynamicTools`，恢复的旧线程工具集取决于原生线程记录 |
| 规则写在提示词里 | 四个角色守则（`plugin/skills/*/SKILL.md`）教 AI 用 `crew say` / `crew send` 等命令；守卫 `guard._crew_coordination` 专门识别 shell 里的 `crew` 命令放行 |
| 已有雏形 | `POST_PATHS` 是写路由白名单；`personal_actions.py` 已有动作名 → 读写分组；个人便笺写入已有 `request_id` 回执；`codex_tools._validate` 是零依赖 JSON Schema 子集校验器——都直接并入清单 |

---

## 2. 清单条目字段

清单放在新包 `plugin/crew/ops/`：`registry.py`（`Operation` 定义、登记函数、查询）、`scopes.py`（角色与范围）、`domains/*.py`（按领域登记条目，处理函数就是拆分后 `web_api/*` 里的函数）。纯 Python、零依赖。

```python
@dataclass(frozen=True)
class Operation:
    name: str                 # 'channel.post'：领域.动词，小写，点分；MCP 工具名把点换成下划线 → channel_post
    summary: str              # 一行中文，用于 crew_search 结果、CLI 帮助、助手指南
    description: str          # 何时用、副作用、与相近操作的区别（生成指南用）
    params: dict              # JSON Schema 子集：object / properties / required / additionalProperties:false / enum / items
    result: dict | None       # 返回结构（生成文档与契约测试）
    access: str               # 'read' | 'write' | 'external'（打开主机程序）| 'host'（主机管理）
    hints: Hints              # MCP 注解：read_only / destructive / idempotent / open_world，由 access 推导后可覆盖
    risk: str                 # 'normal' | 'confirm'（AI 发起需挂原始用户消息）| 'high'（AI 发起需用户在界面确认）
    scopes: frozenset[str]    # 可调用角色，见第 4 节；不得为空
    target: str               # 目标约束：'none' | 'self' | 'own_group' | 'own_project' | 'own_goal' | 'any'
    lock: str                 # 'none' | 'read' | 'write'（来自 W2 阶段表）
    admission: bool           # True = 走 chat.work() 更新准入（现 MODEL_WORK 等）
    project: str              # 'none' | 'selected' | 'chat' | 'body'（来自 W2 的项目解析规则）
    handler: Callable         # handler(ctx, args) -> dict；ctx 含 caller、conn、project、now、service
    http: tuple[Binding, ...] # 现有 URL 绑定（迁移期保留，界面零改动）；新客户端统一走 /api/op
    cli: CliSpec | None       # 生成 CLI 的命令路径与位置参数；None = 不出 CLI
    core_for: frozenset[str]  # 在哪些角色的 MCP 默认工具集里（其余经 crew_search）
    emits: tuple[str, ...]    # 写成功后产生的事件类型（给“快照 + 序号”事件流）
```

规则（由清单自检测试强制，第 10 节）：

- `access == 'read'` ⇒ `hints.read_only`、`lock in ('none','read')`、不得写库（测试用只读连接执行全部读操作的样例参数）。现在“读里带写”的几处（文档列表刷新、助手状态建助手、标签补登记、`crew_channel` 推进游标）拆成显式写操作或改为后台步骤。
- `access == 'write'` ⇒ `params` 必须含 `request_id`（字符串，必填）；`emits` 非空。
- `risk == 'high'` ⇒ `hints.destructive`；对 AI 角色走确认请求（第 5.6 节）。
- `scopes` 只能用第 4 节列出的角色名；`target` 与角色组合必须在权限表里有定义。
- 每个 `http` 绑定唯一；每个 `cli` 命令路径唯一；工具名唯一。

---

## 3. 调用者与身份

### 3.1 调用者（`Caller`）

| 角色 | 如何认出 | 说明 |
| --- | --- | --- |
| `human` | 本机界面 / 本机 CLI：现有本机来源检查 + `X-Crew: 1` | 用户本人；CLI 默认就是 `human`，不再从环境变量读会话 |
| `device` | 远程浏览器 Cookie / 远程桌面 Bearer（现有 `remote_access`） | 用户的其他设备；权限是 `human` 的子集（现 `remote_routes` 允许 / 拒绝清单改为清单字段 `scopes` 里是否含 `device`） |
| `assistant` | MCP 令牌，签发给全局助手会话 | 跨工作区；写操作至少 `confirm` |
| `leader` | MCP 令牌，签发给 leader 总对话 | 限本工作区；目标约束按条目 |
| `execution` | MCP 令牌，签发给成员 / 目标内执行（含 execution_id、goal_id） | 只能动自己的目标与执行 |
| `git_admin` | MCP 令牌，签发给 Git 助手会话 | 本工作区 Git 管理写权限 + leader 的只读集 |
| `ops` | 运维对话 | **不给 Crew 业务工具**（现状如此，保持） |

### 3.2 令牌

- 后端在启动托管会话（CC 进程、Codex app-server、目标执行）前签发：随机 32 字节，库里只存哈希。表 `mcp_tokens(token_hash PK, session_id, execution_id NULL, goal_id NULL, role, project, scopes JSON, issuer_token_hash NULL, created_at, revoked_at NULL)`。
- `scopes` 在签发时算好：`角色默认集 ∩ 签发者有效集`（用户手动启动时签发者是 `human`，有效集为全集）。之后签发者权限变化不追溯（会话下次启动重新签发时生效）。
- 会话停止、归档、删除，或执行结束时吊销；后台重启时，旧令牌全部吊销，托管进程按现有“自动续回”重新启动并拿新令牌。
- 令牌**只**通过载体的 MCP 配置传入，不进环境变量，因此模型在 shell 里起的子进程（如 codex-dispatch、claude-dispatch）拿不到会话身份——这正是现在 `CREW_SESSION_ID` / `CLAUDE_CODE_SESSION_ID` 泄漏给子进程和测试的根因。
- `/mcp` 只接受本机连接；远程监听对 `/mcp` 一律拒绝（加入 `remote_routes` 拒绝清单并由覆盖测试检查）。

---

## 4. 权限范围表

“✓”= 可用；“自”= 只能作用于自己（`target=self`）；“组”= 本组（自己领导的成员、自己发起的执行）；“目标”= 自己负责或参与的目标；“确认”= `risk=confirm`，须挂原始用户消息；“界面确认”= `risk=high`，AI 发起会生成用户确认请求；空 = 不可用。

| 领域（操作前缀） | human | device | assistant | leader | execution | git_admin |
| --- | --- | --- | --- | --- | --- | --- |
| 查询：`workspace.state/list`、`session.whoami/who`、`decision.list`、`todo.list`、`document.list`、`goal.get`、`channel.list` | ✓ | ✓ | ✓ 跨工作区 | ✓ 本工作区 | ✓ 本工作区（只读视图） | ✓ 本工作区 |
| 自身：`session.task.set`、`session.card.*`、`session.rename` | ✓ 任意会话 | ✓ | | 自 | 自（不可改名） | 自 |
| 协作消息：`message.send` | | | ✓ 确认（以用户来源代发） | ✓ 本工作区 | ✓ 本工作区 | ✓ 本工作区 |
| 交流区：`channel.post/consume` | ✓（`channel.post` 以用户身份） | ✓ | | ✓ | | |
| 拍板：`decision.ask/revise/withdraw` | | | | ✓ | ✓（挂在执行上） | ✓ |
| 拍板答复：`decision.answer` | ✓ | ✓ | ✓ 确认 | 组（原归属组，按授权模式） | | |
| 拍板流转：`decision.discuss/ready/clarify` | ✓ | ✓ | ✓ 确认 | | | |
| 待办：`todo.add/withdraw` | | | | ✓ | ✓ | ✓ |
| 待办处理：`todo.resolve/undo` | ✓ | ✓ | ✓ 确认 | | | |
| 文档：`document.register/refresh` | ✓ | ✓ | | ✓ | ✓ | ✓ |
| 建人：`leader.create` | ✓ | ✓ | ✓ 确认 | | | |
| 派执行：`execution.spawn` | ✓ | ✓ | | 组 | | |
| 停 / 归档：`session.stop/archive`、`group.close` | ✓ | ✓ | ✓ 确认 | 组 | 自（仅 stop） | |
| 删除 / 清理：`session.delete`、`workspace.remove`、`maintenance.cleanup.run`、`storage.clean/trash_empty` | ✓ | 部分（见映射表） | 界面确认 | | | |
| 目标生命周期：`goal.create/start/pause/resume/cancel/reopen/transfer/amend/assign` | ✓ | ✓ | ✓ 确认 | 目标 | | |
| 目标验收：`goal.accept/reject/observe` | ✓ | ✓ | ✓ 确认 | | | |
| 目标交付：`goal.work.*`、`goal.context.*`、`goal.report.*`、`goal.finish`、`goal.version.capture` | ✓（`report.publish`、`mode.set`、`version.*`） | 同左 | | 目标 | 目标（自己的执行） | |
| Git 管理：`git.policy.set`、`git.request.resolve` | ✓ | | | | | ✓ |
| Git 协作：`git.request.add`、`git.adopt`、`git.*.list` | ✓（`git.adopt` 指定接手人） | ✓ | | ✓（以自己为接手人） | ✓（只读与 `request.add`） | ✓ |
| Git 设置：`git.enable/disable/settings.set/fetch` | ✓ | 部分 | ✓ 确认 | | | |
| 资源与声明：`resource.*`、`lease.*`、`claim.*` | ✓ | | | ✓ | ✓ | ✓ |
| 想法与标签：`note.*`、`tag.*` | ✓ | ✓ | ✓ 确认 | | | |
| 提示词与设置：`prompt.save/reset`、`settings.save` | ✓ | | ✓ 确认 | | | |
| 主机管理：`admin.*`、`connections.*`、`remote_access.*`、`data.*`、`storage.*`、`files.open/reveal` | ✓ | 拒绝（现桌面拒绝清单） | | | | |

派生规则：

1. 默认集按上表；`execution` 的有效集 ∩ 发起它的 leader 的有效集；助手启动的 leader 用 leader 默认集（助手有效集覆盖它）。
2. **授权模式**（`trust_mode`：由我拍板 / 自主推进）不改变清单权限，只改变 leader 的 `decision.answer` 等是否需要升级给用户，由处理函数判断——维持现状。
3. 现有处理函数里已做的归属校验（如“只能停本组成员”“只能撤回自己的拍板”）保留在处理函数内；清单的 `target` 是第一道闸，处理函数是第二道，测试分别覆盖。

---

## 5. MCP 服务形态

### 5.1 传输

- 端点：Crew 后端现有端口上的 `POST /mcp`（Streamable HTTP，MCP 2025-06-18 起的形态）。`GET /mcp` 返回 405（规范允许“服务器不提供独立 SSE 流”），`DELETE /mcp` 返回 200。两种载体在这种形态下都能工作（第 12 节）。
- 所有响应都是普通 `application/json`；不推通知、不需要常驻连接，`BaseHTTPRequestHandler` 足够，不引入依赖。
- 鉴权：`Authorization: Bearer <令牌>`。缺失或吊销 → 401；权限不足 → 工具结果 `isError: true` + 结构化错误（`forbidden`、原因、需要的角色），不用 HTTP 403，避免载体把它当连接故障重试。
- `initialize` 返回 `capabilities.tools = {}`（不声明 `listChanged`）；`serverInfo.name = "crew"`。会话 ID 头 `Mcp-Session-Id` 用令牌哈希前缀，便于日志关联。

### 5.2 工具集

- `tools/list` 返回令牌有效权限集内的全部操作，工具名为操作名把点换成下划线（`channel.post` → `channel_post`），加上 `crew_search`。每个工具的 `inputSchema` 就是清单 `params`；`annotations` 来自 `hints`（`readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`），`title` 用 `summary`，`description` 用 `description`（写清何时用、与相近操作的区别）。
- 核心工具（`core_for`）只影响 Codex 的直出注册，CC 不区分：

| 角色 | 核心工具（Codex `crew_core` 直出） |
| --- | --- |
| leader | `session_whoami`、`workspace_state`、`message_send`、`channel_post`、`channel_consume`、`decision_ask`、`execution_spawn`、`session_stop`、`todo_add`、`document_register`、`goal_get`、`goal_finish` |
| execution | `session_whoami`、`message_send`、`decision_ask`、`todo_add`、`document_register`、`goal_get`、`goal_work_update`、`goal_context_put`、`goal_report_publish`、`goal_finish` |
| assistant | `workspace_state`、`session_detail`、`goal_get`、`chat_send`、`leader_create`、`decision_answer`、`todo_resolve`、`note_capture`、`notes_list` |
| git_admin | `session_whoami`、`git_requests_list`、`git_branches_list`、`git_policy_get`、`git_request_resolve`、`message_send` |

- Codex 启动参数（每会话 app-server）：

```
-c mcp_servers.crew.url="http://127.0.0.1:<端口>/mcp"
-c mcp_servers.crew.http_headers.Authorization="Bearer <令牌>"
-c mcp_servers.crew.disabled_tools=[<核心工具名>]
-c mcp_servers.crew_core.url="http://127.0.0.1:<端口>/mcp"
-c mcp_servers.crew_core.http_headers.Authorization="Bearer <令牌>"
-c mcp_servers.crew_core.enabled_tools=[<核心工具名>]
-c mcp_servers.crew_core.omit_tools_from=["deferred"]
```

  `disabled_tools` / `enabled_tools` / `omit_tools_from` 的写法照 t3code `CodexAdapterV2.ts:1747–1761`；R5 落地时先在隔离 `CODEX_HOME` 用 0.172 实测这三个键（本次只实测了 `url` 与 `http_headers`）。

### 5.3 `crew_search`（用法指南）

- 参数：`query`（中文或操作名片段），可选 `domain`。只读。
- 只在调用者有效权限集里搜；按名称、`summary`、`description` 打分，返回前 8 条：工具名、`summary`、何时用、风险（`confirm` 要带哪条用户消息，`high` 会生成用户确认请求）、相近操作的区别。
- 不加载工具、不发通知；结果对两种载体相同（工具名在 CC 里显示为 `mcp__crew__<名>`，结果文字只写裸工具名）。

### 5.4 被否决的方案：`list_changed` 动态加载 + Codex `crew_call`

最初按用户裁决的字面方案设计为：默认只列核心工具 + `crew_search`，CC 搜到后服务端加载并发 `list_changed`，Codex 因不支持 `list_changed` 改用 `crew_call(op, args)` 通用执行器。第 12 节第 12–15 行实测载体原生延迟加载后改为 5.2 的做法，理由：

1. 上下文代价已由载体解决（实测数据见第 12 节），自做加载协议没有额外收益；
2. 两种载体工具面一致，不需要按载体分叉的提示词、搜索结果文字和执行路径；
3. Codex 侧也保留逐工具的注解和 Codex 自带的工具审批粒度，`crew_call` 会把所有操作压成一个工具；
4. MCP 端点不需要 SSE 响应，实现更简单。

保留的风险：载体的延迟加载是它们自己的行为，若某个版本改为全量加载，代价是上下文变大而不是功能失效；阶段五真实模型实测与后续升级检查都看 `tools/list` 的 token 代价（第 12 节的对照方法可复跑）。

### 5.5 幂等（命令带 ID 只执行一次）

- 表 `op_requests(caller_key, request_id, op, args_hash, status, result JSON, created_at)`，主键 `(caller_key, request_id)`；`caller_key` = 令牌哈希或 `human` / 设备 ID。
- 同一 `request_id` 重复到达：参数相同 → 返回已存结果并带 `replayed: true`，不再执行；参数不同 → 409 `request_id_reused`。执行中重复到达 → 返回 `status: running`，客户端等事件流结果，不重发。
- 保存 7 天，随存储清理一并回收。现有便笺 `request_id` 回执（`personal.py`）并入这张表。
- 界面与手机端：写请求一律在客户端生成 `request_id`，断线重连后**只重拿快照、不重发写请求**；需要确认结果时用 `request_id` 查询（`op.status`）。

### 5.6 风险与确认

- `risk=confirm`：AI 调用必须带 `message_id`（触发它的那条用户消息）；后端按现有 `personal.py:887–901,1028–1077` 的做法在该会话日志里核实消息存在、来源为 `human` / `qq` / `lark`、角色为用户。范围从“仅助手”扩大到所有 AI 角色的 `confirm` 操作。
- `risk=high`：AI 调用不直接执行，而是写一条**用户输入请求**（见架构重审第 6.3 节的统一结构，`kind=confirmation`），工具立即返回 `status: awaiting_user` 和请求 ID；用户在界面（桌面 / 手机）确认后后端执行，结果经事件流和 `message.send` 回告发起会话。拒绝或超时同样回告。
- 用户本人（`human` / `device`）在界面上点的高风险按钮保持现有二次确认对话框，不走请求表。

### 5.7 错误

工具错误统一为 `{"error": {"code": "...", "message": "...", "detail": {...}}}`，`code` 取自固定集合：`forbidden`、`invalid_arguments`、`not_found`、`conflict`、`request_id_reused`、`awaiting_user`、`paused_for_update`、`internal`。`message` 保留处理函数的真实原因（现有中文报错原样透出），不吞错。

---

## 6. CLI 由清单生成

- `cli.py` 中业务子命令全部删除，改为 `ops/cli.py` 根据清单生成 argparse 树：命令路径来自 `cli`（默认 `domain verb`，如 `crew channel post`），位置参数按 `CliSpec.positional`，其余参数由 `params` 自动生成（`string` → `--x`，`boolean` → `--x/--no-x`，`array` → 可重复，`object` → `--x-json`），统一提供 `--json` 输出与 `--request-id`（缺省自动生成）。
- CLI 调用者固定为 `human`，经本机 HTTP 的 `/api/op` 执行（与界面同一路径），不再直接读写库、不再读任何会话身份环境变量。
- 保留手写的**主机命令**（不是业务操作，不进清单）：`web`、`start`、`install`、`autostart install|status|uninstall`、`menu install|status|uninstall`、`doctor`、`usage`（本地日志扫描与导出，见映射表注）。它们登记在 `ops/host_commands.py` 的白名单里，覆盖测试据此区分。
- 助手指南、leader / 执行 / Git 助手的工具说明由清单生成（`ops/guide.py` 输出 Markdown，按角色过滤），四个角色守则只保留行为原则（精简后写进行为提示词，不再注册为技能）。

---

## 7. 会话身份迁移：`CREW_SESSION_ID` → 令牌

| 步骤 | 内容 | 验证 |
| --- | --- | --- |
| 1 | 建 `mcp_tokens` 表与签发 / 吊销函数；`chat._launch_native`、`goal_runtime` 启动 CC 时写每会话 MCP 配置文件 `<home>/runtime/mcp/<令牌哈希前缀>.json`（仅当前用户可读）并加 `--mcp-config <文件>`；`codex_runtime` 启动 app-server 时加 `-c mcp_servers.crew.url=...` 与 `-c mcp_servers.crew.http_headers.Authorization="Bearer ..."`，不写用户的 `~/.codex/config.toml` | 单测：签发、吊销、范围求交；隔离 home 真起 CC / Codex，`tools/list` 按角色返回 |
| 2 | `/mcp` 端点 + 清单驱动的工具执行；`tools/list` 按令牌返回全部有权限的操作；Codex 双服务名直出核心工具 | 第 12 节实验（含 12–15 的延迟加载对照）复跑到产品端点 |
| 3 | hooks 不再从环境变量认会话：CC 的 hook 命令在每会话 `--settings` 里写死 `--session <id> --execution <id>`（与现有 Codex 守卫命令行固定 `--home --session` 的做法一致，`codex_runtime.py:325–329`） | `test_hooks*`、守卫相关测试 |
| 4 | 删除注入：`chat.py:1053`、`codex_runtime.py:292`、`goal_runtime.py:315` 不再设置 `CREW_SESSION_ID` / `CREW_EXECUTION_ID` / `CREW_GOAL_ID` / `CREW_NAME` / `CREW_ROLE`；`child_environment` 继续**剥离**这些变量（防外层残留） | grep 无注入点；托管会话 shell 中 `echo $CREW_SESSION_ID` 为空 |
| 5 | 删除读取：`cli.py` 12 处、`spawn.py:271,304`、`hooks.py:69,76,154–168` | grep `CREW_SESSION_ID\|CLAUDE_CODE_SESSION_ID\|CREW_EXECUTION_ID` 只剩剥离代码与测试 |
| 6 | 集成脚本（`integrations/*-dispatch/*.ps1`）只用于记录“由谁派出”的展示信息：改为由 Crew 在托管会话的 MCP 结果里提供 `whoami` 的 `dispatch_label`，脚本参数显式传入；不再从环境变量读身份 | 派工登记仍显示在发起人卡片下 |

迁移期间不保留“令牌失败就回退环境变量”的分支：步骤 1–5 在同一个合入里完成，由第 12 节实验 + 阶段五真实模型实测把关。

---

## 8. 完整映射表

列说明：**操作** = 清单名；**读写 / 风险**；**范围**：H=human，D=device，A=assistant，L=leader，E=execution，G=git_admin；**HTTP** = 现有路径（迁移期作为绑定保留）；**CLI** = 现有子命令；**Codex** = 现有动态工具；**助手** = 现有 `personal_actions` 动作。“—”= 无。行号见[盘点](OPERATIONS_INVENTORY.md)。

### 8.1 工作区与数据

| 操作 | 读写 / 风险 | 范围 | HTTP | CLI | Codex | 助手 |
| --- | --- | --- | --- | --- | --- | --- |
| `workspace.list` | 读 | H D A | GET `/api/projects` | `projects` | — | — |
| `workspace.state` | 读 | H D A L E G | GET `/api/state` | `status` | `crew_status` | `summary`（跨工作区概览并入此操作的 `scope=all`） |
| `workspace.events` | 读 | H L | — | `events` | — | — |
| `workspace.activity` | 读 | H D | GET `/api/workspaces/activity` | — | — | — |
| `workspace.register` | 写 | H | POST `/api/project/init` | `init` | — | — |
| `workspace.host_folders` | 读 / 主机 | H | GET `/api/host/folders` | — | — | — |
| `workspace.add_existing` | 写 / 主机 | H | POST `/api/host/workspace/existing` | — | — | — |
| `workspace.create_folder` | 写 / 主机 | H | POST `/api/host/workspace/new` | — | — | — |
| `workspace.settings.get` | 读 | H D | GET `/api/project/settings` | — | — | — |
| `workspace.update` | 写 | H | POST `/api/project/update` | — | — | — |
| `workspace.remove` | 写 / high | H A(界面确认) | POST `/api/project/remove` | — | — | — |
| `workspace.theme.set` | 写 | H D | POST `/api/project/theme` | — | — | — |
| `data.history` | 读 / 主机 | H | GET `/api/workspace-data/history` | — | — | — |
| `data.artifact` | 读 / 主机 | H | GET `/api/workspace-data/artifact` | — | — | — |
| `data.blob` | 读 / 主机 | H | GET `/api/workspace-data/blob` | — | — | — |
| `data.download` | 读 / 主机 | H | GET `/api/workspace-data/download` | — | — | — |
| `data.discover` | 读 / 主机 | H | POST `/api/workspace-data/discover` | — | — | — |
| `data.open` | 写 / 主机 | H | POST `/api/workspace-data/open` | — | — | — |
| `data.export` | 写 / 主机 | H | POST `/api/workspace-data/export` | — | — | — |
| `data.preview` | 读 / 主机 | H | POST `/api/workspace-data/preview` | — | — | — |
| `data.import` | 写 / 主机 | H | POST `/api/workspace-data/import` | — | — | — |
| `data.handoff` | 写 / 主机 | H | POST `/api/workspace-data/handoff` | — | — | — |
| `data.activate` | 写 / 主机 | H | POST `/api/workspace-data/activate` | — | — | — |

### 8.2 会话、组与人员

| 操作 | 读写 / 风险 | 范围 | HTTP | CLI | Codex | 助手 |
| --- | --- | --- | --- | --- | --- | --- |
| `session.whoami` | 读 | A L E G | — | `whoami` | `crew_whoami` | — |
| `session.who` | 读 | L E G | — | `who` | `crew_who` | — |
| `session.detail` | 读 | H A | — | — | — | `session.detail` |
| `session.rename` | 写 | H D(任意)；L G(自) | POST `/api/session/rename` | `rename` | `crew_rename` | — |
| `session.appearance.set` | 写 | H D | POST `/api/session/appearance` | — | — | — |
| `session.avatar.get` | 读 | H D | GET `/api/session/avatar` | — | — | — |
| `session.task.set` | 写 | L E G(自) | — | `task` | `crew_task` | — |
| `session.card.get` / `session.card.set` | 读 / 写 | L E G(自) | — | `card` | `crew_card` | — |
| `session.options.set` | 写 | H D | POST `/api/session/options` | — | — | — |
| `session.runtime.prepare` | 写（预览） | H | POST `/api/session/runtime/prepare` | — | — | — |
| `session.runtime.switch` | 写 | H | POST `/api/session/runtime/switch` | — | — | — |
| `session.wake` | 写 | H D L(组) | POST `/api/wake` | `wake` | — | — |
| `session.stop` | 写 / confirm(A) | H D A L(组) E(自) | POST `/api/session/stop` | `stop` | `crew_stop` | `session.stop` |
| `session.compact` | 写 | H D L(自/组) | POST `/api/session/compact` | `compact` | — | — |
| `session.archive` | 写 / confirm(A) | H D A L(组) | POST `/api/session/archive` | `archive` | `crew_archive` | — |
| `session.restore` | 写 | H D | POST `/api/session/restore` | — | — | — |
| `session.undo` | 写 | H D | POST `/api/session/undo` | — | — | — |
| `session.delete` | 写 / high | H D A(界面确认) | POST `/api/session/delete` | — | — | — |
| `leader.create` | 写 / confirm(A) | H D A | POST `/api/leader` | `leader start` | — | `leader.create` |
| `execution.spawn` | 写 | H D L(组) | POST `/api/spawn` | `spawn` | `crew_spawn` | — |
| `leaders.order.set` | 写 | H D | POST `/api/leaders/order` | — | — | — |
| `member_defaults.set` | 写 | H D | POST `/api/member-defaults` | — | — | — |
| `group.list` | 读 | H L | — | `group list` | — | — |
| `group.close` | 写 / confirm(A) | H D A L(自组) | POST `/api/group/close` | `leader close` | — | — |
| `group.undo` | 写 | H D | POST `/api/group/undo` | — | — | — |
| `roster.add` | 写 | H | — | `roster add` | — | — |
| `roster.move` | 写 | H D | POST `/api/roster/move` | `roster move` | — | — |
| `roster.remove` | 写 | H D | POST `/api/roster/remove` | `roster remove` | — | — |
| `roster.adopt` | 写 | H | POST `/api/roster/adopt` | — | — | — |
| `session_library.list` | 读 | H | GET `/api/session-library` | — | — | — |
| `session_library.preview` | 读 | H | GET `/api/session-library/preview` | — | — | — |
| `session_library.adopt` | 写 | H | POST `/api/session-library/adopt` | — | — | — |
| `trust.get` | 读 | H L | — | `trust status` | — | — |
| `trust.set` | 写 | H D | POST `/api/trust` | `trust set` | — | — |
| `trust.report` | 读 | H L | GET `/api/trust/report` | `trust report` | — | — |
| — 删除 | — | — | — | `join` | — | — |

`join`（AI 用 `CLAUDE_CODE_SESSION_ID` 把外部会话收编进 Crew）在令牌模型下没有调用者身份可用，收编统一走界面的会话库（`session_library.adopt`）。**这一条删除需主会话确认**（见第 13 节）。

### 8.3 对话、消息与提醒

| 操作 | 读写 / 风险 | 范围 | HTTP | CLI | Codex | 助手 |
| --- | --- | --- | --- | --- | --- | --- |
| `chat.history` | 读 | H D | GET `/api/chat/history` | — | — | — |
| `chat.file` | 读 | H D | GET `/api/chat/file` | — | — | — |
| `chat.skills` | 读 | H D | GET `/api/chat/skills` | — | — | — |
| `chat.send` | 写 / confirm(A) | H D A | POST `/api/chat/send` | — | — | `session.message` |
| `chat.interrupt` | 写 | H D | POST `/api/chat/interrupt` | — | — | — |
| `chat.read` | 写 | H D | POST `/api/chat/read` | — | — | — |
| `message.send` | 写 | A(确认) L E G | POST `/api/messages` | `send` | `crew_send_message` | — |
| `attention.dismiss` | 写 | H D | POST `/api/attention/dismiss` | — | — | — |
| `notifications.away.set` | 写 | H D | POST `/api/notifications/away` | — | — | — |
| `comment.list` | 读 | H D L | GET `/api/comments` | — | — | — |
| `comment.add` | 写 | H D | POST `/api/comments` | — | — | — |

### 8.4 交流区

| 操作 | 读写 / 风险 | 范围 | HTTP | CLI | Codex | 助手 |
| --- | --- | --- | --- | --- | --- | --- |
| `channel.list` | 读 | H D L | GET `/api/channel` | — | — | — |
| `channel.mark_read` | 写 | H D | POST `/api/channel/read` | — | — | — |
| `channel.consume` | **写**（读并推进本人游标） | L | — | `channel` | `crew_channel` | — |
| `channel.post` | 写 | H D(以用户)、L | POST `/api/channel` | `say` | `crew_say` | — |
| `channel.digest.start` | 写 | H D | POST `/api/channel/digest` | — | — | — |

### 8.5 拍板、待办、交接与报告

| 操作 | 读写 / 风险 | 范围 | HTTP | CLI | Codex | 助手 |
| --- | --- | --- | --- | --- | --- | --- |
| `decision.list` | 读 | H D L E G | （随 `/api/state`） | `decisions` | `crew_decisions` | — |
| `decision.ask` | 写 | L E G | — | `ask` | `crew_ask` | — |
| `decision.revise` | 写 | L E G | — | `decision revise` | `crew_decision_revise` | — |
| `decision.withdraw` | 写 | L E G | — | `withdraw` | `crew_withdraw` | — |
| `decision.answer` | 写 / confirm(A) | H D A L(原归属组) | POST `/api/answer` | `answer` | `crew_answer` | `decision.answer` |
| `decision.discuss` | 写 | H D A(确认) | POST `/api/decision/discuss` | — | — | — |
| `decision.ready` | 写 | H D A(确认) | POST `/api/decision/ready` | — | — | — |
| `decision.clarify` | 写 | H D A(确认) | POST `/api/decision/clarify` | — | — | — |
| `todo.list` | 读 | H D A L E G | GET `/api/todos` | `todo` / `todo list` / `todos` | `crew_todo_list` | — |
| `todo.add` | 写 | L E G | — | `todo add` | `crew_todo_add` | — |
| `todo.withdraw` | 写 | L E G | — | `todo withdraw` | `crew_todo_withdraw` | — |
| `todo.resolve` | 写 / confirm(A) | H D A | POST `/api/todo/resolve` | — | — | `todo.resolve` |
| `todo.undo` | 写 | H D | POST `/api/todo/undo` | — | — | — |
| `handoff.add` | 写 | L E G | — | `handoff` | `crew_handoff` | — |
| `handoff.list` | 读 | L E G | — | `handoffs` | `crew_handoffs` | — |
| `report.add` | 写 | L E G | — | `report` | `crew_report` | — |
| `report.list` | 读 | H L E G | — | `reports` | `crew_reports` | — |

`todos` 这个兼容别名在生成 CLI 时不再保留（只保留 `todo list`），属于旧路径删除。

### 8.6 文档与文件

| 操作 | 读写 / 风险 | 范围 | HTTP | CLI | Codex | 助手 |
| --- | --- | --- | --- | --- | --- | --- |
| `document.list` | **读**（不再刷新） | H D A L E G | GET `/api/documents` | `doc` / `doc list` | `crew_doc_list` | `documents.list` |
| `document.register` | 写 | H D L E G | POST `/api/documents` | `doc add` | `crew_doc_add` | — |
| `document.pin` | 写 | H D | POST `/api/documents/pin` | — | — | — |
| `document.refresh` | 写 | H D L E G | POST `/api/documents/refresh` | `doc refresh` | `crew_doc_refresh` | — |
| `files.resolve` | 读 | H D | POST `/api/files/resolve` | — | — | — |
| `files.content` | 读 | H D | GET `/api/files/content` | — | — | — |
| `files.download` | 读 | H D | GET `/api/files/download` | — | — | — |
| `files.open` | 外部 | H | POST `/api/files/open` | — | — | — |
| `files.reveal` | 外部 | H | POST `/api/files/reveal` | — | — | — |
| `goal.report.folder` | 外部 | H | POST `/api/goals/report/folder` | — | — | — |

`POST /api/files/resolve` 是读操作，绑定改为 GET 属于 HTTP 层整理，迁移期保留原方法。

### 8.7 目标（`/api/goals/agent` 与 `crew_goal` / `crew_context` 的多动作入口拆成独立操作）

| 操作 | 读写 / 风险 | 范围 | HTTP | CLI | Codex（`action`） | 助手 |
| --- | --- | --- | --- | --- | --- | --- |
| `goal.list` | 读 | H D A L | GET `/api/goals` | — | — | — |
| `goal.get` | 读 | H D A L E | GET `/api/goal` | `goal status` | `crew_goal status` | `goal.detail` |
| `goal.create` | 写 / confirm(A) | H D A L | POST `/api/goals/create`；`/api/goals/agent` `create` | `goal create` | `crew_goal create` | — |
| `goal.start` / `pause` / `resume` / `cancel` / `reopen` | 写 / confirm(A) | H D A L(目标) | POST `/api/goals/action`；`/api/goals/agent` 同名 | `goal start` 等 | `crew_goal` 同名 | — |
| `goal.transfer` | 写 / confirm(A) | H D A L(目标) | 同上 | `goal transfer` | `crew_goal transfer` | — |
| `goal.amend` | 写 | H D L(目标) | `/api/goals/agent` `amend` | `goal amend` | `crew_goal amend` | — |
| `goal.assign` | 写 | H D L(目标) | `/api/goals/agent` `assign` | `goal assign` | `crew_goal assign` | — |
| `goal.accept` / `reject` / `observe` | 写 / confirm(A) | H D A | POST `/api/goals/action` | — | — | — |
| `goal.mode.set` | 写 | H D L(目标) | `/api/goals/agent` `mode.set` | `goal mode.set` | `crew_goal mode.set` | — |
| `goal.report.template` | 读 | L E | — | `goal report-template` | `crew_goal report-template` | — |
| `goal.report.get` | 读 | H D L E | GET `/api/goals/report` | `goal report.get` | `crew_goal report.get` | — |
| `goal.report.preview` | 写（临时） | L E | `/api/goals/agent` `report.preview` | `goal report.preview` | `crew_goal report.preview` | — |
| `goal.report.publish` | 写 | H D L E(目标) | `/api/goals/agent` `report.publish` | `goal report.publish` | `crew_goal report.publish` | — |
| `goal.finish` | 写 | L E(目标) | `/api/goals/agent` `finish` | `goal finish` | `crew_goal finish` | — |
| `goal.work.list` | 读 | L E | — | `goal work.list` | `crew_goal work.list` | — |
| `goal.work.add` / `goal.work.update` | 写 | L E(目标) | `/api/goals/agent` 同名 | `goal work.add` / `work.update` | `crew_goal` 同名 | — |
| `goal.versions.list` / `preview` / `file` | 读 | H D L E | — | `goal versions.*` | `crew_goal versions.*` | — |
| `goal.version.capture` | 写 | H D L E(目标) | `/api/goals/agent` | `goal version.capture` | `crew_goal version.capture` | — |
| `goal.version.restore` | 写 / high(AI) | H D L E(界面确认) | `/api/goals/agent` | `goal version.restore` | `crew_goal version.restore` | — |
| `goal.context.inspect` / `retrieve` | 读 | L E | — | `context inspect` / `retrieve` | `crew_context` 同名 | — |
| `goal.context.put` | 写 | H D L E(目标) | POST `/api/goals/context` `add`；`/api/goals/agent` `context.put` | `context put` | `crew_context put` | — |
| `goal.context.correct` | 写 | 同上 | `/api/goals/context` `correct`；agent `context.correct` | `context correct` | `crew_context correct` | — |
| `goal.context.expire` | 写 | 同上 | `/api/goals/context` `expire`；agent `context.expire` | `context expire` | `crew_context expire` | — |

用户入口与 AI 入口语义上的差异（盘点 7 节最后几行：用户专用路由带版本与验收语义，agent 分派检查执行身份）由同一操作的处理函数按 `ctx.caller.role` 处理，不再分成两个 URL 两套代码。

### 8.8 Git

| 操作 | 读写 / 风险 | 范围 | HTTP | CLI | Codex（`crew_git action`） | 助手 |
| --- | --- | --- | --- | --- | --- | --- |
| `git.overview` | 读 | H D L G | GET `/api/git` | — | — | — |
| `git.commit.get` | 读 | H D L G | GET `/api/git/commit` | — | — | — |
| `git.settings.get` | 读 | H D L G | GET `/api/git/settings` | `git status` | `status` | — |
| `git.settings.set` | 写 | H A(确认) | POST `/api/git/settings` | — | — | — |
| `git.enable` | 写 / confirm(A) | H A | POST `/api/git/enable` | `git enable` | — | — |
| `git.disable` | 写 / confirm(A) | H A | POST `/api/git/disable` | `git disable` | — | — |
| `git.fetch` | 写 | H D | POST `/api/git/fetch` | — | — | — |
| `git.adopt` | 写 | H D(指定接手人)、L(自己接手) | POST `/api/git/adopt` | `git adopt` | `adopt` | — |
| `git.policy.get` | 读 | H L E G | — | `git policy` | `policy`（无 text） | — |
| `git.policy.set` | 写 | H G | — | `git policy --set` | `policy`（有 text） | — |
| `git.requests.list` | 读 | H L E G | — | `git requests` | `requests` | — |
| `git.branches.list` | 读 | H L E G | — | `git branches` | `branches` | — |
| `git.request.add` | 写 | L E G | — | `git request` | `request` | — |
| `git.request.resolve` | 写 | H G | — | `git resolve` | `resolve` | — |

Web 用 `git.settings.set enabled:false` 停用、CLI 用 `/api/git/disable`：两条路径合并为 `git.disable`（设置页的停用按钮改调它），属旧路径删除。

### 8.9 资源、租约与文件声明

| 操作 | 读写 | 范围 | CLI |
| --- | --- | --- | --- |
| `resource.list` / `resource.add` / `resource.remove` | 读 / 写 / 写 | H L E G | `resource list` / `add` / `rm` |
| `lease.list` / `lease.take` / `lease.give` | 读 / 写 / 写 | H L E G | `lease list` / `take` / `give` |
| `claim.list` / `claim.add` / `claim.remove` | 读 / 写 / 写 | H L E G | `claims` / `claim` / `unclaim` |

这三组现在只有 CLI，没有 HTTP 和 Codex 工具；登记后 AI 经 `crew_search` 可用，界面以后需要时直接用 `/api/op`。

### 8.10 架构图、想法与标签

| 操作 | 读写 / 风险 | 范围 | HTTP | 助手 |
| --- | --- | --- | --- | --- |
| `domains.get` | 读 | H D L | GET `/api/domains` | — |
| `domains.apply` | 写 | H D A(确认) | POST `/api/domains` | — |
| `notes.list` | 读 | H D A | GET `/api/personal/notes` | `notes.list` |
| `note.get` | 读 | H D A | — | `notes.get` |
| `note.recovery.get` | 读 | H D | GET `/api/personal/notes/recovery` | — |
| `note.capture` / `update` / `annotate` / `archive` / `restore` / `pin` / `snooze` / `merge` / `bulk` | 写 / confirm(A) | H D A | POST `/api/personal/notes`（`action` 同名） | `note.*` 同名 |
| `note.keep` / `note.unmerge` | 写 | H D | POST `/api/personal/notes`（`keep` / `unmerge`） | — |
| `note.recover` | 写 | H D | POST `/api/personal/notes/recovery` | — |
| `note.start` | 写 | H D | POST `/api/personal/notes/start` | — |
| `tags.list` | **读**（补登记改为迁移步骤） | H D A | GET `/api/personal/tags` | `tags.list` |
| `tag.create` / `rename` / `merge` / `recolor` / `remove` | 写 / confirm(A) | H D A | POST `/api/personal/tags`（`action` 同名） | `tag.*` 同名 |
| `tag.pin` / `tag.reorder` | 写 | H D | POST `/api/personal/tags`（`pin` / `reorder`） | — |

`POST /api/personal/notes` / `tags` 按 `action` 分派多个操作：迁移期绑定写成“路径 + action 值 → 操作”，覆盖测试按 `(路径, action)` 检查。

### 8.11 助手、提示词与设置

| 操作 | 读写 / 风险 | 范围 | HTTP | 助手 |
| --- | --- | --- | --- | --- |
| `assistant.open` | 写 | H D | POST `/api/assistant/open` | — |
| `assistant.state` | 读（建助手改为 `assistant.open` 的职责） | H D | GET `/api/assistant/state` | — |
| `prompt.list` / `get` / `history` / `applied` | 读 | H A | GET `/api/behavior-prompts`（`action` 同名） | `prompt.get` / `history` / `applied` |
| `prompt.save` / `prompt.reset` | 写 / confirm(A) | H A | POST `/api/behavior-prompts` | `prompt.save` / `reset` |
| `settings.get` | 读 | H D A | GET `/api/settings` | — |
| `settings.save` | 写 / confirm(A 仅 `ai` 字段) | H A | POST `/api/settings` | `settings.save` |
| `runtimes.list` | 读 | H D A L | GET `/api/runtimes` | — |
| `claude_budget.set` | 写 | H | POST `/api/claude-budget` | — |
| `sounds.custom.add` / `delete` | 写 | H | POST `/api/sounds/custom` / `delete` | — |
| `sounds.custom.file` | 读 | H D | GET `/api/sounds/custom/file` | — |
| `approval.resolve` | 写 | H D | POST `/api/native-approval` | — |
| — 删除 | — | — | POST `/api/assistant/native-action` | （助手改走 MCP） |

### 8.12 用量、维护与存储

| 操作 | 读写 / 风险 | 范围 | HTTP | CLI |
| --- | --- | --- | --- | --- |
| `usage.report` | 读 | H D | GET `/api/usage` | `usage summary` / `sessions` / `daily` / `codex` / `agents` / `models`（主机命令，见注） |
| `usage.export` | 读 | H D | GET `/api/usage/export` | `usage export`（主机命令） |
| `usage.scan` | 写 | H | — | `usage scan`（主机命令） |
| `maintenance.jobs` / `maintenance.archive.get` | 读 | H | GET `/api/maintenance/jobs` / `archive` | — |
| `maintenance.department.preview` / `compact` | 写 | H | POST `/api/maintenance/department/preview` / `compact` | — |
| `maintenance.cleanup.preview` | 写（临时） | H | POST `/api/maintenance/cleanup/preview` | — |
| `maintenance.cleanup.run` | 写 / high | H A(界面确认) | POST `/api/maintenance/cleanup` | — |
| `storage.get` | 读 / 主机 | H | GET `/api/storage` | — |
| `storage.preview` / `restore` / `policy.set` | 写 / 主机 | H | POST `/api/storage/preview` / `restore` / `policy` | — |
| `storage.clean` / `storage.trash_empty` | 写 / high | H | POST `/api/storage/clean` / `trash/empty` | — |

注：`usage` 系列 CLI 读的是本机原生日志目录（`--claude-root` 等参数），是主机命令；HTTP 的两个用量 GET 现在“读里扫描”，改为：`usage.report` 只读库，扫描由现有后台轮询负责（`Service.scan_usage`），界面“刷新”按钮调 `usage.scan`。

### 8.13 主机管理（只给本机用户界面）

| 操作 | 读写 | HTTP |
| --- | --- | --- |
| `admin.update.prepare` / `admin.update.cancel` | 写 | POST `/api/admin/update/prepare` / `cancel` |
| `admin.connection.plan` / `admin.connection.prepare` | 读 / 写 | GET `/api/admin/connection/plan`、POST `/api/admin/connection/prepare` |
| `ops_chat.get` / `ops_chat.send` / `ops_chat.interrupt` | 读 / 写 / 写 | GET `/api/admin/chat`、POST `/api/admin/chat/send` / `interrupt` |
| `ops_config.get` / `ops_config.save` | 读 / 写 | GET / POST `/api/admin/config` |
| `connections.get` / `configure` / `action` / `preferences.set` | 读 / 写 | GET `/api/connections`、POST `/api/connections/configure` / `action` / `preferences` |
| `remote_access.get` / `remote_access.action` | 读 / 写 | GET / POST `/api/remote-access` |

### 8.14 非操作入口（覆盖测试的白名单，每条写明理由）

| 入口 | 理由 |
| --- | --- |
| `STATIC` 全部静态路径 | 静态资源 |
| GET `/api/access/context` | 传输层能力探测，不读业务数据 |
| GET `/api/admin/health` | 进程健康探测，桌面与 `spawn` 用于判断后端存活 |
| GET `/api/chat/events` | 对话事件流订阅；并入统一事件流后由事件层管理 |
| POST `/api/report-preview/worker/*`（4 条） | 桌面截图 worker 的私有协议，带 worker 令牌，不是业务动作 |
| GET / POST `/remote/login`、`/remote/logout` | 远程设备认证 |
| `/mcp` | MCP 传输本身 |
| 主机 CLI 命令 `web`、`start`、`install`、`autostart *`、`menu *`、`doctor`、`usage *` | 本机安装与诊断，非业务数据 |

### 8.15 盘点发现、随清单一并处理

- `STATIC['/goal-versions-ui.js']`（`web.py:2051`）指向不存在的文件：随 A1 或清单落地时删除该条。
- 无调用方的写路由：`/api/attention/dismiss`、`/api/chat/interrupt`、`/api/comments`(POST)、`/api/session/restore`、`/api/roster/adopt`、`/remote/logout`。登记进清单（操作仍有效，助手与手机端可能用），但不再各自维护一个 URL；迁移完成后只保留 `/api/op` 访问。

---

## 9. 统一 HTTP 入口

- 新增 `POST /api/op`：`{"op": "...", "args": {...}, "request_id": "..."}`；读操作也可 `GET /api/op/<name>?<args>`。调用者由本机来源 / 设备认证决定（`human` / `device`），远程设备按清单的 `D` 范围放行——`remote_routes` 的路径清单由清单生成，不再手写。
- 迁移期旧 URL 作为清单的 `http` 绑定继续可用（同一个处理函数，不是两套代码）；前端按领域逐步改用 `/api/op`，每改完一个领域删除对应绑定。手机端从第一天就只用 `/api/op` + 事件流。

---

## 10. 覆盖测试

新增 `plugin/tests/test_operations_registry.py`（Python）与 `plugin/tests/test_operations_frontend.js`（Node），全部是静态或纯函数测试，秒级完成：

1. **路由全覆盖**：`web` 实际可达的每个 GET / POST 路径（W2 后为路由表的全部键；最终为清单生成的绑定）必须等于“清单 `http` 绑定 ∪ 8.14 白名单”。多一个少一个都失败，失败信息列出路径与建议的操作名。
2. **按 action 分派的入口**：`/api/personal/notes`、`/api/personal/tags`、`/api/behavior-prompts`、`/api/goals/agent`、`/api/goals/action`、`/api/goals/context` 的每个合法 `action` 值都映射到一个操作（从处理函数的动作表读取，如 `personal.py` 的动作常量、`goal_actions.dispatch` 的分派表）。
3. **界面改数据动作全覆盖**：扫描 `frontend/*.js`、`frontend/app/*.js`、`desktop/src/*.cjs`、`desktop/src/home*.js` 中 `api(` / `fetch(` / `post(` 调用的路径字面量与 `method:"POST"`，每个非 GET 路径必须命中清单绑定；动态拼接的路径必须在文件内声明 `// crew-op: <操作名>` 注释，否则失败（逼迫新代码写字面量或声明）。
4. **CLI 全覆盖**：生成的 argparse 树中每个叶子命令必须对应一个操作或在主机命令白名单里；反之每个 `cli != None` 的操作都能在树里找到。
5. **MCP 工具全覆盖**：对每个角色模拟签发令牌后调用 `tools/list`，返回的工具 = 有效权限集内的全部操作 + `crew_search`；生成的 Codex 启动参数里 `crew_core.enabled_tools` = 该角色 `core_for` ∩ 有效权限集；`crew_search` 对每个可用操作都能按名字搜到。
6. **权限穷尽**（借鉴 t3code 的“权限穷尽声明”）：`Operation` 构造时 `scopes`、`access`、`risk`、`target` 缺一即抛错；测试遍历 `所有操作 × 所有角色`，断言 `authorize()` 的结果与第 4 节表（以数据形式存于 `ops/scopes.py`）一致；新增角色或操作而不更新表即失败。
7. **字段一致性**：第 2 节的规则（读 ⇒ 只读注解与只读连接、写 ⇒ `request_id` 与 `emits`、high ⇒ destructive）逐条断言；读操作用样例参数在只读连接上执行一遍，写库即失败。
8. **远程边界**：`/mcp`、全部 `host` 类操作对 `device` 拒绝。

---

## 11. 旧路径整条删除

在 MCP 路径通过第 12 节实验与阶段五真实模型实测后，同一个合入里删除：

| 旧路径 | 删除内容 | 核查 |
| --- | --- | --- |
| Codex 动态工具 | `codex_tools.py` 整个文件；`codex_runtime.py:346` 的 `dynamicTools=`；app-server `item/tool/call`（dynamicToolCall）处理分支；`test_codex_tools*` 中只测动态工具的用例（改写为 MCP 用例） | grep `dynamicTools\|codex_tools\|DynamicToolCall` 为空 |
| Bash 调 `crew` | `guard._crew_coordination`（`guard.py:25–53,172–233` 中只为放行 `crew` 命令存在的部分）；角色守则里所有 `crew xxx` 命令教学；`cli.py` 中 AI 专用子命令（`whoami`、`who`、`task`、`card`、`ask`、`decision revise`、`withdraw`、`handoff(s)`、`report(s)`、`say`、`channel`、`send`、`personal`、`goal`、`context`、`todo add/withdraw`、`doc add`、`claim/unclaim`、`lease take/give`、`join`）——由生成 CLI 取代，生成 CLI 调用者固定为 `human` | grep 守则与提示词无 `crew say` 等；守卫测试改写 |
| 助手宿主动作 | `/api/assistant/native-action` 路由、`chat.personal_action`、`personal_actions.py` 的分派入口（动作实现函数保留为清单处理函数） | 路由覆盖测试 |
| 环境变量身份 | 第 7 节步骤 4、5 | grep |
| 角色技能注册 | `plugin/skills/*/SKILL.md` 不再注册为技能，精简内容并入行为提示词（`behavior_prompts` 的全局层默认值） | 插件清单无 `skills` |
| 重复路由 | `/api/goals/create`、`/api/goals/context` 与 `/api/goals/agent` 的同义动作合并为单一操作后，多余绑定删除；`git.settings.set enabled:false` 停用路径删除；CLI 兼容别名 `todos` 删除 | 路由覆盖测试 |

每一项删除在交付里列“删除清单 + 调用方核查”，不保留“以防万一”的旧分支。

---

## 12. 实测：两种载体能否在会话中途动态增加工具

**环境**：隔离配置目录（Claude：临时 `CLAUDE_CONFIG_DIR`；Codex：临时 `CODEX_HOME`，各自只复制登录凭据，实验后删除）；临时工作目录；子进程用 `env -i` 只保留系统必需变量，去掉桌面宿主注入的 `ANTHROPIC_*`、`CLAUDE_CODE_*` 与会话身份变量。未触碰 `~/.crew`、正式 Crew、`~/.claude/projects` 与 `~/.codex/sessions`（Crew 会扫描这两个目录，所以必须隔离）。版本：Claude Code 2.1.332，codex-cli 0.172.0。

**实验服务**：零依赖 Python MCP 服务（stdio 与 Streamable HTTP 两种模式，HTTP 需 `Authorization: Bearer <令牌>`，记录全部收发）。初始只有 `crew_search`；调用它后新增 `crew_secret_tool`（返回口令 `ZEBRA-4471`）并发出 `notifications/tools/list_changed`。脚本与完整日志在共享笔记目录 `arch-review-mcp-exp/`（`dyn_mcp.py`、`appserver_probe.py`、`run_claude.sh`、`run_codex.sh`、`logs/`）。

**提示词（极简）**：“调用 crew_search 工具（query 填 secret）。它会让新工具 crew_secret_tool 出现。然后调用 crew_secret_tool，把返回的口令原样回复。若看不到 crew_secret_tool 就只回答 NO_TOOL。”

**命令（摘要）**：

```bash
# Claude Code（Haiku 5.5）
CLAUDE_CONFIG_DIR=<临时> claude -p "$PROMPT" --model claude-haiku-5-5 --mcp-config <cfg.json> \
  --strict-mcp-config --allowedTools mcp__dyn --output-format stream-json --verbose
# cfg.json（HTTP 版）: {"mcpServers":{"dyn":{"type":"http","url":"http://127.0.0.1:<端口>/mcp","headers":{"Authorization":"Bearer <令牌>"}}}}

# Codex（gpt-6-luna，reasoning low）
CODEX_HOME=<临时> codex exec --skip-git-repo-check --json "$PROMPT"
# config.toml: model="gpt-6-luna" / model_reasoning_effort="low" / [mcp_servers.dyn] url=... bearer_token_env_var=...

# Codex app-server（Crew 的真实调用方式）：appserver_probe.py 发 initialize → thread/start → 两轮 turn/start，
# 模式 none / reload（两轮之间 config/mcpServer/reload）/ resume（reload 后 thread/resume）/ fresh（reload 后新开线程）
```

**结果**：

| # | 载体 / 形态 | 服务端收到的序列 | 模型行为 | 结论 |
| --- | --- | --- | --- | --- |
| 1 | CC Haiku 5.5 · stdio | `tools/list` → `tools/call crew_search` → 发 `list_changed` → **再次 `tools/list`** → `tools/call crew_secret_tool` | 经 CC 自带 ToolSearch 选中新工具，调用成功，回复“口令：ZEBRA-4471”，花费 $0.0042 | ✅ 支持 |
| 2 | CC Haiku 5.5 · HTTP + Bearer（通知随 `tools/call` 的 SSE 响应下发；GET 流返回 405） | 同上；客户端尝试过 `GET /mcp` | 同上，$0.0034 | ✅ 支持 |
| 3 | Codex luna low · `codex exec` · stdio | `tools/list` → `tools/call crew_search` → 发 `list_changed` → **无后续 `tools/list`** | 回答 `NO_TOOL` | ❌ 不支持 |
| 4 | Codex · exec · HTTP（POST 的 SSE 响应里带通知） | 同 3 | `NO_TOOL` | ❌ |
| 5 | Codex · exec · HTTP，另在 GET 常驻流里也推通知 | 同 3（GET 流已建立且收到通知） | `NO_TOOL` | ❌ |
| 6 | Codex · app-server · 第二轮再要求调用（`none`） | 第二轮开始时无 `tools/list` | 第二轮 `NO_TOOL` | ❌ 跨轮也不刷新 |
| 7 | Codex · app-server · 两轮间 `config/mcpServer/reload`（`reload`） | reload 后重新 `initialize` + `tools/list`（返回含新工具）；`mcpServerStatus/list` 也列出 `crew_secret_tool` | 已有线程第二轮仍 `NO_TOOL` | ❌ 线程工具集在开线程时固定 |
| 8 | 同 7 再 `thread/resume`（`resume`） | 同 7 | `NO_TOOL` | ❌ |
| 9 | 同 7 后**新开线程**（`fresh`） | 同 7 | 新线程 `NO_TOOL` | ❌ 同一 app-server 进程内也不生效 |
| 10 | 对照：服务端先解锁，Codex 再启动 | `tools/list` 即含新工具 | 直接调用成功，回复口令 | 连接建立时已有的工具可用 |
| 11 | Codex · exec · 令牌只用 `-c mcp_servers.crew.url=... -c mcp_servers.crew.http_headers.Authorization="Bearer ..."` 注入（配置文件无此服务） | 带正确令牌的 `initialize` / `tools/list` / `tools/call` | 调用成功 | ✅ 令牌可经启动参数注入，无需环境变量 |

**追加实验：载体是否原生延迟加载 MCP 工具**（受 t3code 用 Codex `omit_tools_from: ["deferred"]` 的启发补做）。服务端加 `--many N` 参数：启动即暴露 N 个工具，每个说明约 600 字（60 个共 41,194 字符）。

| # | 载体 | 条件 | 结果 |
| --- | --- | --- | --- |
| 12 | Codex luna low · exec · stdio | “只回复 OK，不要调用任何工具”，额外工具 0 个 vs 60 个 | 输入 token 19706 vs 19511（缓存 11904 相同）→ 工具定义不进上下文，**原生延迟加载** |
| 13 | Codex 同上 | 60 个工具下要求调用 `crew_op_37` | 调用成功，回复 `OP-37-OK`（本轮输入 59502 token，含搜索与调用往返） |
| 14 | CC Haiku 5.5 · stdio | 同 12 的对照 | 输入（缓存读 + 写）29678 vs 30578，只多约 900 token（init 列出 61 个工具名）→ **原生延迟加载** |
| 15 | CC 同上 | 60 个工具下要求调用 `crew_op_37` | 先 `ToolSearch select:mcp__dyn__crew_op_37`，再调用成功，花费 $0.0032 |

**设计取舍**（已写入第 0、5 节）：采用“权限即工具集 + 载体原生延迟加载”，两种载体同一工具面；Codex 用双服务名把核心工具直出。`list_changed` 动态加载与 Codex `crew_call` 作为被否决方案记在 5.4。实验 1–11 仍然有效：它证明了 Codex 不能靠 `list_changed` 中途加工具，所以设计里不让任何能力依赖中途改工具列表。

**未验证**：Crew 默认 `approvalPolicy=on-request` 时，Codex 对带 `destructiveHint` 的 MCP 工具是否发 `mcpServer/elicitation/request`（实验用的是 `never` 与只读工具）；R5 落地时用产品端点补测，`codex_client.py:269–300` 已有该请求的处理分支。延迟加载只测了 `-p` / `exec` 单轮与 60 个工具；Crew 实际是 CC 流式长会话与 Codex app-server，且真实清单约 200 个操作，R5 落地时按产品启动参数复测 token 代价与可调用性。Codex 的 `disabled_tools` / `enabled_tools` / `omit_tools_from` 三个键尚未在本机 0.172 上实测（5.2 已注明）。

---

## 13. 待主会话拍板

| # | 问题 | 推荐 |
| --- | --- | --- |
| 1 | 删除 `crew join`（AI 自收编外部会话） | 删除，收编统一走界面会话库；令牌模型下外部会话没有调用者身份 |
| 2 | 生成 CLI 的调用者固定为 `human` | 是。CLI 给人和脚本用；AI 不再经 shell 调 Crew |
| 3 | `usage` 系列保持主机命令，不进清单 | 是，它读本机原生日志目录，与业务数据无关 |
| 4 | 迁移期旧 URL 作为清单绑定保留，前端按领域逐步改 `/api/op` | 是，避免一次性改 40 多个前端调用点 |
| 5 | 用“权限即工具集 + 载体原生延迟加载”代替用户裁决 7 字面上的“默认只开放核心工具 + `crew_search` 按需加载” | 采用。目标（不撑大上下文、按需加载）不变，由载体完成，实测有效；`crew_search` 保留为用法指南。需记入决策日志 |
