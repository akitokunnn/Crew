# 巨石拆分边界（阶段二施工依据）

> 日期：2026-11-27 · 作者：架构重审主题会话（Claude）· 基线：`claude/spatial-studio-ui` `3fdfbec`
> 上级文档：[总计划](REBUILD_PLAN_2026-11-27.md) 阶段二、[交接文档](HANDOFF_2026-11-27.md) 第 3 节契约、[架构重审](ARCHITECTURE_REVIEW.md)
> 本文是 `web.py`、`chat.py`、`app.js` 三个“纯搬移、不改行为”施工任务书的直接依据。行号以基线为起点，施工时自行复核。

---

## 0. 总则（四个任务书都必须原样带上）

1. **只搬不改。** 函数体、方法体、装饰器、默认参数、异常类型、返回值逐字不变。唯一允许的机械改动在各节“允许的改动”里写死，其余一律不许（包括顺手改名、补类型、删“看起来没用”的分支、合并重复代码、改 import 风格）。
2. **对外名字不丢。** 搬走后，原模块上所有被外部引用的名字（见各节“必须保留的名字”）仍可用 `web.X` / `chat.X` 访问（直接定义或 `from .x import X` 再导出）。
3. **测试只改装载方式，不改断言。** 允许改的只有：`patch('crew.chat.X')` 之类补丁目标（列在交付里，逐条写旧目标→新目标及原因）、JS 测试读取源码的方式（统一改用本文给出的辅助函数）。任何 `assert*` 行一字不动；需要改断言即说明搬移改变了行为，停工上报。
4. **一次只拆一个文件，一步一提交。** 每步跑完本节的验证命令且失败差集为空，主会话验收后才开下一步。
5. **不写防御性代码**（codex-dispatch §5 原文照抄进任务书）；不引入依赖；不新增环境变量；不动 `desktop/`、`docs/`（除本任务书要求更新的契约说明）、正式数据。
6. **循环导入**：新模块不得在顶层 `import` 被拆的原模块（`web` / `chat`）。原来写在函数体内的延迟 import 保持在原位置。

### 0.1 基线与通用验证命令

在仓库根目录、PowerShell 中执行。测试进程必须清掉会话身份变量。

```powershell
Remove-Item Env:CREW_SESSION_ID,Env:CLAUDE_CODE_SESSION_ID,Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
# Python 全量（web/chat 分别被 54 / 46 个测试模块直接导入，拆这两个文件时“相关测试”就是全量）
Push-Location plugin; python -m unittest discover -s tests 2>&1 | Tee-Object ..\Temp\split\py_after.log; Pop-Location
# 前端 VM 单测（基线 283 通过 / 0 失败）
node --test plugin/tests/test_*.js 2>&1 | Tee-Object Temp\split\js_after.log
# 语法
python -m compileall -q plugin/crew
```

- 改前先在同一 worktree 跑一次得到 `py_before.log` / `js_before.log`；交付里给出改前、改后失败用例集合与差集（必须为空）。
- 基线已知失败：`test_web_contract.FrontendChannelContractTests` 4 条 `CrewBrowser is not defined`（交接文档 3.1-3）。本会话在 `3fdfbec` 实测的全量结果记在 §5。

### 0.2 纯搬移核对脚本（主会话验收时也跑）

两个脚本放在各自 worktree 的 `Temp/split/`（被忽略）。脚本全文见附录 A、B，复制即可用。

- `same_bodies.py <rev>:<旧文件> <新文件...>`：旧文件里每个函数 / 方法，必须在新文件集合里找到**同名且 AST（含装饰器）完全一致**的定义。用于 W1、C1。
- `same_route_blocks.py <rev> <新文件...>`：旧 `Service.post/_post`、`Handler._dispatch` 里每个按 `path` 判断的分支体，改名（Service 中 `self→service`，Handler 中 `self→handler`）后必须原样出现在新文件某个函数体内。用于 W2。

两个脚本对基线自检：`same_bodies.py` 对 web.py / chat.py 各 93 个定义 0 不一致；`same_route_blocks.py` 共识别 154 个路由分支。

---

## 1. `plugin/crew/web.py`（2665 行）

### 1.1 现状分区（基线行号）

