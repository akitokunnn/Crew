# 巨石拆分边界（阶段二施工依据）

> 日期：2026-11-27 · 作者：架构重审主题会话（Claude）· 基线：`claude/spatial-studio-ui` `3fdfbec`
> 上级文档：[总计划](REBUILD_PLAN_2026-11-27.md) 阶段二、[交接文档](HANDOFF_2026-11-27.md) 第 3 节契约、[交互清单](INTERACTION_INVENTORY.md)、[架构重审](ARCHITECTURE_REVIEW.md)
> 本文是 `web.py`、`chat.py`、`app.js` 拆分施工任务书的直接依据。行号以基线为起点，施工时自行复核。
> **第 2 版（同日）**：按 `gpt-6.2-astra` 只读评审修订（评审要点与取舍见架构重审附录 A）。主要变化：W1 的 mixin 改为与 `web.py` 同层的平铺模块（子包会让函数内相对导入失效）；W2 补全外层直接处理、条件分派与 B/C 边界，新增**先写后拆**的路由契约测试；C1 调整 `clear_unstarted_member_bindings` 归属；A1 由“服务端拼接”改为**暂缓**（备选“生成并提交 app.js”）；两个核对脚本升级。

---

## 0. 总则（每个任务书都必须原样带上）

1. **只搬不改。** 函数体、方法体、装饰器、参数与默认值、异常类型、返回值逐字不变。唯一允许的机械改动在各节“允许的改动”里写死，其余一律不许（包括顺手改名、补类型、删“看起来没用”的分支、合并重复代码、改 import 风格、裁掉“原文件不再使用”的 import）。
2. **对外名字不丢。** 搬走后，原模块上所有被外部引用或打补丁的名字（见各节“必须保留的名字”）仍可用 `web.X` / `chat.X` 访问；原模块已有的模块对象绑定（如 `web.subprocess`、`web.Path`）即使原文件已不再使用也必须保留。
3. **新代码访问名字的方式与原来相同。** 原来写 `from . import board` 再调 `board.register_session(...)` 的，新模块也这样写；不得改成 `from .board import register_session`（补丁行为不同）。
4. **测试只改装载方式，不改断言。** 允许改的只有补丁目标路径（交付中逐条列旧目标 → 新目标及原因）；任何 `assert*` 行一字不动。需要改断言即说明搬移改变了行为，停工上报。
5. **一次只拆一个文件，一步一提交。** 每步跑完验证命令且失败差集为空，主会话验收后才开下一步。
6. **不写防御性代码**（codex-dispatch §5 原文照抄进任务书）；不引入依赖；不新增环境变量；不动 `desktop/`、`docs/`（除任务书要求的契约说明）、正式数据。
7. **函数体内的延迟 import 保持原位、原文。** 为此 W1、C1 的新模块都放在 `plugin/crew/` 同层（不建子包），相对导入 `from .x import y` 的解析结果不变。

### 0.1 基线与验证命令

在仓库根目录、PowerShell 中执行。测试进程必须清掉会话身份变量。

```powershell
Remove-Item Env:CREW_SESSION_ID,Env:CLAUDE_CODE_SESSION_ID,Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
# Python 全量：web / chat 分别被 54 / 46 个测试模块直接导入，拆这两个文件时“相关测试”实际就是全量（约 14 分钟，放后台）
Push-Location plugin; python -m unittest discover -s tests 2>&1 | Tee-Object ..\Temp\split\py_after.log; Pop-Location
# 前端 VM 单测（基线 283 通过 / 0 失败）
node --test plugin/tests/test_*.js 2>&1 | Tee-Object Temp\split\js_after.log
python -m compileall -q plugin/crew
```

- 改前在同一 worktree 跑一次得到 `py_before.log` / `js_before.log`。交付给出改前、改后失败用例集合与差集（必须为空），**并逐条比较失败原因文本**（同名用例换了失败原因也算新问题）。
- 基线：`3fdfbec` 全量 2621 个用例，只有 `test_web_contract.FrontendChannelContractTests` 4 条失败（`CrewBrowser is not defined`，交接文档 3.1-3），详见 §5。
- 全量之外，每步按 [交互清单](INTERACTION_INVENTORY.md) 抽查受影响入口（任务书列出抽查条目标识）。

### 0.2 核对脚本（主会话验收时也跑）

两个脚本放在各自 worktree 的 `Temp/split/`（被忽略），全文见附录 A、B。新文件请**逐个列出**（PowerShell 用 `(Get-ChildItem plugin/crew/web_service_*.py).FullName` 展开，不要把 `*.py` 原样交给 Python）。

- `same_bodies.py <rev>:<旧文件> <新文件...>`：旧文件的每个函数 / 方法必须在新文件集合里恰好找到一个同名定义，**参数（含默认值）、装饰器、返回注解、函数体** AST 全同；并检查新文件里每个相对导入都能解析到真实模块。用于 W1、C1。
- `same_route_blocks.py <rev> <新文件...>`：旧 `Service.post/_post`、`Handler._dispatch` 里每个按 `path` 判断的分支体，在新文件中（Service / Handler 类内 `self` 与旧树同样改名为 `service` / `handler`）必须原样出现。**这只是必要条件**：它查不出“外层条件被改、优先级被调换、锁被换、分支被放进不可达函数”。充分性由 W2 的路由契约测试（§1.4.4）保证。

自检：`same_bodies.py` 对 web.py / chat.py 各 93 个定义 0 问题，且能识别“搬进子包后 `from .operations` 无法解析”；`same_route_blocks.py` 对基线 154 个路由分支 0 不一致。脚本不能检测 mixin 顺序（MRO）错误，由全量测试覆盖。

