# 操作清单与生成式 MCP 设计（方案 C）

> 日期：2026-11-27 · 作者：架构重审主题会话（Claude）· 基线 `3fdfbec`
> 依据：[总计划](REBUILD_PLAN_2026-11-27.md) 用户裁决 7、[Teamwork 设计](ONBOARDING_AND_TEAMWORK_DESIGN.md) 第 6 节、[入口全量盘点](OPERATIONS_INVENTORY.md)（事实底稿）、[架构重审](ARCHITECTURE_REVIEW.md)、[拆分边界](SPLIT_BOUNDARIES.md)
> 本文是阶段三“操作清单与助手（R5）”会话的施工依据。实测记录见第 12 节。
> **第 2 版（同日）**：按 `gpt-6.2-astra` 只读评审修订（要点与取舍见架构重审附录 A）：工具暴露回到用户裁决 7 的“核心工具 + `crew_search`”为施工基线，原生全量注册改为 R5 内 A/B 复测的候选；补威胁模型、委派与撤权规则、设备两种形态、按判别条件绑定、确认与幂等的边界、桌面 IPC 覆盖。

---

## 0. 结论

1. **清单是唯一事实来源。** 每个业务动作登记一次：名称、参数格式、读写级别、风险、各角色可用范围、加锁与准入、对外绑定（HTTP 路径 / CLI 命令 / MCP 工具）。HTTP 路由表、CLI、MCP 工具、助手指南、覆盖测试全部从清单生成或对照清单检查。
2. **AI 只走 MCP。** Crew 后端在本机 HTTP 端口上提供 `/mcp`（Streamable HTTP，无常驻流），每个托管会话启动时拿到自己的令牌（经 CC 的 `--mcp-config` 文件与 Codex app-server 的 `-c` 参数注入，不放环境变量，防止子进程与测试意外继承身份；威胁模型见 3.4）。后端按令牌认出调用者、按清单卡权限。Bash 调 `crew`、Codex `dynamicTools`、`/api/assistant/native-action` 三条旧路径整条删除。
3. **工具暴露：施工基线按用户裁决 7，原生全量注册作为候选做 A/B（已用真模型实测，第 12 节）。**
   - **基线（裁决 7）**：默认只列核心工具 + `crew_search`。Claude Code 支持会话中途 `notifications/tools/list_changed`（实测：收到后立即重拉并能调用新工具），`crew_search` 命中后服务端把这些操作加入该会话可见集并发通知。Codex 只在 MCP 连接建立时读一次工具列表（实测：`list_changed`、`config/mcpServer/reload` 后服务端已重新列出新工具、`thread/resume`、同进程新开线程，线程都仍不能调用新工具），所以 Codex 会话用固定核心集 + `crew_search`（返回调用方式）+ `crew_call(op, args, request_id)`。这是明确的按载体差异，不做隐式回退；`crew_call` 由服务端按具体操作名逐个鉴权、校验、确认与审计，代价是 Codex 侧失去逐工具的原生审批粒度。
   - **候选（权限即工具集）**：实测两种载体都会把 MCP 工具藏在自带的工具搜索后面——60 个长说明工具，CC 只多约 900 token，Codex 几乎不增加，且都能按名字找到并调用。若在 R5 的产品形态复测（约 200 个真实操作、相似名称、不透露工具名的中文意图、长会话、恢复、权限收紧、高风险操作）中发现成功率与误选不劣于基线，则改为“每个会话一开始拿到全部有权限的操作工具”，两种载体同一工具面、去掉 `crew_call` 与 `list_changed` 依赖；`tools/list` 由少数变为全部是可观察的契约变更，须记入决策日志。
   - 两种模式在 R5 中作为**显式配置**同时实现以便对比，复测结束后删除落选的一种，不长期并存。
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
    lock: str                 # 'outer_write' | 'self_managed' | 'read'：W2 的 B/C 段为 outer_write；A 段为 self_managed（分支内部自己加锁，不等于无锁）
    admission: str            # 'model_work' | 'none'：在解析出具体操作后判定（同一 URL 不同 action 可不同，如 goals/agent 的 report.preview）
    project: str              # 'none' | 'selected' | 'chat' | 'body'（来自 W2 的项目解析规则）
    handler: Callable         # handler(ctx, args) -> dict；ctx 含 caller、conn、project、now、service
    http: tuple[Binding, ...] # 现有 URL 绑定，键为（方法, 路径, 判别条件）：判别条件如 action 值；迁移期保留，新客户端统一走 /api/op
    ipc: tuple[str, ...]      # 桌面直接调用的 IPC 通道 / worker 动作（如 desktop:settings-save 经运维 worker），共享业务写入必须映射到同一操作
    cli: CliSpec | None       # 生成 CLI 的命令路径与位置参数；None = 不出 CLI
    core_for: frozenset[str]  # 在哪些角色的 MCP 默认工具集里（其余经 crew_search）
    emits: tuple[str, ...]    # 写成功后产生的事件类型（给“快照 + 序号”事件流）