| 行 | 内容 | 去向 |
| --- | --- | --- |
| 1–35 | import | 留，按需裁剪 |
| 37–56 | `_text`、`_optional_text`、`_vscode_state_db` | `_text/_optional_text` → `web_api/common.py`（web.py 再导出）；`_vscode_state_db` 留 |
| 60–62 | `MODEL_WORK` | 留 |
| 65–140 | `Service.__init__`、`_remote_listener`、`now` | 留（组装） |
| 142–301 | 后台轮询：`storage_active_keys`、`refresh_agents`、`_poll_agents`、`digest_channels`、`_refresh_vscode`、`_read_vscode`、`start`、`close`、`_poll_notices` | `web_service/workers.py` |
| 297–420 | 更新与连接：`release_abandoned_update`、`update_reasons`、`prepare_update`、`cancel_update`、`connection_plan`、`prepare_connection` | `web_service/updates.py` |
| 421–552 | `_poll_git_ci`、`deliver_notices`、`settle_channel_mentions`、`deliver_git_results` | `web_service/workers.py` |
| 553–645 | `selected_project`、`candidates`、`candidate`、`adopt`、`_adopt` | `web_service/roster.py` |
| 646–935 | 快照：`state`、`member_execution_records`、`_execution_dispatch_settings`、`_conversation_dispatch`、`_native_context_history`、`_project_conversations`、`managed_agents`、`limits`、`_claude_limits`、`_budget`、`decision_participants` | `web_service/snapshot.py` |
| 936–997 | Git：`_git_branches`、`git`、`git_settings`、`git_commit` | `web_service/git.py` |
| 998–1121 | 设置与工作区：`settings_state`、`workspace_state`、`update_workspace`、`maintenance_post`、`storage_post`、`native_approvals`、`resolve_native_approval`、`_init_project` | `web_service/workspace.py` |
| 1122–1732 | `post`、`_post`（600 行 if 链） | W2 拆为路由表，见 1.4 |
| 1733–1800 | `archive_session`、`delete_session`、`_assert_deletable`、`stop_archived` | `web_service/sessions.py` |
| 1801–1943 | `chat_project`、`chat_session`、`chat_history`、`chat_skills`、`channel_messages`、`document_list`、`resolve_file`、`read_file`、`session_avatar`、`chat_file` | `web_service/reads.py` |
| 1944–1994 | `scan_usage`、`notification_status`、`set_away`、`usage` | `web_service/usage.py` |
| 1995–2027 | `POST_PATHS` | 留 |
| 2028–2101 | `STATIC`、`STATIC_CACHED` | 留（交接文档 3.1-2 契约：新静态文件仍登记在 `web.py` 的 `STATIC`） |
| 2102–2510 | `Handler`（`_dispatch` 265 行 if 链） | 传输层留在 web.py；GET 分支 W2 拆为路由表 |
| 2511–2665 | 端口常量、`Server`、`create_server`、`project_url`、`serve`、`_open_registered_service`、`_serve_locked` | 留 |

### 1.2 必须保留在 `web` 上的名字

测试与其他模块经 `web.X` 访问或打补丁：`Service`、`Handler`、`Server`、`create_server`、`serve`、`_serve_locked`、`_vscode_state_db`、`POST_PATHS`、`STATIC`、`MODEL_WORK`、`FETCH_BLOCKED_PORTS`、`MAX_BROWSER_PORT_ATTEMPTS`、`ThreadingHTTPServer`、`build_opener`（`_open_registered_service` / `_serve_locked` 留在 web.py 即自然满足），以及测试通过 `web.<模块>` 访问的模块对象：`json`、`sys`、`os`、`subprocess`、`webbrowser`、`traceback`、`usage`、`store`、`hooks`、`connections`、`session_ops`、`board`、`Path`。

补丁说明：`patch('crew.web.subprocess.Popen')`、`crew.web.os.startfile`、`crew.web.sys.platform`、`crew.web.store.connect`、`crew.web.usage.scan`、`crew.web.session_ops.archive`、`crew.web.hooks.git_details`、`crew.web.board.register_session`、`crew.web.traceback.print_exc`、`crew.web.Path.home` 改的都是**被引用模块 / 类本身的属性**，代码搬到哪里都生效，前提是新代码仍按 `subprocess.Popen(...)`、`store.connect(...)` 这种“模块.属性”的写法调用（原样搬移即满足）。`patch.object(web.Service, '_poll_notices' …)` 等作用在类上，mixin 方法经 MRO 同样被覆盖。

### 1.3 第一步 W1：`Service` 方法搬进 mixin（零语义风险）

新建包 `plugin/crew/web_service/`（`__init__.py` 为空）。每个模块一个 mixin 类，方法体逐字搬入：

```python
# plugin/crew/web_service/workers.py
"""Service 的后台轮询与投递线程。由 web.Service 组合，不单独实例化。"""
import threading, ...            # 只导入被搬方法实际用到的模块，写法与 web.py 原 import 相同
from .. import board, store, ...

class WorkersMixin:
    def storage_active_keys(self): ...   # 原文
```

`web.py` 中：

```python
from .web_service.workers import WorkersMixin
...
class Service(WorkersMixin, UpdatesMixin, RosterMixin, SnapshotMixin, GitMixin,
              WorkspaceMixin, SessionsMixin, ReadsMixin, UsageMixin):
    def __init__(...): ...   # 原文
    def _remote_listener(...): ...
    def now(self): ...
    def post(...): ...       # W1 不动
    def _post(...): ...      # W1 不动
```