---

## 1. `plugin/crew/web.py`（2665 行）

### 1.1 现状分区（基线行号）

| 行 | 内容 | 去向 |
| --- | --- | --- |
| 1–35 | import | 留，**一个都不删**（总则 2） |
| 37–47 | `_text`、`_optional_text` | 移到 `web_api/common.py`；web.py 与各新模块显式 `from .web_api.common import _text, _optional_text` |
| 48–56 | `_vscode_state_db` | 留（`__init__` 调用，测试经 `web._vscode_state_db` 访问） |
| 60–62 | `MODEL_WORK` | 留（`post` 使用） |
| 65–140 | `Service.__init__`、`_remote_listener`、`now` | 留 |
| 142–296、421–552 | 后台轮询与投递：`storage_active_keys`、`refresh_agents`、`_poll_agents`、`digest_channels`、`_refresh_vscode`、`_read_vscode`、`start`、`close`、`_poll_notices`、`_poll_git_ci`、`deliver_notices`、`settle_channel_mentions`、`deliver_git_results` | `web_service_workers.py` · `WorkersMixin` |
| 297–420 | 更新与连接：`release_abandoned_update`、`update_reasons`、`prepare_update`、`cancel_update`、`connection_plan`、`prepare_connection` | `web_service_updates.py` · `UpdatesMixin` |
| 553–645 | `selected_project`、`candidates`、`candidate`、`adopt`、`_adopt` | `web_service_roster.py` · `RosterMixin` |
| 646–935 | 快照：`state`、`member_execution_records`、`_execution_dispatch_settings`、`_conversation_dispatch`、`_native_context_history`、`_project_conversations`、`managed_agents`、`limits`、`_claude_limits`、`_budget`、`decision_participants` | `web_service_snapshot.py` · `SnapshotMixin` |
| 936–997 | Git：`_git_branches`、`git`、`git_settings`、`git_commit` | `web_service_git.py` · `GitMixin` |
| 998–1121 | 设置与工作区：`settings_state`、`workspace_state`、`update_workspace`、`maintenance_post`、`storage_post`、`native_approvals`、`resolve_native_approval`、`_init_project` | `web_service_workspace.py` · `WorkspaceMixin` |
| 1122–1732 | `post`、`_post` | W2，见 1.4 |
| 1733–1800 | `archive_session`、`delete_session`、`_assert_deletable`、`stop_archived` | `web_service_sessions.py` · `SessionsMixin` |
| 1801–1943 | `chat_project`、`chat_session`、`chat_history`、`chat_skills`、`channel_messages`、`document_list`、`resolve_file`、`read_file`、`session_avatar`、`chat_file` | `web_service_reads.py` · `ReadsMixin` |
| 1944–1994 | `scan_usage`、`notification_status`、`set_away`、`usage` | `web_service_usage.py` · `UsageMixin` |
| 1995–2101 | `POST_PATHS`、`STATIC`、`STATIC_CACHED` | 留（交接文档 3.1-2：新静态文件仍登记在 `web.py` 的 `STATIC`） |
| 2102–2510 | `Handler` | 传输层留；GET 业务分支 W2 拆出 |
| 2511–2665 | 端口常量、`Server`、`create_server`、`project_url`、`serve`、`_open_registered_service`、`_serve_locked` | 留 |

### 1.2 必须保留在 `web` 上的名字

经 `web.X` 访问或打补丁：`Service`、`Handler`、`Server`、`create_server`、`serve`、`_serve_locked`、`_vscode_state_db`、`POST_PATHS`、`STATIC`、`STATIC_CACHED`、`MODEL_WORK`、`FETCH_BLOCKED_PORTS`、`MAX_BROWSER_PORT_ATTEMPTS`、`ThreadingHTTPServer`、`build_opener`，以及模块对象绑定 `json`、`sys`、`os`、`subprocess`、`webbrowser`、`traceback`、`usage`、`store`、`hooks`、`connections`、`session_ops`、`board`、`Path`。

补丁说明：`patch('crew.web.subprocess.Popen')`、`crew.web.os.startfile`、`crew.web.sys.platform`、`crew.web.store.connect`、`crew.web.usage.scan`、`crew.web.session_ops.archive`、`crew.web.hooks.git_details`、`crew.web.board.register_session`、`crew.web.traceback.print_exc`、`crew.web.Path.home` 改的都是**被引用模块 / 类本身的属性**，代码搬到哪里都生效，前提是新代码仍按“模块.属性”调用（总则 3）。`patch.object(web.Service, '_poll_notices' …)` 作用在具体类上，经 MRO 覆盖 mixin 方法。

### 1.3 第一步 W1：`Service` 方法搬进同层 mixin 模块（零语义风险）

```python
# plugin/crew/web_service_workers.py
"""web.Service 的后台轮询与投递线程。由 web.Service 组合，不单独实例化。"""
import threading, time, ...                 # 只写被搬方法实际用到的，写法与 web.py 原 import 一致
from . import board, store, notifications, ...
from .web_api.common import _text           # 被搬方法用到时

class WorkersMixin:
    def storage_active_keys(self): ...      # 原文，一字不改
```

```python
# web.py
from .web_api.common import _text, _optional_text
from .web_service_workers import WorkersMixin
...
class Service(WorkersMixin, UpdatesMixin, RosterMixin, SnapshotMixin, GitMixin,
              WorkspaceMixin, SessionsMixin, ReadsMixin, UsageMixin):
    def __init__(...): ...            # 原文
    def _remote_listener(...): ...
    def now(self): ...
    def post(...): ...                # W1 不动
    def _post(...): ...               # W1 不动
```