```

规则（由清单自检测试强制，第 10 节）：

- `access == 'read'` ⇒ `hints.read_only`、`lock in ('none','read')`、不得写库（测试用只读连接执行全部读操作的样例参数）。现在“读里带写”的几处（文档列表刷新、助手状态建助手、标签补登记、`crew_channel` 推进游标）拆成显式写操作或改为后台步骤。
- `access == 'write'` ⇒ `params` 必须含 `request_id`（字符串，必填）；`emits` 非空。
- `risk == 'high'` ⇒ `hints.destructive`；对 AI 角色走确认请求（第 5.6 节）。
- `scopes` 只能用第 4 节列出的角色名；`target` 与角色组合必须在权限表里有定义。
- 每个（方法, 路径, 判别条件）绑定唯一；授权与准入在解析出具体操作**之后**执行；每个 `cli` 命令路径唯一；工具名唯一。

---

## 3. 调用者与身份

### 3.1 调用者（`Caller`）

| 角色 | 如何认出 | 说明 |
| --- | --- | --- |
| `human` | 本机界面 / 本机 CLI：现有本机来源检查 + `X-Crew: 1` | 用户本人；CLI 默认就是 `human`，不再从环境变量读会话 |
| `device_browser` | 远程浏览器 Cookie（现有 `remote_access`） | 用户的其他设备上的浏览器；按现有**允许清单**（`remote_routes.py:7–27`）放行，并保留“远程正文不得带 `caller_session_id` / `actor_*` 等 AI 身份字段”的规则（`remote_routes.py:54–72`） |
| `device_desktop` | 远程桌面客户端 Bearer | 局域网连接主机的 Crew 桌面；按现有**拒绝清单**（`remote_routes.py:30–45`）处理，比浏览器多工作区、存储、连接等设置能力 |
| `assistant` | MCP 令牌，签发给全局助手会话 | 跨工作区；写操作至少 `confirm` |
| `leader` | MCP 令牌，签发给 leader 总对话 | 限本工作区；目标约束按条目 |
| `execution` | MCP 令牌，签发给成员 / 目标内执行（含 execution_id、goal_id） | 只能动自己的目标与执行 |
| `git_admin` | MCP 令牌，签发给 Git 助手会话 | 本工作区 Git 管理写权限 + leader 的只读集 |
| `ops` | 运维对话 | **不给 Crew 业务工具**（现状如此，保持） |

### 3.2 令牌

- 后端在启动托管会话（CC 总对话、CC 目标执行、Codex app-server 新开与恢复）前签发：随机 32 字节，库里只存哈希。表 `mcp_tokens(token_hash PK, session_id, execution_id NULL, goal_id NULL, role, runtime, project, own_scopes JSON, delegable_scopes JSON, issuer_token_hash NULL, created_at, revoked_at NULL)`。
- 注入层：CC 在 `chat._launch_native` 与 `goal_runtime` 构造命令行处加 `--mcp-config <每会话文件>`；Codex 在 `codex_client.py:103` 构造 app-server 命令处加 `-c mcp_servers.crew...`（不是改 `thread/start` 参数，也不写用户的 `~/.codex/config.toml`）。正常退出、启动失败、异常退出三条路径都吊销令牌并删除每会话文件。
- 后台重启时，旧令牌全部吊销，托管进程按现有“自动续回”重新启动并拿新令牌。

### 3.3 委派与撤权

- **两种权限分开存**：`own_scopes` = 本人能做的操作；`delegable_scopes` = 能授予由它启动的会话的操作（例如 leader 自己不能 `goal.finish` 别人的目标，但能把 `goal.finish(own_goal)` 委派给它派出的执行）。签发子令牌时：子 `own_scopes` = 子角色默认集 ∩ 父 `delegable_scopes`，项目、目标、执行约束一并收窄。助手的 `delegable_scopes` 含 leader 默认集（用于 `leader.create`），不含 `execution.spawn`（助手不直接派执行）。
- **每次调用都检查当前有效性**：令牌未吊销 → 会话仍在岗（未归档 / 未停止）→ 祖先链上每个签发令牌都未吊销 → 操作在 `own_scopes` 内且仍在**当前**权限表允许范围内（表收紧立即生效）→ 目标约束 → 处理函数自己的归属校验。父会话被停止或归档时级联吊销其签发的令牌。
- 新增权限（表放宽、角色升级）只在会话下次启动重签令牌后生效；工具列表里暂时多出已失效的工具名可以接受，但调用必被拒。

### 3.4 威胁模型（写清楚能防什么、不能防什么）

- **能防**：意外继承与误用。令牌不进环境变量，托管会话在 shell 里起的子进程（codex-dispatch、claude-dispatch、测试）不会**意外**带上会话身份——这是现在 `CREW_SESSION_ID` / `CLAUDE_CODE_SESSION_ID` 泄漏的根因；AI 角色的权限与确认由后端执行，提示词写错或模型误判不会越权。
- **不能单靠令牌防**：同一操作系统用户下主动作恶的进程。每会话配置文件、进程命令行对同用户可见；本机 HTTP 的 `human` 身份只靠本机来源与 `X-Crew: 1`，同用户进程都能发。这与现状相同，不在本设计里假装解决。
- **纵深措施**：① 守卫（`guard.check`）拒绝托管会话 shell 里直接访问 Crew 本机 API（`localhost:<端口>/api`、`/mcp`）、`crew` CLI 与 `CREW_HOME` 下数据库 / 令牌文件的命令，把“绕过 MCP 冒充 human”挡在常见路径上；② 每会话文件放 `CREW_HOME/runtime/mcp/`，Windows 上设当前用户 ACL，进程退出即删；③ 令牌不写日志（日志只记哈希前缀）；④ Codex `workspace-write` 沙箱下的网络限制按 Codex 自身配置生效，不依赖它。要真正隔离不受信任的执行，需要独立的 OS 用户或容器，列为后续议题。
- `/mcp` 只接受本机连接并校验 `Origin`（有 `Origin` 头且不是本机 Crew 页面来源即拒绝，防 DNS 重绑定）；远程监听对 `/mcp` 一律拒绝（加入 `remote_routes` 拒绝清单并由覆盖测试检查）。

---

## 4. 权限范围表

本表是**摘要**，用于说明方向；每个操作的确切角色、目标约束与前置条件写在清单条目里，并由 R5 按**现有入口的实际行为**逐条写正反例测试来校验（不能用同一张表既生成实现又生成预期，那会把错误固化）。有意收紧现有行为的必须单独记为产品行为变更。D 列在实现时拆成 `device_browser`（允许清单）与 `device_desktop`（拒绝清单），以现有 `remote_routes.py` 为准。

“✓”= 可用；“自”= 只能作用于自己（`target=self`）；“组”= 本组（自己领导的成员、自己发起的执行）；“目标”= 自己负责或参与的目标；“确认”= `risk=confirm`，须挂原始用户消息；“界面确认”= `risk=high`，AI 发起会生成用户确认请求；空 = 不可用。

| 领域（操作前缀） | human | device | assistant | leader | execution | git_admin |
| --- | --- | --- | --- | --- | --- | --- |
| 查询：`workspace.state`、`session.whoami/who`、`decision.list`、`todo.list`、`document.list`、`goal.get` | ✓ | ✓ | ✓ 跨工作区 | ✓ 本工作区 | ✓ 本工作区（`document.list` 等按当前执行的 `goal_id` 过滤，保留现有目标过滤） | ✓ 本工作区 |
| 查询：`workspace.list`、`channel.list` | ✓ | ✓ | ✓（仅 `workspace.list`） | ✓（仅 `channel.list`） | | |
| 自身：`session.task.set`、`session.card.*`、`session.rename` | ✓ 任意会话 | ✓ | | 自 | 自（不可改名） | 自 |
| 协作消息：`message.send` | | | ✓ 确认（以用户来源代发） | ✓ 本工作区 | ✓ 本工作区 | ✓ 本工作区 |
| 交流区：`channel.post` | ✓（以用户身份） | ✓ | | ✓ | | |
| 交流区：`channel.consume`（读并推进本人游标） | | | | ✓ | | |
| 拍板：`decision.ask/revise/withdraw` | | | | ✓ | ✓（挂在执行上） | ✓ |
| 拍板答复：`decision.answer` | ✓ | ✓ | ✓ 确认 | 组（原归属组，按授权模式） | | |
| 拍板流转：`decision.discuss/ready/clarify` | ✓ | ✓ | ✓ 确认 | | | |
| 待办：`todo.add/withdraw` | | | | ✓ | ✓ | ✓ |
| 待办处理：`todo.resolve/undo` | ✓ | ✓ | ✓ 确认 | | | |
| 文档：`document.register/refresh` | ✓ | ✓ | | ✓ | ✓ | ✓ |
| 建人：`leader.create` | ✓ | ✓ | ✓ 确认 | | | |
| 派执行：`execution.spawn` | ✓ | ✓ | | 组 | | |
| 停 / 归档：`session.stop/archive`、`group.close` | ✓ | 浏览器无 `group.close`（以允许清单为准） | ✓ 确认 | 组 | 自（仅 stop） | 自（仅 stop，现有 `stop_identity` 允许已登记调用者停自己） |
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

1. 委派与撤权按 3.3：子会话 `own_scopes` = 子角色默认集 ∩ 父 `delegable_scopes`；每次调用检查令牌、在岗状态、祖先链与当前权限表。
2. **授权模式**（`trust_mode`：由我拍板 / 自主推进）不改变清单权限，只改变 leader 的 `decision.answer` 等是否需要升级给用户，由处理函数判断——维持现状。
3. 现有处理函数里已做的归属与可见范围校验（如“只能停本组成员”“只能撤回自己的拍板”“拍板答复要求调用者是原归属 leader 且当前组有效”“执行侧文档列表按当前目标过滤”）保留在处理函数内；清单的 `target` 是第一道闸，处理函数是第二道，测试分别覆盖，通用层不得因为“已过角色表”而跳过处理函数的过滤。

---

## 5. MCP 服务形态

### 5.1 传输

- 端点：Crew 后端现有端口上的 `POST /mcp`（Streamable HTTP，MCP 2025-06-18 起的形态）。`GET /mcp` 返回 405（规范允许“服务器不提供独立 SSE 流”）。
- 客户端 POST 来的是 JSON-RPC **请求**时：普通结果回 `application/json`；基线模式下 CC 会话的 `crew_search` 加载了新工具时，响应改为 `text/event-stream`，先发一条 `notifications/tools/list_changed` 再发结果后关闭（实测第 2 行即此形态）。客户端 POST 来的是**通知或响应**（如 `notifications/initialized`）时：回 `202 Accepted`、空响应体（规范要求）。
- 不发 `Mcp-Session-Id`（服务器无协议会话状态；调用者身份只来自令牌，Codex 的两个服务别名共用一个令牌也不冲突）。
- 鉴权：`Authorization: Bearer <令牌>`。缺失或吊销 → 401；权限不足 → 工具结果 `isError: true` + 结构化错误（`forbidden`、原因、需要的角色），不用 HTTP 403，避免载体把它当连接故障重试。`Origin` 校验见 3.4。
- `initialize`：基线模式返回 `capabilities.tools.listChanged = true`（候选模式为 `{}`）；`serverInfo.name = "crew"`。

### 5.2 工具集

工具名为操作名把点换成下划线（`channel.post` → `channel_post`）；`inputSchema` 就是清单 `params`；`annotations` 来自 `hints`（`readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`），`title` 用 `summary`，`description` 写清何时用、与相近操作的区别。

| 角色 | 核心工具（`core_for`） |
| --- | --- |
| leader | `session_whoami`、`workspace_state`、`message_send`、`channel_post`、`channel_consume`、`decision_ask`、`execution_spawn`、`session_stop`、`todo_add`、`document_register`、`goal_get`、`goal_finish`、`crew_search` |
| execution | `session_whoami`、`message_send`、`decision_ask`、`todo_add`、`document_register`、`goal_get`、`goal_work_update`、`goal_context_put`、`goal_report_publish`、`goal_finish`、`crew_search` |
| assistant | `workspace_state`、`session_detail`、`goal_get`、`chat_send`、`leader_create`、`decision_answer`、`todo_resolve`、`note_capture`、`notes_list`、`crew_search` |
| git_admin | `session_whoami`、`git_requests_list`、`git_branches_list`、`git_policy_get`、`git_request_resolve`、`message_send`、`crew_search` |
| Codex 会话（基线模式）额外 | `crew_call` |

**基线模式（用户裁决 7，施工默认）**：`tools/list` = 核心工具 ∩ 有效权限 + 该会话已加载的工具。CC 会话：`crew_search` 命中项加入该会话（任务执行按执行区分）的“已加载集”并随响应发 `list_changed`；已加载集存于 `mcp_loaded_tools`，不随令牌轮换清空——原生进程或后台重启后恢复的会话会按历史直接调用这些工具（2026-11-27 真实模型实测：按令牌清空时，恢复的助手调用已找到的 `goal_create` 被 Claude Code 拒绝后直接放弃）。Codex 会话：已加载集不变，`crew_search` 结果写明“用 `crew_call` 调用，`op` 填 `<操作名>`”并附完整 `params`；`crew_call` 与直接调用同名工具走完全相同的鉴权、参数校验（按 `op` 的 schema 校验 `args`）、确认与幂等，不是后门。载体类型在签发令牌时记录（`runtime` 字段），不从 `clientInfo` 猜。

**候选模式（权限即工具集）**：`tools/list` = 有效权限内全部操作 + `crew_search`（只读用法指南，不加载）。Codex 照 t3code 注册两个服务名：`crew`（全部工具，走 Codex 的延迟加载，`disabled_tools` = 核心工具）与 `crew_core`（`enabled_tools` = 核心工具，`omit_tools_from = ["deferred"]` 直出），写法见 t3code `CodexAdapterV2.ts:1747–1761`；这三个键尚未在本机 Codex 0.172 上实测。

**R5 内的 A/B 复测**（决定保留哪种）：使用真实约 200 个操作 schema；任务用不透露工具名的中文意图，并包含名称相近的操作；覆盖冷启动、长会话（CC 流式长会话、Codex app-server 多轮）、恢复、权限收紧后调用、普通与高风险操作；记录发现成功率、误选、额外往返、首次调用耗时、输入与缓存 token、审批是否以正确工具粒度出现。模型按裁决 5（Haiku 5.5、`gpt-6-luna` low）。结论记入总计划决策日志，删除落选模式。

### 5.3 `crew_search`

- 参数：`query`（中文或操作名片段），可选 `domain`。只在调用者有效权限集里搜；按名称、`summary`、`description` 打分，返回前 8 条：工具名、`summary`、何时用、风险（`confirm` 要带什么证据，`high` 会生成用户确认请求）、相近操作的区别、调用方式（随模式与载体不同，见 5.2）。

### 5.4 两种模式的取舍记录

候选模式的好处：两种载体同一工具面，不需要按载体分叉的搜索结果文字和执行路径；Codex 侧保留逐工具注解与原生审批粒度；不依赖 `list_changed`。保留基线的理由（评审意见，主控采纳）：实验 12–15 只是 60 个合成工具、按名字点名调用的单轮对照，不能证明约 200 个真实操作下的发现率与误选；“暴露全部”改变了裁决 7 字面上的默认契约，须以产品形态证据为准。两种模式共用清单、鉴权、处理函数，差别只在 `tools/list` 与 Codex 启动参数，A/B 成本小。

### 5.5 幂等（命令带 ID 只执行一次）

- 表 `op_requests(caller_key, request_id, op, args_hash, status, result JSON, last_seq, created_at, finished_at)`，主键 `(caller_key, request_id)`；`caller_key` 用**稳定调用身份**：`session:<id>` / `execution:<id>` / `human` / `device:<设备 ID>`，不用令牌哈希（令牌轮换后同一请求仍能去重）。
- 同一 `request_id` 重复到达：参数相同 → 返回已存结果并带 `replayed: true`，不再执行；参数不同 → 409 `request_id_reused`；之前被拒绝 → 重放拒绝原因；执行中 → `status: running`。
- **承诺按副作用分级**：只改 Crew 数据库的操作，业务写入、回执与增量事件在同一事务提交，保证“同一请求只提交一次”。涉及库外副作用的操作（向模型投递输入、启动进程、写文件、打开主机程序、外部服务）采用“先记意图 → 提交 → 执行 → 记结果”：承诺的是**可查询、不盲目重发、结果不明可识别**（状态 `unknown` 显示为“待核实”），而不是“恰好一次”。后台重启时扫描遗留的 `running` 回执，按可查询的副作用对账，查不清的标为 `unknown`。
- 保存 7 天，随存储清理回收；过期后同一 `request_id` 视为新请求（客户端不会在 7 天后重试同一请求）。现有便笺 `request_id` 回执（`personal.py`）并入这张表。
- 迁移期旧 URL 绑定：旧前端请求没有 `request_id`，绑定层**显式**为其生成一次性 ID（不提供去重），并在覆盖测试里把这些绑定标为“旧绑定”；前端改用 `/api/op` 后删除。

### 5.6 风险与确认

- `risk=confirm`：AI 调用必须带 `message_id`（触发它的那条用户消息），后端按 `personal.py:887–901,1028–1077` 的做法核实该消息存在、来源为 `human` / `qq` / `lark`、角色为用户。这只是**来源证据**（证明用户说过话），不等于用户同意了这个具体操作；因此 `confirm` 只用于低到中风险、可撤销的操作。
- `risk=high`：AI 调用不直接执行，而是生成一条**用户输入请求**（架构重审 6.3 的统一结构，`kind=confirmation`），内容绑定发起者、操作名、目标、参数哈希与目标当前版本，一次性有效；工具立即返回 `status: awaiting_user` 与请求 ID。用户确认后，后端执行前再次检查发起者当前授权与目标版本，不一致则拒绝并回告。结果经事件流与 `message.send` 回告发起会话；拒绝、过期同样回告。
- 用户本人（`human` / `device_*`）在界面上点的高风险按钮保持现有二次确认对话框，不走请求表。

### 5.7 错误

工具错误统一为 `{"error": {"code": "...", "message": "...", "detail": {...}}}`，`code` 取自固定集合：`forbidden`、`invalid_arguments`、`not_found`、`conflict`、`request_id_reused`、`awaiting_user`、`paused_for_update`、`unknown_outcome`、`internal`。`message` 保留处理函数的真实原因（现有中文报错原样透出），不吞错。

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
| 2 | `/mcp` 端点 + 清单驱动的工具执行；基线模式（核心 + `crew_search`，CC `list_changed` 加载，Codex `crew_call`）与候选模式（权限即工具集）两种显式配置，按 5.2 做 A/B | 第 12 节实验复跑到产品端点；A/B 结论记入决策日志后删除落选模式 |
| 3 | hooks 不再从环境变量认会话：现在 `hooks.py` 用环境变量做三件事——判断是否 Crew 托管进程（`CREW_SESSION_ID` 是否存在，`hooks.py:161,168`）、目标执行身份（`CREW_EXECUTION_ID`，`hooks.py:69,76`）、新会话首次登记的名字与角色（`CREW_NAME` / `CREW_ROLE` / `CREW_GROUP` / `CREW_SPAWN_ID`，`hooks.py:154–156`）。改为**按载体 payload 里的 `session_id` 查库**：后台在启动托管进程前已登记它的原生会话 ID、所属执行与待登记身份（CC 由 Crew 用 `--session-id` 指定原生 ID，目标执行的原生 ID 绑定已在 `goal_runtime`），存在有效令牌即视为托管。不改插件 `hooks.json`，也不新增每会话 `--settings`，避免与插件 hook 重复触发 | `test_hooks*`、目标执行 hook 测试、守卫相关测试；外部（非托管）CC 会话的 hook 行为不变 |
| 4 | 删除注入：`chat.child_environment()`（`chat.py:194–215`，总对话与目标执行都用）不再注入 `CREW_NAME` / `CREW_ROLE` / `CREW_GROUP` / `CREW_SPAWN_ID`；`chat.py:1053`、`codex_runtime.py:292`（及 `:170–172` 守卫命令行）、`goal_runtime.py:313–317` 不再注入 `CREW_SESSION_ID` / `CREW_EXECUTION_ID` / `CREW_GOAL_ID`。`child_environment` 与 `codex_runtime.py:290`、`channel_digest.py:45,83`、`native_skills.py:30`、`operations_chat.py:103`、`operations_worker.py:29`、`desktop/src/operations.cjs:19` 里**剥离**这些变量的代码保留（防外层残留） | grep 注入点为空；托管会话 shell 中 `echo $CREW_SESSION_ID` 为空 |
| 5 | 删除读取：`cli.py:520,527,534,579,584,601,622,627,722,748,759,827,954,1053`、`spawn.py:271,304–305`、`hooks.py:69,76,154–168` | 交付列“读取 / 注入 / 剥离”三张清单，逐条标“已删 / 保留（剥离）” |
| 6 | 派工展示关联：`integrations/codex-dispatch/run_codex.ps1:100–117` 与 `integrations/claude-dispatch/scripts/run_claude.ps1:117–133` 现在从环境变量记录“由谁派出”再清理。改为托管会话经 MCP `session.whoami` 拿到逻辑会话 / 执行 ID，作为参数显式传给脚本；`codex_runs.jsonl` 按逻辑 ID 关联到发起人卡片 | 派工登记仍显示在发起人卡片下（第四张清单：展示关联） |

迁移期间不保留“令牌失败就回退环境变量”的分支：步骤 1–6 在同一个合入里完成，由第 12 节实验 + 阶段五真实模型实测把关。前置条件：`risk=high` 依赖的“用户输入请求”最小闭环（架构重审第 7 节）已上线，否则只上线不含高风险操作的领域。

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
| `runtimes.readiness` | 主机（只读，运行各载体自带的登录状态命令） | H D | GET `/api/runtimes/readiness` | — |
| `onboarding.get` / `onboarding.set` | 读 / 写 | H D | GET / POST `/api/onboarding` | — |
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

- `STATIC['/goal-versions-ui.js']`（`web.py:2051`）指向不存在的文件：删除会改变该 URL 的响应（从 500 类错误变 404），放进阶段二第 4 步死代码清理，不在清单落地时顺手删。
- 无调用方的写路由：`/api/attention/dismiss`、`/api/chat/interrupt`、`/api/comments`(POST)、`/api/session/restore`、`/api/roster/adopt`（`/remote/logout` 属远程认证协议，按 8.14 作非操作入口，不迁 `/api/op`）。登记进清单（操作仍有效，助手与手机端可能用），但不再各自维护一个 URL；迁移完成后只保留 `/api/op` 访问。

---

## 9. 统一 HTTP 入口

- 新增 `POST /api/op`：`{"op": "...", "args": {...}, "request_id": "..."}`；读操作也可 `GET /api/op/<name>?<args>`。调用者由本机来源 / 设备认证决定（`human` / `device`），远程设备按清单的 `D` 范围放行——`remote_routes` 的路径清单由清单生成，不再手写。
- 旧 URL 只作**过渡**绑定（同一个处理函数，不是两套代码）：R5 会话结束前前端调用全部改走 `/api/op` 并删除旧绑定，不留到后续（决策日志 2026-11-27）。桌面 IPC 与本机 worker 按第 10 节第 9 条登记。手机端从第一天就只用 `/api/op` + 事件流。

---

## 10. 覆盖测试

新增 `plugin/tests/test_operations_registry.py`（Python）与 `plugin/tests/test_operations_frontend.js`（Node），全部是静态或纯函数测试，秒级完成：

1. **路由全覆盖**：`web` 实际可达的每个（方法, 路径, 判别条件）——W2 后为外层直接处理集合 ∪ 各段路由表 ∪ 条件分支，最终为清单生成的绑定——必须等于“清单 `http` 绑定 ∪ 8.14 白名单”。多一个少一个都失败，失败信息列出路径与建议的操作名；旧绑定（无 `request_id`）单独标出并统计数量。
2. **按 action 分派的入口**：`/api/personal/notes`、`/api/personal/tags`、`/api/behavior-prompts`、`/api/goals/agent`、`/api/goals/action`、`/api/goals/context` 的每个合法 `action` 值都映射到一个操作（从处理函数的动作表读取，如 `personal.py` 的动作常量、`goal_actions.dispatch` 的分派表）。
3. **界面改数据动作全覆盖**：扫描 `frontend/*.js`、`frontend/app/*.js`、`desktop/src/*.cjs`、`desktop/src/home*.js` 中 `api(` / `fetch(` / `post(` 调用的路径字面量与 `method:"POST"`，每个非 GET 路径必须命中清单绑定；动态拼接的路径必须在文件内声明 `// crew-op: <操作名>` 注释，否则失败（逼迫新代码写字面量或声明）。
4. **CLI 全覆盖**：生成的 argparse 树中每个叶子命令必须对应一个操作或在主机命令白名单里；反之每个 `cli != None` 的操作都能在树里找到。
5. **MCP 工具全覆盖**：对每个角色、每种载体模拟签发令牌后调用 `tools/list`：基线模式 = 核心工具 ∩ 有效权限（Codex 另加 `crew_call`），CC 经 `crew_search` 加载后出现被加载项；候选模式 = 有效权限内全部操作 + `crew_search`。`crew_search` 对每个可用操作都能按名字搜到；`crew_call` 对有效权限外的 `op` 一律拒绝。
6. **权限穷尽**（借鉴 t3code 的“权限穷尽声明”）：`Operation` 构造时 `scopes`、`access`、`risk`、`target` 缺一即抛错；测试遍历 `所有操作 × 所有角色`，断言 `authorize()` 的结果与第 4 节表（以数据形式存于 `ops/scopes.py`）一致；新增角色或操作而不更新表即失败。
7. **字段一致性**：第 2 节的规则（读 ⇒ 只读注解与只读连接、写 ⇒ `request_id` 与 `emits`、high ⇒ destructive）逐条断言；读操作用样例参数在只读连接上执行一遍，写库即失败。
8. **远程边界**：`/mcp`、全部 `host` 类操作对 `device_*` 拒绝；`device_browser` 的可用集合等于现有允许清单，`device_desktop` 的拒绝集合等于现有拒绝清单（迁移前后对比）。
9. **桌面 IPC 与本机 worker 全覆盖**：从 `desktop/src/preload.cjs` / `main.cjs` 实际注册的 `ipcMain.handle` 通道与运维 worker 的动作表取集合（如 `desktop:settings-save` → 运维 worker 的 `save_settings`），共享业务写入必须映射到清单操作的 `ipc` 绑定；只影响本设备的能力（主题 `desktop:theme-set`、启动设置、远程主机配对等）登记为带理由的“本机能力”白名单，保留脱离后端也能运行的方式。
10. **预期集合独立维护**：上面各项的“应有入口”清单（路由、action 值、IPC 通道、CLI 叶子）从源码扫描或手写的独立文件取得，不从清单生成——避免“用清单生成路由，再用清单证明路由齐全”。

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

**设计取舍**（已写入第 0、5 节）：实验 1–11 证明 CC 可用 `list_changed` 中途加工具、Codex 不行，因此基线模式里 Codex 走 `crew_call`；实验 12–15 证明两种载体都原生延迟加载 MCP 工具，使“权限即工具集”成为可行候选，但它只覆盖 60 个合成工具的点名调用，按评审意见不据此改写裁决 7，而是在 R5 里按 5.2 的 A/B 复测决定。

**未验证**：Crew 默认 `approvalPolicy=on-request` 时，Codex 对带 `destructiveHint` 的 MCP 工具是否发 `mcpServer/elicitation/request`（实验用的是 `never` 与只读工具）；R5 落地时用产品端点补测，`codex_client.py:269–300` 已有该请求的处理分支。延迟加载只测了 `-p` / `exec` 单轮与 60 个工具；Crew 实际是 CC 流式长会话与 Codex app-server，且真实清单约 200 个操作，R5 落地时按产品启动参数复测 token 代价与可调用性。Codex 的 `disabled_tools` / `enabled_tools` / `omit_tools_from` 三个键尚未在本机 0.172 上实测（5.2 已注明）。

---

## 13. 待主会话拍板（2026-11-27 已全部裁决：按推荐，第 4 条加限定，见决策日志）

| # | 问题 | 推荐 |
| --- | --- | --- |
| 1 | 删除 `crew join`（AI 自收编外部会话） | 删除，收编统一走界面会话库；令牌模型下外部会话没有调用者身份 |
| 2 | 生成 CLI 的调用者固定为 `human` | 是。CLI 给人和脚本用；AI 不再经 shell 调 Crew |
| 3 | `usage` 系列保持主机命令，不进清单 | 是，它读本机原生日志目录，与业务数据无关 |
| 4 | 迁移期旧 URL 作为清单绑定保留，前端按领域逐步改 `/api/op` | **已裁决（2026-11-27）**：按推荐，加限定——旧 URL 只作过渡绑定，R5 会话结束前前端调用全部改走清单入口并删除旧绑定 |
| 5 | 工具暴露：施工基线按裁决 7（核心 + `crew_search`，CC 用 `list_changed`，Codex 用 `crew_call`），“权限即工具集”作为 R5 内 A/B 复测的候选 | 采纳评审意见：先按裁决施工，A/B 结果出来后由主会话记入决策日志并删除落选模式 |
| 6 | 威胁模型只承诺“防意外继承与误用”，同用户恶意进程不在本设计范围；守卫拦截 shell 直连 Crew API / 数据库作为纵深措施 | 是；真正隔离需独立 OS 用户或容器，列为后续议题 |

---

## 14. R5 实施记录（2026-11-27）

**落地形态**（与前文不同处以本节为准）：

| 项 | 实施 |
| --- | --- |
| 清单 | `plugin/crew/ops/`：`registry`（登记与自检）、`schema`（零依赖校验）、`executor`（鉴权 → 校验 → 风险 → 回执 → 准入 → 加锁 → 执行门禁）、`scopes`、`tokens`、`mcp`、`search`、`launch`、`guide`、`cli`、`confirmations`、`domains/*`。共 232 个操作。 |
| HTTP | 只剩 `POST /api/op`（信封 `{op,args,request_id,project}`）、`POST /api/op/<操作名>`（正文即参数，请求号放 `X-Crew-Request-Id`，页面用这种）、`GET /api/op/<读操作>`（原样返回 JSON / HTML / 文件，给 `<img>`、下载链接用），加 8.14 的传输入口。旧业务 URL 全部删除；操作的处理函数仍是原 `web_api` 函数（`legacy` 适配器把它当初写给的路径和判别值传进去）。 |
| 设备 | `remote_routes` 只决定“能到哪些地址”（`/api/op*`、静态、传输入口），能做什么看操作的 `device_browser` / `device_desktop` 范围（冻结在测试的独立预期里）；“远程不得冒用 AI 身份”的正文规则移进执行器。 |
| MCP | 基线模式按裁决 7：核心工具 + `crew_search`；CC 搜索后服务端发 `list_changed` 加载，Codex 用 `crew_call`。候选模式（权限即工具集）未实现，A/B 复测未做（见下）。 |
| 令牌注入 | CC：`--mcp-config <每会话文件>` + `--allowedTools mcp__crew` + `--append-system-prompt <工具指南>`；Codex：`thread/start` 的 `config.mcp_servers.crew`（不用 `-c`，实测可用）+ 指南并入 developerInstructions。令牌在签发时登记为“启动中”，进程创建后绑定，进程退出即失效，启动失败立即吊销，后台重启全部吊销。 |
| 人用操作的参数 | 每个操作有 `params`；对其他 AI 角色另有 `ai_params` 与 `ai_description`（同一操作人和 AI 的参数形状不同时，如 `channel.post`）。 |
| 全局助手 | 143 个操作。凡用户能做、又不属于主机管理（`admin/ops_chat/ops_config/connections/remote_access/storage/data/sounds`、在主机上开程序、`approval.resolve`、`interaction.*`、助手页自身）的操作都对助手开放，以用户身份运行同一处理函数，参数由用户参数收紧生成（`assistant_params`，必须带 `project`）；写操作一律 `confirm`（附用户原话 `message_id`），`high` 走确认请求。 |
| 高风险确认 | `interaction_requests` 表 + `interaction.list/resolve`；AI 调 `risk=high` 返回 `awaiting_user` 与确认编号，用户在**助手页**确认后按发起者当前权限执行，结果回告发起会话。 |
| 守则 | `plugin/skills/` 删除、插件不再注册技能；四个守则精简后移到 `plugin/prompts/roles/`，工具说明改由 `ops/guide.py` 按角色与载体生成后在启动时注入；“守则已读 / 重读提醒”机制随技能一起删除。 |
| 身份 | 不再注入或读取 `CREW_SESSION_ID / CREW_EXECUTION_ID / CREW_GOAL_ID / CREW_NAME / CREW_ROLE / CREW_GROUP / CREW_SPAWN_ID`；剥离代码保留。hooks 以“会话有未吊销令牌”判托管、以 `goal_execution_bindings` 认目标执行；守卫拦托管会话在 Shell 里调 `crew`、Crew 后台端口、HOME 下的 `crew.py`/`crew.db`/`runtime/mcp`。派工脚本改为显式 `-CrewSession/-CrewExecution`。 |
| CLI | `crew` 只剩主机命令；业务命令由清单生成（`crew <领域> <动作>`），调用者固定 `human`，经后台 `/api/op`。`crew join` 等 AI 子命令删除。 |

**真实模型实测**（隔离 HOME、随机端口、只复制登录凭据；`claude-haiku-5-5`、`gpt-6-luna` low；任务：whoami → 交流区发言 → 登记人工待办 → `crew_search` 找“留交接” → 调用找到的操作）：

1. CC 首跑失败：默认权限模式下 CC 把 `mcp__crew__*` 当未授权直接拒绝（stream-json 无人批准）。修：启动加 `--allowedTools mcp__crew`（Crew 自己在后台鉴权与确认）。复跑全部成功；`crew_search` 后服务端发 `list_changed`，CC 立即用 ToolSearch 加载 `handoff_add` 并调用。
2. Codex 首跑：`crew_call` 因标了 `destructiveHint` 被 Codex 发 `mcpServer/elicitation/request` 原生审批而失败（§12 未验证项之一的答案：会发）。修：`crew_call` 标非破坏性；真正的高风险由 Crew 确认请求把关。
3. Codex 二跑：Codex 在 `thread/start` 期间就连 MCP，此时进程尚未登记，存活检查误判 401，整轮没有 Crew 工具。修：令牌“启动中”状态（见上）。三跑全部成功（`crew_call(op="handoff.add")`）。

4. 全局助手（同一隔离方式，用户消息“帮我在想法墙记下一条想法……”）：首跑 Haiku 猜错字段名（想法操作的参数当时只写了“任意对象”）被拒，又沿用同一请求号改参数重试被判“请求号复用”，第三次成功（只记一条，没有重复）。修：想法 / 标签操作的参数改由处理函数自己的字段表 `personal._NOTE_FIELDS` 生成精确结构；`request_id` 说明写明“参数被拒后改参数重试要换新编号”。复跑 Haiku 与 luna low 都一次成功，`message_id` 取自 Crew 注入的用户原话编号。

**后端测试迁移发现并修复的回归**（约 110 个测试文件改走 `/api/op` / `ai_call`）：GET 读操作的处理函数拿不到查询串；`message.send` 查收件人时没有连接；leader 在自己的任务执行里被当成执行而不能派工、交接；目标页经 agent 路由的操作未固定为用户身份；人和设备的正文不得冒用 AI 身份（执行器统一检查，原先只拦远程）；令牌在会话换载体或换工作区后仍有效；助手读标签用了只读连接；`session.compact`、`group.close`、`trust.get`、资源 / 借用的强制归还对 leader 或用户缺失；`resource.add` 的正则错误未转成参数错误；任务卡不能清空字段、`whoami` 缺任务卡；交流区原文读取缺 `raw`；几处内部调用（音效播放地址、后台复用探测、自动启动探测）仍走旧地址。复核删除清单时另发现：旧 `crew status` 文本会隐藏已收起的组、已归档和离线超过一天的未分组会话，`workspace.state` 原样全返回，长期使用会撑大 AI 上下文；已把同样的过滤搬进操作（返回 `hidden_ended_sessions` 计数）并加测试。按设计删除、只改测试的三项：未收编的外部会话发合并申请、`crew init` 在普通子目录复用工作区、摘要会话拦截动态工具调用。

**A/B 小样本复测**（`crew web --mcp-exposure core|all` 显式切换；232 个真实操作，leader 可用 72 个；每条意图新开 leader，意图不点名工具且都需要非核心操作：给所有人留交接、声明 docs/ 归属、登记共享资源；脚本 `Temp/r5/trial/run_ab.py`）：

| 载体 · 模式 | 完成 | 平均耗时 | 调用路径 | 本轮输入 token（含缓存读） |
| --- | --- | --- | --- | --- |
| CC Haiku 5.5 · core | 3/3 | 11.8 s | 每次先 `crew_search`，再调加载的工具 | 13.5 万 / 14.8 万 / 39.1 万 |
| CC Haiku 5.5 · all | 3/3 | 10.2 s | 直接调用目标工具（ToolSearch 原生延迟加载） | 16.1 万 / 14.0 万 / 25.4 万 |
| Codex luna low · core | 3/3 | 13.5 s | `crew_search` → `crew_call` | 11.2 万 / 11.1 万 / 13.8 万 |
| Codex luna low · all | 3/3 | 15.4 s | 一次直接调用、一次先搜索、一次先查看板 | 23.0 万 / 14.0 万 / 7.5 万 |

结论：两种模式在这组意图上都 100% 完成、没有误选；all 模式让 CC 少一步搜索，Codex 在 all 模式下路径和 token 波动更大。样本只有 12 次、单轮、无相似名称干扰与长会话 / 恢复 / 权限收紧场景，不足以推翻裁决 7。**建议**保持基线（core）为默认并删除 all 模式；all 模式代码目前仍在（启动参数），等主会话记入决策日志后删除。

**与集成分支合并时的处理**（191d4e2 增量事件流与新手引导、ef6f6fd 跨 Leader 作战室）：
- 新操作 `workspace.snapshot`、`onboarding.get/set`、`runtimes.readiness`、`war_room.*` 补参数格式并登记设备范围；`/api/events` 归传输入口（§8.14）。前端请求统一经 `CrewAppData`（`app-data.js`），`app.js` 的 `api` / `queryPath` 委托给它。
- `war_room.*` 去掉 HTTP 绑定，leader 经 MCP 在自己的工作区调用、全局助手以用户身份带 project。作战室实测反馈写进工具参数：状态板 `id` 写明“已有项的编号，新增时不要带”；标题、事项、说明、契约、结论的长度限制写进 `maxLength` 与参数说明。守则的作战室一节移到 `prompts/roles/leader.md`，房内通知改为工具名。
- 工具指南与 `crew_search` 结果里“需附用户原话 message_id”只给全局助手（之前 leader 也看到）；`channel.post` 摘要由“以用户身份……”改为“在 Leader 交流区发言”。

**写入争用修复**（合并后真 AI 实测暴露）：事件流第 2 轮把用量扫描移到后台、启动即全量扫描，扫描在服务写锁下用一个写事务扫完全部会话记录（本机 572 个会话、约 31 万行写入、17 秒）。期间所有写入都要等它：
1. 经清单的写操作在回执（`op_requests`）处等 SQLite 忙等 5 秒后报 500。曾试过让回执排队等服务写锁，但这违反 `lock='none'` 操作不取写锁的约定（更新准备 / 取消等恢复路径在写入者卡住时也必须能走通），已撤回；以下面第 2 条的根因修复为准。
2. 聊天线程的投递回执、事件流记录不走服务写锁，同样超时；实测中 crew-chat 读取线程崩溃，会话离线——`usage.scan` 改为按整份会话记录分批提交（每批约 0.5 秒，与读取偏移同一事务），批间停 0.12 秒让忙等的写入者进来；后台扫描不再整段持锁，`usage_scan_lock` 保证同时只有一次扫描。中途出错只回滚当前批，下次扫描补齐且不重复计数。首次全量扫描 17.1 秒 → 20.9 秒，增量扫描不变（`test_a_long_first_scan_never_keeps_other_writers_past_the_busy_timeout`，修复前失败、修复后通过）。

**未做 / 未验证**：
- 完整 A/B（相似名称干扰、长会话、恢复、权限收紧后调用、高风险操作）未做。
- 确认请求只在助手页显示，工作区页与全局导航还没有入口。
- Codex 三个工具过滤配置项、长会话里的延迟加载仍未测；CC 长会话里 `list_changed` 加载在真实会话中只测了一轮。
- `rule_reads` 表随功能删除后不再写入，表结构保留未做迁移删除。
- 单份特别大的会话记录仍在一个事务里扫完（分批只在文件之间切分）。