- **允许的改动**：新增 mixin 模块与类声明、`web.py` 中删除被搬方法并加 import 与基类列表；mixin 模块中按需写 import。
- mixin 方法里用到的模块级常量（如 `MODEL_WORK`、`STATIC`、`FETCH_BLOCKED_PORTS`）若只被搬走的方法使用，随方法搬走并在 web.py 再导出；若 web.py 留下的代码也用，留在 web.py，mixin 里在**方法体内**延迟导入会违背“逐字不变”，因此改为把该常量移到 `web_api/common.py`，两边从那里导入（交付中逐个列出）。
- **验证**：§0.1 全量 + `python Temp/split/same_bodies.py HEAD:plugin/crew/web.py plugin/crew/web.py plugin/crew/web_service/*.py` 输出 `0 mismatches`。
- **验收标准**：web.py ≤ 1500 行；`Service` 类体只剩 `__init__`、`_remote_listener`、`now`、`post`、`_post`。

### 1.4 第二步 W2：POST / GET 分支搬成按领域的路由表

目标：`_post` 与 `_dispatch` 的 if 链变成“查表 + 原样的阶段骨架”，分支体搬到 `plugin/crew/web_api/` 下按领域的函数里。路由表就是后续 R5 操作清单的挂载点（R5 只给表项加字段，不再挪代码）。

#### 1.4.1 现状语义（必须一比一保住）

`Service.post()`（1122–1137）不动：它决定**准入**（`MODEL_WORK`、`/api/goals/` 前缀等走 `self.chat.work()`）。`_post` 内部按代码位置分三个**阶段**，阶段决定加不加锁、何时解析项目：

| 阶段 | 基线位置 | 加锁与连接 | 分支（按出现顺序） |
| --- | --- | --- | --- |
| A 无锁 | 1140–1338，`with self.write_lock` 之前 | 无共享写锁；分支自己开连接 / 加锁 | `report_preview.WORKER_PATHS`；`/api/goals/agent`(action=report.preview)；`/api/remote-access`；`/api/behavior-prompts`；`/api/personal/notes`、`/tags`、`/notes/recovery`、`/notes/start`；`/api/assistant/open`、`/native-action`；**前缀** `/api/workspace-data/`；**前缀** `/api/storage/`；`/api/project/update`、`/remove`；**前缀** `/api/maintenance/`；`/api/admin/chat/send`、`/interrupt`、`/api/admin/config`；`/api/connections/configure`、`/action`、`/preferences`；`/api/member-defaults`；`/api/sounds/custom`、`/delete`；`/api/claude-budget`；`/api/settings`；`/api/session/runtime/prepare`、`/switch`；`/api/files/resolve`、`/open`、`/reveal`；`/api/goals/report/folder`；`/api/git/fetch`；`/api/git/adopt` |
| B 持锁、未解析项目 | 1339–1373 | `with self.write_lock, closing(store.connect(...)) as conn`，`now` 已算 | `/api/project/init`；`/api/host/workspace/existing`、`/new`；`/api/notifications/away`；**无返回的预处理**：正文项目一致性检查（1354 那一组路径，改写 `project_name`）；`/api/git/settings`；`/api/git/enable`、`/disable` |
| C 持锁、已解析项目 | 1374–1732 | 同上；1377–1380 按 `personal_paths` 选 `chat_project` 或 `selected_project` 得到 `project` | `/api/native-approval`；**前缀** `/api/goals/`；`/api/documents`、`/pin`、`/refresh`；`/api/trust`；`/api/decision/discuss`、`/ready`、`/clarify`；`/api/comments`；`/api/todo/resolve`、`/undo`；`/api/messages`；`/api/leaders/order`；`/api/domains`；`/api/project/theme`；`/api/channel`、`/read`、`/digest`；`/api/attention/dismiss`；**前缀** `/api/session/`；`/api/answer`；`/api/wake`；`/api/spawn`；`/api/leader`；`/api/chat/send`、`/interrupt`、`/read`；`/api/roster/move`、`/remove`；`/api/group/close`、`/undo`；`/api/roster/adopt`；`/api/session-library/adopt`；末尾兜底 `return {"ok": True}` |

顺序上的三处陷阱：`/api/goals/report/folder` 在 A 里先于 C 的 `/api/goals/` 前缀；`/api/session/runtime/*` 在 A 里先于 C 的 `/api/session/` 前缀；`/api/goals/agent` + `report.preview` 在 A 里用只读连接先截走。阶段顺序不变，这三处就自动保住。

#### 1.4.2 目标结构

```
plugin/crew/web_api/
  common.py        _text、_optional_text、UNHANDLED 哨兵（W1 已建）
  routes.py        阶段表的组装与查表函数（唯一知道三个阶段的地方）
  admin.py         remote-access、admin/*、connections/*、settings、claude-budget、sounds、member-defaults
  workspace.py     workspace-data/*、storage/*、project/update|remove|init、host/workspace/*、maintenance/*
  files.py         files/*、goals/report/folder、chat/file、文件读取类 GET
  git.py           git/*
  sessions.py      session/*、session/runtime/*、roster/*、group/*、session-library/*、answer、wake、spawn、leader、chat/*
  board.py         native-approval、documents*、trust、decision/*、comments、todo/*、messages、leaders/order、domains、project/theme、channel*、attention/dismiss、notifications/away
  personal.py      （已有）personal/*、assistant/* 的原有函数不动，新增本阶段搬入的分支函数
  behavior_prompts.py（已有，不动）
```