- **允许的改动**：新建上表 9 个模块与 `web_api/common.py`；web.py 删除被搬方法与 `_text/_optional_text` 定义，加 import 与基类列表；新模块写 import。
- **名字归属（自检结果，AST 扫描）**：被搬方法引用的 web.py 模块级函数只有 `_text`（`_init_project`、`cancel_update`、`maintenance_post`、`resolve_file`、`storage_post`、`update_workspace`）与 `_optional_text`（`storage_post`；它自身也调 `_text`），两者一起进 `web_api/common.py`。`STATIC`、`STATIC_CACHED`、`MODEL_WORK`、端口常量、`_vscode_state_db` 只被留下的代码使用，全部留在 web.py。
- **函数内延迟导入**：`update_reasons`（`web.py:341`）与 `connection_plan`（`:384`）的 `from .operations import read_operations`、`_project_conversations`（`:874`）的 `from . import decision_briefs, decision_flow`，因新模块与 web.py 同层，原文不改即可解析。
- **验证**：§0.1 全量；`python Temp/split/same_bodies.py HEAD:plugin/crew/web.py plugin/crew/web.py plugin/crew/web_api/common.py <9 个新模块逐个列出>` 输出 `0 problems`。
- **验收标准**：web.py ≤ 1500 行；`Service` 类体只剩 `__init__`、`_remote_listener`、`now`、`post`、`_post`。

### 1.4 第二步 W2：POST / GET 业务分支搬成按领域的处理函数

目标：`_post` 与 `Handler._dispatch` 里的业务分支整块搬到 `plugin/crew/web_api/` 按领域的函数里，原函数只剩**与现在完全相同的控制流骨架**和查表。这一步为 R5 的操作清单准备好“一块分支 = 一个处理函数”，但**不**在这一步引入操作元数据（锁、准入、权限的声明化归 R5）。

#### 1.4.1 现状控制流（必须一比一保住）

**外层 `Service.post`（1122–1137），W2 不动：**

1. `/api/admin/update/prepare`、`/api/admin/update/cancel`、`/api/admin/connection/prepare` 直接返回，不进入 `_post`；
2. `/api/goals/agent` 且 `action == 'report.preview'` → 直接 `_post`（**不**经 `chat.work()` 准入）；
3. `MODEL_WORK`、`/api/goals/` 前缀与另 6 条路径 → `with self.chat.work(): _post(...)`；
4. 其余 → `_post(...)`。

**`_post` 内部三段（以基线为准，施工时复核行号）：**

| 段 | 位置 | 外层锁与连接 | 分支（按出现顺序） |
| --- | --- | --- | --- |
| A 外层无写锁 | 1140–1337 | 未持外层 `write_lock`；**各分支内部自己开连接、自己加锁**（如 `workspace-data` 先做 relocate 预处理再 `with self.write_lock`，`session/runtime/*` 先只读校验再另开一段锁），整块搬移、保留内部锁 | `report_preview.WORKER_PATHS`（集合）；**条件分支** `/api/goals/agent` 且 `action == 'report.preview'`（只读连接）；`/api/remote-access`；`/api/behavior-prompts`；`/api/personal/notes`、`/tags`、`/notes/recovery`、`/notes/start`；`/api/assistant/open`、`/native-action`；**前缀** `/api/workspace-data/`；**前缀** `/api/storage/`；`/api/project/update`、`/remove`；**前缀** `/api/maintenance/`；`/api/admin/chat/send`、`/interrupt`、`/api/admin/config`；`/api/connections/configure`、`/action`、`/preferences`；`/api/member-defaults`；`/api/sounds/custom`、`/delete`；`/api/claude-budget`；`/api/settings`；`/api/session/runtime/prepare`、`/switch`；`/api/files/resolve`、`/open`、`/reveal`；`/api/goals/report/folder`；`/api/git/fetch`；`/api/git/adopt` |
| B 持锁、未解析项目 | 1338–1376 | 1338 取 `write_lock` 并开连接，1339 算 `now` | `/api/project/init`；`/api/host/workspace/existing`、`/new`（块内定义的 `register()` 闭包捕获 `conn`、`now`，整块搬）；`/api/notifications/away`；**正文项目一致性检查**（1354–1358，无返回，**改写 `project_name`**，后续 C 依赖）；`/api/git/settings`；`/api/git/enable`、`/disable`（disable 的 `stop_archived` 与返回到 1376） |
| C 持锁、已解析项目 | 1377–1732 | 1377–1381 按 `personal_paths` 解析 `project` | `/api/native-approval`；**前缀** `/api/goals/`；`/api/documents`、`/pin`、`/refresh`；`/api/trust`；`/api/decision/discuss`、`/ready`、`/clarify`；`/api/comments`；`/api/todo/resolve`、`/undo`；`/api/messages`；`/api/leaders/order`；`/api/domains`；`/api/project/theme`；`/api/channel`、`/read`、`/digest`；`/api/attention/dismiss`；**前缀** `/api/session/`（块内先解析并校验 `target`，子分支共用）；`/api/answer`；`/api/wake`；`/api/spawn`；`/api/leader`；`/api/chat/send`、`/interrupt`、`/read`；`/api/roster/move`、`/remove`（**有条件**返回详细结果，否则落到末尾）；`/api/group/close`、`/undo`；`/api/roster/adopt`；`/api/session-library/adopt`；末尾 `return {"ok": True}` |