每个领域模块导出三张 POST 表和一张 GET 表，**键是完整路径**，值是函数：

```python
# 阶段 A：签名 (service, path, data, project_name)
POST_UNLOCKED = {'/api/remote-access': remote_access, ...}
POST_UNLOCKED_PREFIX = (('/api/workspace-data/', workspace_data), ...)   # 只有前缀路由用元组，保持原顺序
# 阶段 B：签名 (service, conn, path, data, project_name, now)
POST_LOCKED = {...}
# 阶段 C：签名 (service, conn, project, path, data, project_name, now)
POST_PROJECT = {...}
POST_PROJECT_PREFIX = (('/api/goals/', goals_prefix), ('/api/session/', session_prefix))
# GET：签名 (handler, service, url, query)
GET = {'/api/state': state, ...}
```

- 一个原分支对应一个函数，**参数名必须与原局部变量同名**（`service`、`conn`、`project`、`project_name`、`path`、`data`、`now`、`handler`、`url`、`query`），函数体就是原分支体，`self` 改为 `service`（GET 中 `self` 改为 `handler`，`service` 本就是局部变量）。
- 原来多路径共用一个 if 的（如 `in ('/api/files/resolve', '/api/files/open', '/api/files/reveal')`），搬成一个函数，表里登记多个键指向它；函数体内原有的 `if path == ...` 子分支原样保留。
- 原分支末尾“落空后走到 `_post` 结尾 `return {"ok": True}`”的（`/api/roster/move`、`/remove` 等），函数末尾补一句 `return {'ok': True}`——这是唯一允许新增的语句，交付中逐个列出。
- 阶段 B 的“正文项目一致性预处理”不是路由，原样留在 `routes.py` 的阶段 B 骨架里，在查 `POST_LOCKED` 之前执行；1377–1380 的 `project` 解析也原样留在骨架里。

`routes.py` 骨架（伪代码，`_post` 与 `_dispatch` 只调用它）：

```python
def post(service, path, data, project_name):
    handler = _lookup(POST_UNLOCKED, POST_UNLOCKED_PREFIX, path)
    if handler: return handler(service, path, data, project_name)
    with service.write_lock, closing(store.connect(service.home)) as conn:
        now = ...                                  # 原 1339 行
        handler = POST_LOCKED.get(path)
        if handler is None:                        # 预处理只在原位置之后的分支前执行
            ...原 1354–1358 行，原样...
        ...                                        # B 的其余分支按原顺序（见下）
        project = ...                              # 原 1377–1380 行
        handler = _lookup(POST_PROJECT, POST_PROJECT_PREFIX, path)
        if handler: return handler(service, conn, project, path, data, project_name, now)
        return {"ok": True}
```

阶段 B 内部顺序为 `init → host/workspace → away → 预处理 → git/settings → git/enable|disable`，预处理位于中间；施工时按此顺序写骨架，不得把预处理挪到最前或最后。`_lookup` 先查精确表、再按元组顺序做前缀匹配。

`Handler._dispatch` 中：`local_settings_path` 计算、POST 的读体 / 413 / 403 / `X-Crew` / `POST_PATHS` 404 检查、`/api/access/context`、`STATIC` 与 404 兜底**全部原样留在 web.py**；中间那串 `elif url.path == ...` 换成 `route = routes.GET.get(url.path)`。GET 中 `in (...)` 元组形式的分支照 POST 规则登记多键。

#### 1.4.3 W2 新增的覆盖测试（R5 清单测试的种子）

新增 `plugin/tests/test_route_tables.py`：

1. `POST_PATHS` 中每条路径都能被 `routes` 的阶段表（精确或前缀）命中；
2. 阶段表的每个精确键都在 `POST_PATHS` 里；
3. GET 表每个键都不在 `STATIC` 里，且与 POST 表的同名路径不冲突（同一路径 GET、POST 并存是允许的，测试只防同表重复登记）；
4. 同一路径在 A、B、C 中只出现一次。

#### 1.4.4 W2 验证

```powershell
python Temp/split/same_route_blocks.py HEAD plugin/crew/web.py (Get-ChildItem plugin/crew/web_api/*.py).FullName
```

必须 `0 mismatches`；再跑 §0.1 全量。验收标准：web.py ≤ 800 行，`_post` ≤ 20 行，`_dispatch` 中不再有业务分支。

---

## 2. `plugin/crew/chat.py`（2135 行）

### 2.1 现状分区与去向

| 行 | 内容 | 去向 |
| --- | --- | --- |
| 1–218 | import；常量（`PAUSED_FOR_UPDATE`、`MAINTENANCE_HELD`、`REPLY_KIND`、`NATIVE_*`、`IDLE_RECLAIM_MINUTES`、`IMAGE_TYPES`、`MAX_IMAGE_BYTES`）；函数 `idle_reclaim_seconds`、`track_background`、`native_output`、`has_native_output`、`lost_turn`、`clear_unstarted_member_bindings`、`compact_command`、`tool_label`、`validate_images`、`child_environment`；`UpdatePaused`、`_admitted` | `chat_base.py`（chat.py 全部再导出） |
| 219–499 | `Journal`、`Running` | `chat_journal.py`（chat.py 再导出；`messaging.py` 的 `from .chat import Journal` 不改） |
| 500–509 | `claude_executable` | **留在 chat.py**（见 2.2） |
| 510–592 | `Manager.__init__`、`work`、`paused`、`close_admission`、`open_admission`、`busy_reasons` | 留在 chat.py（组装 + 更新准入） |
| 593–752 | 查询：`managed_pids`、`path`、`journal`、`file`、`running`、`is_owned`、`owns_pid`、`busy`、`scope_busy`、`maintenance_held`、`activity`、`applied`、`context`、`display_context`、`context_state`、`history`、`subagent_tasks` | `chat_status.py` · `StatusMixin` |
| 753–951 | 原生输出与掉线：`_read`、`_replace_lost_native`、`_stderr`、`_external_agents`、`_runs_in_claude`、`_check_offline`、`missing_native` | `chat_native.py` · `NativeMixin` |
| 952–1172 | 启动：`_launch`、`_identity_keys`、`_claim_identity`、`_launch_native`、`start` | **留在 chat.py**（`_launch_native` 调 `claude_executable`） |
| 1173–1436 | 投递：`_send`、`prepare_runtime`、`switch_runtime`、`send_message`、`deliver_messages`、`_deliver_messages`、`send` | `chat_delivery.py` · `DeliveryMixin`（阶段三“消息可靠性”主改这里） |
| 1437–1548 | 压缩与维护：`compact`、`maintenance_launch`、`maintenance_compact`、`maintenance_release` | `chat_maintenance.py` · `MaintenanceMixin` |
| 1549–1569、1644–1798 | 续回：`_resume_spec`、`_resume`、`wake`、`auto_resume`、`_auto_resume` | `chat_resume.py` · `ResumeMixin` |
| 1570–1643 | 助手：`_personal_leader`、`_recover_personal_note`、`start_personal_note`、`personal_action` | `chat_personal.py` · `PersonalMixin`（阶段三“操作清单与助手”主改这里） |
| 1799–1896 | 控制通道：`_control`、`_control_response`、`sync_native_titles`、`set_options`、`interrupt` | `chat_control.py` · `ControlMixin` |
| 1897–2135 | 生命周期：`_idle_for`、`reclaim_idle`、`stop_managed`、`stop_identity`、`detach`、`close` | `chat_lifecycle.py` · `LifecycleMixin` |

结果：`class Manager(StatusMixin, NativeMixin, DeliveryMixin, MaintenanceMixin, ResumeMixin, PersonalMixin, ControlMixin, LifecycleMixin)`，chat.py 约 450 行。

### 2.2 必须保留的名字与补丁目标

- 经 `chat.X` 访问：`Manager`、`Journal`、`Running`、`UpdatePaused`、`claude_executable`、`child_environment`、`validate_images`、`tool_label`、`idle_reclaim_seconds`、`has_native_output`、`clear_unstarted_member_bindings`、`compact_command`、`IMAGE_TYPES`、`MAX_IMAGE_BYTES`、`MAINTENANCE_HELD`、`NATIVE_MISSING`、`IDLE_RECLAIM_MINUTES`，以及 `chat_base` 中全部公开名。
- `patch('crew.chat.claude_executable')` 共 14 处，并且 `channel_digest`、`goal_runtime`、`operations_chat`、`operations_worker` 都以 `chat.claude_executable()` 调用、依赖这个补丁点。所以 `claude_executable` 与唯一的内部调用者 `_launch_native` 都留在 chat.py，补丁目标不改。
- 其余补丁（`crew.chat.shutil.which`、`crew.chat.store.connect`、`crew.chat.hooks.git_details`、`crew.chat.runtime_switch.accepted`、`crew.chat.runtimes.set`、`crew.chat.codex_client.Client`）都是模块属性补丁，搬到 mixin 后仍生效，前提是 mixin 模块按原写法 `from . import store, hooks, ...` 并以 `store.connect(...)` 调用。
- 装饰器 `@_admitted` 修饰的 `start`（留）、`send`、`compact`、`start_personal_note`、`wake`：mixin 模块 `from .chat_base import _admitted`。

### 2.3 允许的改动与验证