优先级陷阱：`/api/goals/report/folder`（A）先于 C 的 `/api/goals/` 前缀；`/api/session/runtime/*`（A）先于 C 的 `/api/session/` 前缀；`/api/goals/agent` 只有 `report.preview` 在 A 截走，其他 action 进 C 的 goals 前缀（持写锁）。

**`Handler._dispatch`**：`local_settings_path` 计算、POST 的读体 / 413 / 403 / `X-Crew` / `POST_PATHS` 404 检查、`/api/access/context`、`STATIC` 与 404 兜底留在原处；中间 `elif url.path == ...` 业务分支可搬出。注意 `/api/chat/history|events` 分支内用 `keep_blank_values=True` 重新解析 `query`（`web.py:2425`），文件下载、SSE、报告分支自己写 HTTP 响应——这些分支搬出后仍由处理函数调用 `handler._send` / `handler._stream_storage_file` / `handler._chat_events` 完成响应。

#### 1.4.2 目标结构

```
plugin/crew/web_api/
  common.py      _text、_optional_text（W1 已建）、UNHANDLED 哨兵
  routes.py      POST 三段骨架与 GET 查表（唯一知道段、顺序与条件分支的地方）
  admin.py       remote-access、admin/chat|config、connections/*、settings、claude-budget、sounds、member-defaults
  workspace.py   workspace-data/*、storage/*、project/update|remove|init、host/workspace/*、maintenance/*、notifications/away
  files.py       files/*、goals/report/folder，及文件类 GET
  git.py         git/*
  sessions.py    session/*、session/runtime/*、roster/*、group/*、session-library/*、answer、wake、spawn、leader、chat/*
  board.py       native-approval、documents*、trust、decision/*、comments、todo/*、messages、leaders/order、domains、project/theme、channel*、attention/dismiss
  personal.py    （已有）新增 personal/*、assistant/* 分支函数
  goals.py       goals/* 前缀块与 goals/agent 条件分支
```

- **提取单位是完整的 if 块**（含块内的嵌套 if、内部锁、闭包与回落），一个块一个函数。**参数名必须与原局部变量同名**（`service`、`conn`、`project`、`project_name`、`path`、`data`、`now`；GET 为 `handler`、`service`、`url`、`query`），函数体就是原块体，Service 块里 `self` 改为 `service`，Handler 块里 `self` 改为 `handler`——这是唯一的体内改动。
- 原块“未返回、落到 `_post` 末尾 `return {"ok": True}`”的情形（如 `/api/roster/move`、`/remove` 只带 `name` 时），处理函数末尾返回 `UNHANDLED`，由骨架落到原有的 `return {"ok": True}`；**不得**在处理函数里自行补 `return {'ok': True}`（会混淆“已处理”与“未匹配”）。
- 多路径共用一块的（如 `in ('/api/files/resolve', '/api/files/open', '/api/files/reveal')`），表里登记多个键指向同一函数。

`routes.py` 骨架（照抄现有顺序，查表只替换“连续的按路径分支”）：

```python
def post(service, path, data, project_name):
    # A 段：先按原顺序处理集合与条件分支，再查精确表与前缀表
    if path in report_preview.WORKER_PATHS:
        return report_worker(service, path, data, project_name)
    if path == '/api/goals/agent' and data.get('action') == 'report.preview':
        return goals_agent_report_preview(service, path, data, project_name)
    result = _lookup(POST_A, POST_A_PREFIX, service, path, data, project_name)
    if result is not UNHANDLED:
        return result
    with service.write_lock, closing(store.connect(service.home)) as conn:
        now = service.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")   # 原 1339 行
        result = _lookup(POST_B_EARLY, (), service, conn, path, data, project_name, now)  # init / host / away
        if result is not UNHANDLED:
            return result
        if path in BODY_PROJECT_PATHS:            # 原 1354–1358 行，原文
            body_project = _text(data, 'project')
            if project_name is not None and body_project != project_name:
                raise board.BoardError('请求正文和地址中的项目不一致')
            project_name = body_project
        result = _lookup(POST_B_LATE, (), service, conn, path, data, project_name, now)   # git/settings、git/enable|disable
        if result is not UNHANDLED:
            return result
        personal_paths = {...}                     # 原 1377–1381 行，原文
        project = (...)
        result = _lookup(POST_C, POST_C_PREFIX, service, conn, project, path, data, project_name, now)
        if result is not UNHANDLED:
            return result
        return {"ok": True}
```

`_lookup` 先查精确表，再按元组顺序做前缀匹配；表用 `_table([...])` 构造并在模块加载时检测重复键（不用字典字面量静默覆盖）。A 段精确键与 A 段前缀不重叠（已核对：没有以 `/api/workspace-data/`、`/api/storage/`、`/api/maintenance/` 开头的 A 段精确路径），所以“先精确后前缀”与原顺序等价。

`Handler._dispatch` 中把业务 `elif` 链替换为 `route = routes.GET.get(url.path)`，命中则 `route(self, service, url, query)`，其余前后分支原样。

#### 1.4.3 覆盖测试（R5 清单测试的种子）

新增 `plugin/tests/test_route_tables.py`：

1. `POST_PATHS` = 外层直接处理集合（上面第 1 项三条）∪ `WORKER_PATHS` ∪ `{'/api/goals/agent'}` ∪ 各段精确键 ∪ 被前缀覆盖的路径；多一条少一条都失败；
2. 每张表无重复键；同一路径不在 A、B、C 中重复出现（`/api/goals/agent` 的条件分支与 C 段 goals 前缀并存是唯一例外，测试显式列出）；
3. GET 表的键不在 `STATIC` 里。

#### 1.4.4 路由契约测试（**先在旧代码上写好并通过，再做 W2**）

新增 `plugin/tests/test_route_dispatch_contract.py`，用现有 HTTP 测试夹具（隔离 home），对每个例外取正例与反例，断言响应 / 异常与可观察的副作用；准入与加锁用间谍对象记录（`patch.object(service.chat, 'work', wraps=...)`、把 `service.write_lock` 换成记录 acquire 的包装锁）：

| 用例 | 正例 | 反例 |
| --- | --- | --- |
| goals/agent 条件分支 | `action=report.preview`：不进 `chat.work()`、不取外层写锁 | 其他 action：进 `chat.work()`、取写锁、走 goals 前缀 |
| 前缀优先级 | `/api/goals/report/folder` 不取外层写锁 | `/api/goals/action` 取写锁 |
| 前缀优先级 | `/api/session/runtime/prepare` 走 A 段 | `/api/session/rename` 走 C 段 session 前缀 |
| 外层直接处理 | `/api/admin/update/prepare` 不进 `_post` | — |
| 正文项目一致性 | `/api/channel` 正文项目与地址不一致 → 400 原文报错 | 一致 → 成功且后续使用正文项目 |
| 末尾兜底 | `/api/roster/move` 带 `session_id` → 返回 `before` 与 `session` | 只带 `name` → `{"ok": true}` |
| 白名单 | 未登记 POST 路径 → 404 | — |
| GET 自写响应 | `/api/files/download`、`/api/chat/events` 响应头与状态码不变 | 未知 GET → 404 |

W2 完成后这组测试必须原样通过（不改一行），这是“请求仍走到同一处理逻辑”的充分证据。

#### 1.4.5 W2 验证

`same_route_blocks.py`（必要条件）0 不一致 + `test_route_dispatch_contract.py` 与 `test_route_tables.py` 通过 + §0.1 全量。验收标准：web.py ≤ 800 行；`_post` 只调用 `routes.post`；`_dispatch` 中不再有业务分支。

---

## 2. `plugin/crew/chat.py`（2135 行）

### 2.1 现状分区与去向

新模块全部与 chat.py 同层（`plugin/crew/chat_*.py`），函数内相对导入（`.goal_runtime`、`.codex_runtime`、`.goal_context`、`.goal_messages`、`.goal_links`、`.personal_actions`）原文不改即可解析。

| 行 | 内容 | 去向 |
| --- | --- | --- |
| 1–88、131–218 | 常量（`PAUSED_FOR_UPDATE`、`MAINTENANCE_HELD`、`REPLY_KIND`、`CLAUDE_NO_CONVERSATION`、`NATIVE_*`、`IDLE_RECLAIM_MINUTES`、`IMAGE_TYPES`、`MAX_IMAGE_BYTES`）；函数 `idle_reclaim_seconds`、`track_background`、`native_output`、`has_native_output`、`lost_turn`、`compact_command`、`tool_label`、`validate_images`、`child_environment`；`UpdatePaused`、`_admitted` | `chat_base.py`（chat.py 全部再导出） |
| 89–130 | `clear_unstarted_member_bindings`（内部用 `Journal`） | `chat_journal.py`（与 `Journal` 同处，避免 `chat_base ↔ chat_journal` 互相依赖） |
| 219–499 | `Journal`、`Running` | `chat_journal.py`（从 `chat_base` 导入 `compact_command`、`tool_label`；chat.py 再导出；`messaging.py` 的 `from .chat import Journal` 不改） |
| 500–509 | `claude_executable` | **留在 chat.py**（见 2.2） |
| 510–592 | `Manager.__init__`、`work`、`paused`、`close_admission`、`open_admission`、`busy_reasons` | 留 |
| 593–752 | 查询：`managed_pids` … `subagent_tasks` | `chat_status.py` · `StatusMixin` |
| 753–951 | 原生输出与掉线：`_read`、`_replace_lost_native`、`_stderr`、`_external_agents`、`_runs_in_claude`、`_check_offline`、`missing_native` | `chat_native.py` · `NativeMixin` |
| 952–1172 | 启动：`_launch`、`_identity_keys`、`_claim_identity`、`_launch_native`、`start` | **留在 chat.py** |
| 1173–1436 | 投递：`_send`、`prepare_runtime`、`switch_runtime`、`send_message`、`deliver_messages`、`_deliver_messages`、`send` | `chat_delivery.py` · `DeliveryMixin` |
| 1437–1548 | `compact`、`maintenance_launch`、`maintenance_compact`、`maintenance_release` | `chat_maintenance.py` · `MaintenanceMixin` |
| 1549–1569、1644–1798 | `_resume_spec`、`_resume`、`wake`、`auto_resume`、`_auto_resume` | `chat_resume.py` · `ResumeMixin` |
| 1570–1643 | `_personal_leader`、`_recover_personal_note`、`start_personal_note`、`personal_action` | `chat_personal.py` · `PersonalMixin` |
| 1799–1896 | `_control`、`_control_response`、`sync_native_titles`、`set_options`、`interrupt` | `chat_control.py` · `ControlMixin` |
| 1897–2135 | `_idle_for`、`reclaim_idle`、`stop_managed`、`stop_identity`、`detach`、`close` | `chat_lifecycle.py` · `LifecycleMixin` |

结果：`class Manager(StatusMixin, NativeMixin, DeliveryMixin, MaintenanceMixin, ResumeMixin, PersonalMixin, ControlMixin, LifecycleMixin)`（8 个 mixin），chat.py 约 500 行。