- 允许：新建 `chat_base.py`、`chat_journal.py`、七个 mixin 模块；chat.py 删除被搬定义、加 import / 再导出与基类列表；mixin 模块写 import。
- `__init__` 中 `from .goal_runtime import Controller` 等延迟导入原位保留。
- 验证：`python Temp/split/same_bodies.py HEAD:plugin/crew/chat.py plugin/crew/chat.py plugin/crew/chat_*.py` 为 `0 mismatches`；§0.1 全量。
- 验收标准：chat.py ≤ 600 行；任何 `chat_*.py` 顶层不 `import` chat。

### 2.4 不在这一步做的事（写进任务书“不在范围”）

按载体（Claude / Codex）抽出适配接口、`_read` 里的载体分支收敛、`_send` 的投递状态机，都是行为改动，分别归阶段三“消息可靠性”和后续 Harness 接口，不在纯搬移里做。

---

## 3. `frontend/app.js`（1746 行）

### 3.1 约束

- app.js 是一个 `"use strict"; (() => { ... setup(); })();` 闭包，几十个 `let` 共享状态（`state`、`view`、`leaderId`、`goals`、`api`…）。
- 8 个 JS 测试（`test_runtime_ui`、`test_app_state_ui`、`test_chat_connection`、`test_codex_subagent_ui`、`test_documents_ui`、`test_git_runtime_ui`、`test_usage_ui`、`test_working_overview_ui`）读取 app.js **源码文本**，用 `source.replace("  setup();\n})();", 注入串)` 把闭包内的词法名字挂到 `window.ui`，并在注入串里**直接给闭包变量赋值**（`state=`、`api=`、`render=`、`quotas=`、`replace=`、`usage=`、`view=`、`goalId=`、`runtimesCache=` 等）。`test_runtime_ui` 还按 `'    document.addEventListener("click",'` 到 `'    document.addEventListener("input",'` 切出点击处理片段。
- 交接文档 3.1-3：VM 沙箱只伪造 `CrewTheme.logo`，不加载 `global-rail.js`，`state.projects` 可能为 `undefined`，侧栏 HTML 在无 Git 助手且计数为 0 时不得出现 `class="rail-badge"`。
- `STATIC` 白名单与 `remote_access.set_static_paths(STATIC)` 只认登记过的路径。

如果把 app.js 拆成各自独立的 IIFE 模块，被搬函数的函数体必须把自由变量改成 `ctx.state`、`ctx.api(...)`，测试注入的赋值也全部失效——这不是纯搬移，diff 也无法机械核对。所以阶段二分两步：**A1 文件切分（逐字节可证）**，**A2 按需改成真模块（行为等价改写，归后续主题会话）**。

### 3.2 A1：切成有序片段，服务端按清单拼接

- 新建目录 `frontend/app/`，把 app.js 闭包**内部**的代码按下表切成片段文件。切点只能落在闭包顶层语句之间（不得切开函数）；每个片段自身是合法 JS 语句序列（可单独 `node --check`）。
- 外壳 `"use strict";\n(() => {\n` 与 `})();\n` 不进片段，由拼接函数加。
- 新建 `frontend/app/parts.json`：片段文件名的有序数组，唯一顺序来源。
- `web.py` 的 `STATIC["/app.js"]` 改为指向一个拼接产物：新增一个小函数读 `parts.json`、按序读片段、加外壳后返回字节（Content-Type 不变，仍 `no-store`）。这是本步唯一的后端改动；`remote_access` 静态路径不变（仍是 `/app.js` 一条）。桌面 `extraResources` 已整目录复制 `../frontend`，`materializeBackend` 也复制子目录，无需改桌面。
- 新建 `plugin/tests/app_source.js`：`module.exports = () => 拼接后的源码（\r\n 归一为 \n）`。8 个测试里 `fs.readFileSync(.../frontend/app.js)` 一行替换为 `require("./app_source")()`，其余不动。
- 新建测试 `plugin/tests/test_app_parts.js`：`parts.json` 与 `frontend/app/*.js` 一一对应（无遗漏、无多余）；每个片段 `new vm.Script` 可解析；拼接结果以 `"use strict";\n(() => {` 开头、以 `  setup();\n})();\n` 结尾。
- Python 侧在 `test_web_contract` 已有的 `/app.js` 请求测试里，断言返回体等于 `app_source` 同算法的拼接结果（只加断言、不改原断言）。

片段划分（基线行号；每段写一行文件头注释说明职责，注释算在片段内，拼接后与原文的差异只有这些注释行——验收用下面的核对命令排除它们）：