### 2.2 显式名字绑定（写进任务书，不用通配导入）

| 模块 | 需要显式导入的名字 |
| --- | --- |
| `chat_status` | `Journal`（自 `chat_journal`）、`IMAGE_TYPES` |
| `chat_native` | `track_background`、`NATIVE_REPLACED`、`CLAUDE_NO_CONVERSATION`、`NATIVE_MISSING`、`lost_turn`、`NATIVE_LOST_INPUT`、`native_output` |
| `chat_delivery` | `_admitted`、`MAINTENANCE_HELD`、`IMAGE_TYPES`、`validate_images` |
| `chat_maintenance` | `_admitted`、`MAINTENANCE_HELD` |
| `chat_personal` | `_admitted` |
| `chat_resume` | `_admitted`、`REPLY_KIND` |
| `chat_lifecycle` | `idle_reclaim_seconds` |
| chat.py（留下的启动与准入） | `_admitted`、`UpdatePaused`、`PAUSED_FOR_UPDATE`、`MAINTENANCE_HELD`、`has_native_output`、`child_environment`、`Running`、`claude_executable`（本地定义） |

外部经 `chat.X` 访问的名字全部由 chat.py 再导出：`Manager`、`Journal`、`Running`、`UpdatePaused`、`claude_executable`、`child_environment`、`validate_images`、`tool_label`、`idle_reclaim_seconds`、`has_native_output`、`clear_unstarted_member_bindings`、`compact_command`、`IMAGE_TYPES`、`MAX_IMAGE_BYTES`、`MAINTENANCE_HELD`、`NATIVE_MISSING`、`IDLE_RECLAIM_MINUTES`，以及 `chat_base` 的其余公开名。

补丁：`patch('crew.chat.claude_executable')` 共 14 处，且 `channel_digest`、`goal_runtime`、`operations_chat`、`operations_worker` 都以 `chat.claude_executable()` 调用——`claude_executable` 与它在 Manager 内的唯一调用者 `_launch_native` 都留在 chat.py，补丁目标不改。经评审复核，没有第二个“按 `crew.chat.<函数名>` 打补丁、又被搬走代码调用”的函数；其余补丁（`shutil.which`、`store.connect`、`hooks.git_details`、`runtime_switch.accepted`、`runtimes.set`、`codex_client.Client`）都是模块属性，mixin 按原写法 `from . import store, ...` 即生效。

### 2.3 允许的改动与验证

- 允许：新建 `chat_base.py`、`chat_journal.py` 与 8 个 mixin 模块；chat.py 删除被搬定义、加 import / 再导出与基类列表；新模块写 import。
- 验证：`python Temp/split/same_bodies.py HEAD:plugin/crew/chat.py plugin/crew/chat.py <chat_*.py 逐个列出>` 为 `0 problems`；§0.1 全量。
- 验收标准：chat.py ≤ 600 行；任何 `chat_*.py` 顶层不 `import` chat；`chat_base` 不导入 `chat_journal`。

### 2.4 不在这一步做的事

按载体抽出适配接口、`_read` 里的载体分支收敛、`_send` 的投递状态机，都是行为改动，分别归阶段三“消息可靠性”和后续载体接口，不在纯搬移里做。

---

## 3. `frontend/app.js`（1746 行）：A1 暂缓

### 3.1 约束

- app.js 是一个 `"use strict"; (() => { ... setup(); })();` 闭包，几十个 `let` 共享状态。
- 8 个 JS 测试（`test_runtime_ui`、`test_app_state_ui`、`test_chat_connection`、`test_codex_subagent_ui`、`test_documents_ui`、`test_git_runtime_ui`、`test_usage_ui`、`test_working_overview_ui`）读源码文本，用 `source.replace("  setup();\n})();", 注入串)` 把闭包内名字挂出来并**直接给闭包变量赋值**；`test_runtime_ui` 还按 `document.addEventListener("click",` 到 `"input",` 切片（click 在 1492 行、input 在 1678 行）。
- Python 侧 `test_web_contract.py` 既请求 `/app.js`（`:882`），又两处直接读磁盘 `frontend/app.js`（`:906`、`:1200`）。
- 工作树是 CRLF（1746 个），Git blob 是 LF；静态处理固定解包 `(filename, content_type)` 后 `read_bytes()`（`web.py:2464–2469`）。
- 交接文档 3.1-3：VM 沙箱只伪造 `CrewTheme.logo`，不加载 `global-rail.js`，无 Git 助手且计数为 0 时侧栏不得出现 `class="rail-badge"`。

### 3.2 结论：阶段二不做物理切分，保留 `frontend/app.js`

第 1 版提出“切成有序片段、服务端按清单拼接”。原型验证过切点本身没问题（15 段都落在完整顶层语句上、可单独解析、拼回逐字节相同），但评审指出它把一个“仍然共享同一闭包”的文件，变成生产服务、Python 测试、Node 测试三方都要维护的拼接契约，还有 CRLF 与外壳字节、磁盘直读测试两处坑；而收益有限：阶段三三个会话要改的 `app.js` 区域（事件流改 `loadState` 与 `setInterval`、消息可靠性改 `renderChannel`、思维导图改 `renderArchitecture`）互不重叠，Git 对同一文件不同区域的修改本来就能自动合并。所以：