| 片段 | 行 | 职责 | 后续归属 |
| --- | --- | --- | --- |
| `00-state.js` | 3–47 | 选择器、`esc`、访问权限、全部闭包状态变量、常量 | 共用 |
| `05-kit.js` | 48–92 | 图标 `paths`/`ic`、配色与头像、状态徽标、数字 / 时间格式化、`leader`/`member` 查找 | 共用（A2 首个可抽成真模块） |
| `10-runtime-options.js` | 93–181 | 载体 / 模型 / effort / 速度选项、`refreshRuntimes`、上下文环 `contextRing`/`contextControls`、`sessionOpen` | 载体相关 |
| `15-data.js` | 182–238 | `membersOf`、`groupWork`、`decisionsOf`、`todos*`、`queryPath`、`api`、`toast`、`replace`、`action`、`quotas` | **增量事件流会话**（A2 改造为数据层） |
| `20-usage-budget.js` | 239–261 | 额度预算卡、`crewQuota` | 用量 |
| `25-theme-settings.js` | 262–287 | 主题、字号、`renderSettings`、通知渠道 | 设置 |
| `30-sidebar.js` | 288–472 | 置顶、排序、信任标记、分支、侧栏 Leader 行、工作区浮层、`renderEntrySidebar`/`renderSidebar`、拖拽排序 | 侧栏 |
| `35-decisions-dispatch.js` | 473–603 | 拍板卡、Codex 派工卡、`memberNode`/`leaderNode`/`workingRow`、执行卡 | 拍板 / 任务 |
| `40-chat-view.js` | 604–764 | 右侧面板、注意力侧栏、`renderChat`、`chatAccess` | 对话视图 |
| `45-todos-channel-all.js` | 765–866 | 待办卡与处理、`renderChannel`、`renderAll`、会话库挂载 | 待办 / 交流区（消息可靠性会话改 `renderChannel`） |
| `50-usage-view.js` | 867–916 | 用量页 `renderUsage`/`loadUsage` | 用量 |
| `55-cards-menus.js` | 917–1021 | 焦点保持、人员卡、会话菜单与子菜单 | 菜单 |
| `60-dialogs.js` | 1022–1287 | 改名、Leader 外观、信任、停止确认、设置面板开关、压缩 / 删除、Leader 新建表单、预算 | 弹窗 |
| `65-route.js` | 1288–1402 | `syncRoute`/`navigate`/`readRoute`/`render`、架构图入口、`notifySounds`、`loadState`、`answer`、`changeSession` | 路由与状态加载（`loadState` 归事件流会话） |
| `70-setup.js` | 1403–1745 | `setup()`：全部 DOM 事件委托、`hashchange`、`setInterval(loadState, 3000)`、`setup();` 调用 | 事件绑定（A2 拆成按 `data-act` 注册的处理表） |

行号是建议切点，施工时以“完整顶层语句”为准微调，交付中给出最终行号表。

**A1 验证（逐字节）**：

```powershell
git show HEAD:frontend/app.js > Temp/split/app_before.js
node -e "process.stdout.write(require('./plugin/tests/app_source.js')())" > Temp/split/app_after.js
# 去掉片段头注释后应逐字节相同（统一换行后比较）
node -e "const fs=require('fs');const n=s=>s.replace(/\r\n/g,'\n');const a=n(fs.readFileSync('Temp/split/app_before.js','utf8'));const b=n(fs.readFileSync('Temp/split/app_after.js','utf8')).split('\n').filter(l=>!l.startsWith('  // [app-part] ')).join('\n');if(a!==b){console.error('DIFF');process.exit(1)}console.log('identical')"
```

片段头注释统一写成 `  // [app-part] <职责>` 一行，便于上面的命令剔除。另跑 §0.1 的 JS 全量与涉及 `/app.js` 的 Python 测试（`test_web_contract`、`test_shell`、`test_remote_access`、`test_remote_integration`、`test_remote_routes`、`test_storage_http`、`test_assistant_http`）。最后用隔离 home 起一次后端，浏览器打开工作区页确认控制台无错（`prove-it` 截一张图即可，UI 不变）。

### 3.3 A2：真模块化（不在阶段二纯搬移内，供后续会话照做）

- 规则：一次只把**一个**片段改成独立 IIFE `window.CrewAppX = { create(ctx) }`，在 `index.html` 中位于 `app.js` 之前加载并登记 `STATIC` 与 `test_assistant_http.test_new_page_assets_are_served_by_the_actual_backend`；app.js 闭包里保留同名词法绑定（`const {renderUsage, ...} = CrewAppX.create(ctx)`），使测试的 `window.ui = {...}` 继续可用。
- `ctx` 只用 getter 与晚绑定包装暴露外壳状态：`get state() { return state; }`、`api: (...a) => api(...a)`，这样测试对 `state=`、`api=` 的赋值仍被模块看到。
- 被测试直接赋值的变量（`usage`、`quotas`、`usageCollapsed` 等）若随片段迁出，相应测试的注入串改为调用模块暴露的 setter，并在交付中逐条列出——这一步属于行为等价改写，不再是纯搬移，按普通功能改动验收。
- 顺序建议：`05-kit`（无状态，零风险）→ `15-data` + `65-route` 中的 `loadState`（由增量事件流会话做，正好换成快照 + 序号订阅）→ `70-setup` 拆成 `data-act` 处理表 → 其余按改动需要逐个转。

---

## 4. 搬移顺序与任务书清单

| 步骤 | 任务书名（`Temp/tasks/`） | 执行 | 前置 | 核对 |
| --- | --- | --- | --- | --- |
| W1 | `web拆分第一步mixin_任务书.md` | Codex `gpt-6.2-astra` high | — | `same_bodies.py` + 全量 |
| W2 | `web拆分第二步路由表_任务书.md` | Codex `gpt-6.2-astra` xhigh | W1 合入 | `same_route_blocks.py` + 全量 + `test_route_tables.py` |
| C1 | `chat拆分mixin_任务书.md` | Codex `gpt-6.2-astra` high | W2 合入（两者都改 web/chat 的 import，串行避免冲突） | `same_bodies.py` + 全量 |
| A1 | `app.js切片_任务书.md` | Codex `gpt-6.2-sol` high | C1 合入（A1 改 `web.py` 的 `STATIC`） | 逐字节核对 + JS 全量 + 相关 Python + 一张截图 |

每份任务书模板：角色声明首行；“范围”逐条引用本文对应小节；“不在范围”写 §2.4 与 §3.3；编码要求照抄 §0；测试写“改前基线 → 改后 → 差集为空”；交付必须包含：最终文件 / 行号表、核对脚本原始输出、全量测试改前改后失败集合、所有补丁目标或测试装载改动的逐条清单。

阶段三会话的文件归属（建议，供主会话出“文件归属表”）：消息可靠性 → `chat_delivery.py`、`web_api/board.py` 中 channel / messages 相关函数、`frontend/app/45-todos-channel-all.js` 的 `renderChannel`、`channel.js`；操作清单与助手 → `web_api/routes.py`（加字段）、`chat_personal.py`、`cli.py`、`codex_tools.py`；增量事件流 → `web_service/snapshot.py`、`frontend/app/15-data.js`、`65-route.js`；想法思维导图 → `notes.js`、`architecture.js`、`web_api/personal.py`。

---

## 5. 基线实测记录（`3fdfbec`，本会话 2026-11-27）

- `node --test plugin/tests/test_*.js`：283 通过，0 失败，约 1 秒。
- Python 全量：见下方结果行（本会话后台跑完后补记）。
- `same_bodies.py` 自检：web.py 93 / chat.py 93 个定义，0 不一致。`same_route_blocks.py` 识别 154 个路由分支。

---

## 附录 A：`same_bodies.py`

```python
"""纯搬移核对：旧文件里每个函数/方法，在新文件集合中必须找到同名定义且 AST 完全一致。
用法: python same_bodies.py <git-rev>:<旧文件> <新文件1> [<新文件2> ...] [--rename=self:service]
"""
import ast, subprocess, sys

def defs(source, rename=None):
    out = {}
    tree = ast.parse(source)
    def walk(node, prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if rename:
                    for n in ast.walk(child):
                        if isinstance(n, ast.Name) and n.id == rename[0]: n.id = rename[1]
                        if isinstance(n, ast.arg) and n.arg == rename[0]: n.arg = rename[1]
                body = ast.dump(ast.Module(body=child.body, type_ignores=[]))
                out.setdefault(child.name, []).append((prefix + child.name, body, [ast.dump(d) for d in child.decorator_list]))
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + '.')
    walk(tree)
    return out

args = [a for a in sys.argv[1:] if not a.startswith('--rename')]
rename = next((tuple(a.split("=", 1)[1].split(":")) for a in sys.argv[1:] if a.startswith('--rename=')), None)
old = defs(subprocess.run(['git', 'show', args[0]], capture_output=True, text=True, encoding='utf-8', check=True).stdout)
new = {}
for path in args[1:]:
    for name, items in defs(open(path, encoding='utf-8').read()).items():
        new.setdefault(name, []).extend(items)
bad = 0
for name, items in old.items():
    for qual, body, deco in items:
        if not any(b == body and d == deco for _, b, d in new.get(name, [])):
            print('CHANGED_OR_MISSING', qual); bad += 1
print('checked', sum(len(v) for v in old.values()), 'definitions;', bad, 'mismatches')
sys.exit(1 if bad else 0)
```

## 附录 B：`same_route_blocks.py`

```python
"""W2 路由搬移核对：旧 Service._post / Service.post / Handler._dispatch 里每个以 path 判断的 if 分支体，
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
haystack = []
for path in sys.argv[2:]:
    for node in ast.walk(ast.parse(open(path, encoding='utf-8').read())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            haystack.append(seq(node.body))
text = '\n'.join(haystack)
bad = [name for name, body in blocks if body not in text]
for name in bad: print('CHANGED_OR_MISSING block at', name)
print('checked', len(blocks), 'route blocks;', len(bad), 'mismatches')
sys.exit(1 if bad else 0)
```

注：`Service.post()` 本身在 W2 中不搬（准入逻辑），它的分支体仍在 web.py，所以核对命令的新文件列表要包含 `plugin/crew/web.py`。