1. **阶段二不做 A1**，`frontend/app.js` 原样保留。阶段三文件归属表按“函数区域”分配 app.js（见 §4），合入冲突由主会话按顺序处理。
2. **真模块化（A2）在需要时由负责会话做**：第一个是增量事件流会话，把数据层（`api`、`queryPath`、`loadState`、轮询定时器、`notifySounds`）抽成独立 IIFE `window.CrewAppData`，按 3.3 的规则保住测试注入。
3. 若主会话仍要物理切分（例如阶段三冲突比预期多），用**备选：生成并提交 `app.js`**——片段放 `frontend/app/`，标准库脚本 `scripts/build_app_js.py` 按 `parts.json` 拼出并**提交** `frontend/app.js`（逐字节保留原外壳与换行），新增测试检查生成物未过期。生产服务、两类测试都不改；代价是编辑片段后要多跑一次生成，需改写“改完刷新即生效”的开发约定（交接文档 3.1-2）。不再推荐请求时拼接。

切点原型（供备选方案直接使用）：`00-state` 3–47、`05-kit` 48–92、`10-runtime-options` 93–181、`15-data` 182–238、`20-usage-budget` 239–261、`25-theme-settings` 262–287、`30-sidebar` 288–472、`35-decisions-dispatch` 473–603、`40-chat-view` 604–764、`45-todos-channel-all` 765–866、`50-usage-view` 867–916、`55-cards-menus` 917–1021、`60-dialogs` 1022–1287、`65-route` 1288–1402、`70-setup` 1403–1745。片段不加头注释（职责写在 `parts.json` 旁的说明里），避免改变字节与行号。

### 3.3 A2 规则（抽真模块时照做）

- 一次只抽一块，成为独立 IIFE `window.CrewAppX = { create(ctx) }`，在 `index.html` 中位于 `app.js` 之前加载，登记 `STATIC` 与 `test_assistant_http.test_new_page_assets_are_served_by_the_actual_backend`。
- app.js 闭包里保留同名词法绑定（`const {loadState, ...} = CrewAppData.create(ctx)`）；`ctx` 只用 getter 与晚绑定包装暴露外壳状态（`get state() { return state; }`、`api: (...a) => api(...a)`），使测试对 `state=`、`api=` 的赋值仍被模块看到。
- 测试直接赋值的变量若随模块迁出（如 `usage`、`quotas`、`usageCollapsed`、`loadState`），相应测试注入串改为调用模块暴露的设置函数，逐条列在交付里——这是行为等价改写，按普通功能改动验收（相关测试 + 交互清单对应条目 + `prove-it`）。
- 注意 `setInterval(loadState, 3000)` 在 `setup()` 里（`app.js:1743`），`loadState` 在路由区（`app.js:1364`），抽数据层时两处都要改。

---

## 4. 搬移顺序与任务书清单

| 步骤 | 任务书名（`Temp/tasks/`） | 执行 | 前置 | 核对 |
| --- | --- | --- | --- | --- |
| W1 | `web拆分第一步mixin_任务书.md` | Codex `gpt-6.2-astra` high | — | `same_bodies.py` + 全量 |
| W2a | `web路由契约测试_任务书.md` | Codex `gpt-6.2-astra` high | W1 合入 | 在**未拆**代码上新增 §1.4.4 契约测试与 §1.4.3 覆盖测试的“现状版”，全部通过 |
| W2b | `web拆分第二步路由表_任务书.md` | Codex `gpt-6.2-astra` xhigh | W2a 合入 | `same_route_blocks.py` + 契约测试不改一行通过 + 全量 |
| C1 | `chat拆分mixin_任务书.md` | Codex `gpt-6.2-astra` high | W2b 合入 | `same_bodies.py` + 全量 |
| A1 | 不做（§3.2） | — | — | — |

每份任务书：角色声明首行；“范围”逐条引用本文对应小节；“不在范围”写 §2.4、§3；编码要求照抄 §0；测试写“改前基线 → 改后 → 差集与失败原因均一致”；交付必须包含：最终文件 / 行号表、核对脚本原始输出、全量测试改前改后失败集合与原因、所有补丁目标或测试装载改动的逐条清单。

阶段三文件归属（建议，供主会话出“文件归属表”）：消息可靠性 → `chat_delivery.py`、`web_api/board.py` 的 channel / messages 函数、`channel.js`、`app.js` 的 `renderChannel`（约 823–833 行）；操作清单与助手 → `web_api/routes.py` 与各领域模块（加元数据）、`chat_personal.py`、`cli.py`、`codex_tools.py`；增量事件流 → `web_service_snapshot.py`、`web_service_usage.py`、`app.js` 数据层（182–238、1288–1402 中的 `loadState` / `notifySounds`、1743 的定时器，按 A2 抽成 `CrewAppData`）；想法思维导图 → `notes.js`、`architecture.js`、`web_api/personal.py`、`app.js` 的 `renderArchitecture`（约 1335–1345 行）。

---

## 5. 基线实测记录（`3fdfbec`，本会话 2026-11-27）

- `node --test plugin/tests/test_*.js`：283 通过，0 失败，约 1 秒。
- Python 全量（`python -m unittest discover -s tests`，已清会话身份变量）：`Ran 2621 tests in 843.993s`，`FAILED (failures=4, skipped=14)`；4 个失败全部是 `test_web_contract.FrontendChannelContractTests`（`test_channel_assets_navigation_and_shared_markdown_renderer`、`test_channel_input_ime_multiselect_send_and_clear`、`test_channel_paging_refresh_and_bottom_read`、`test_channel_send_fetches_posts_arriving_during_request`）。墙钟约 14 分钟。
- 核对脚本自检：见 §0.2。A1 切点原型：15 段可单独解析、拼回逐字节相同（统一换行后 184,617 字符）。

---

## 附录 A：`same_bodies.py`（第 2 版）

```python
"""纯搬移核对（第 2 版）：旧文件里每个函数 / 方法，在新文件集合中必须恰好找到一个同名定义，
且参数（含默认值）、装饰器、返回注解、函数体的 AST 完全一致；同时检查新文件里的相对导入都能解析到真实模块。
用法: python same_bodies.py <git-rev>:<旧文件> <新文件1> [<新文件2> ...]
说明: 新文件请逐个列出（PowerShell 用 (Get-ChildItem plugin/crew/web_service_*.py).FullName 展开）。
"""
import ast, os, subprocess, sys

def signature(fn):
    parts = [ast.dump(fn.args), ast.dump(ast.Module(body=fn.body, type_ignores=[])),
             repr([ast.dump(d) for d in fn.decorator_list]), ast.dump(fn.returns) if fn.returns else '']
    return '\n'.join(parts)

def defs(source):
    out = {}
    def walk(node, prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(child.name, []).append((prefix + child.name, signature(child)))
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + '.')
    walk(ast.parse(source))
    return out

def check_relative_imports(path):
    bad = []
    package_dir = os.path.dirname(os.path.abspath(path))
    for node in ast.walk(ast.parse(open(path, encoding='utf-8').read())):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            base = package_dir
            for _ in range(node.level - 1):
                base = os.path.dirname(base)
            targets = [node.module] if node.module else [a.name for a in node.names]
            for target in targets:
                candidate = os.path.join(base, *target.split('.'))
                if not (os.path.isfile(candidate + '.py') or os.path.isfile(os.path.join(candidate, '__init__.py'))):
                    # from . import name 也可能导入包内的名字（非模块），只在 module 形式时报错
                    if node.module or not os.path.isfile(os.path.join(base, '__init__.py')):
                        bad.append('%s:%d from %s%s' % (path, node.lineno, '.' * node.level, target))
    return bad

old = defs(subprocess.run(['git', 'show', sys.argv[1]], capture_output=True, text=True, encoding='utf-8', check=True).stdout)
new = {}
import_errors = []
for path in sys.argv[2:]:
    for name, items in defs(open(path, encoding='utf-8').read()).items():
        new.setdefault(name, []).extend((path + ':' + qual, sig) for qual, sig in items)
    import_errors += check_relative_imports(path)
bad = 0
for name, items in old.items():
    candidates = new.get(name, [])
    for qual, sig in items:
        same = [q for q, s in candidates if s == sig]
        if not same:
            print('CHANGED_OR_MISSING', qual); bad += 1
        elif len(same) > 1 and len(items) == 1:
            print('DUPLICATED', qual, '->', same); bad += 1
for line in import_errors:
    print('UNRESOLVED_RELATIVE_IMPORT', line); bad += 1
print('checked', sum(len(v) for v in old.values()), 'definitions;', bad, 'problems')
sys.exit(1 if bad else 0)
```

## 附录 B：`same_route_blocks.py`（第 2 版）

```python
"""W2 路由搬移核对（第 2 版，只是必要条件，不能单独证明路由等价，充分性由路由契约测试保证）：
旧 Service._post / Service.post / Handler._dispatch 里每个以 path 判断的 if 分支体，
在改名后（Service 里 self->service，Handler 里 self->handler）必须原样出现在新文件某个函数体内。
用法: python same_route_blocks.py <git-rev> <新文件1> [<新文件2> ...]
"""
import ast, subprocess, sys

OLD = 'plugin/crew/web.py'
SOURCES = {('Service', '_post'): 'service', ('Service', 'post'): 'service', ('Handler', '_dispatch'): 'handler'}

def renamed(node, old, new):
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == old: n.id = new
        if isinstance(n, ast.arg) and n.arg == old: n.arg = new
    return node

def seq(body):
    return ', '.join(ast.dump(s) for s in body)

def path_test(test):
    return any(isinstance(n, ast.Attribute) and n.attr == 'path' or isinstance(n, ast.Name) and n.id == 'path' for n in ast.walk(test))

tree = ast.parse(subprocess.run(['git', 'show', f'{sys.argv[1]}:{OLD}'], capture_output=True, text=True, encoding='utf-8', check=True).stdout)
blocks = []
for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
    for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
        target = SOURCES.get((cls.name, fn.name))
        if target is None: continue
        renamed(fn, 'self', target)
        for node in ast.walk(fn):
            if isinstance(node, ast.If) and path_test(node.test):
                blocks.append((f'{cls.name}.{fn.name}:{node.lineno}', seq(node.body)))
CLASS_RENAME = {'Service': 'service', 'Handler': 'handler'}
haystack = []
for path in sys.argv[2:]:
    new_tree = ast.parse(open(path, encoding='utf-8').read())
    for cls in [n for n in new_tree.body if isinstance(n, ast.ClassDef) and n.name in CLASS_RENAME]:
        renamed(cls, 'self', CLASS_RENAME[cls.name])   # 留在原类里的分支与旧树同样改名，避免误报
    for node in ast.walk(new_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            haystack.append(seq(node.body))
text = '\n'.join(haystack)
bad = [name for name, body in blocks if body not in text]
for name in bad: print('CHANGED_OR_MISSING block at', name)
print('checked', len(blocks), 'route blocks;', len(bad), 'mismatches')
sys.exit(1 if bad else 0)
```

用法注意：新文件列表包含留下的 `plugin/crew/web.py` 和全部 `plugin/crew/web_api/*.py`，逐个列出。
