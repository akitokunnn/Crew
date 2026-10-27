# 模块指南

按本次修改涉及的行查入口，无需整仓通读。术语见 [CONTEXT](../../CONTEXT.md)，架构取舍见 [ADR](../adr/0001-module-boundaries.md)。

## 职责与依赖

依赖自上而下：**桌面 / UI → HTTP 与应用组合 → 功能请求 → 领域 / 执行协调 → 存储与载体基础模块**。下层不反向导入上层入口。

| 要改什么 | 所有者 |
| --- | --- |
| 页面组合、导航、快照投影 | `web/app.js`；向功能传小范围能力，不维护功能内部状态。 |
| 动作登记 / 读取遮挡 / IME 安全替换 | `web/ui-actions.js` / `ui-visibility.js` / `ui-renderer.js`；功能登记并自行卸载。 |
| 对话面板开合、位置、遮挡、焦点与留白 | `web/conversation-workspace.js`、`right-panels.css`。 |
| 待处理内容与动作 | `web/attention.js`、`attention.css`。 |
| 压缩请求、订阅、排队、完成与失败 | `web/context-compaction.js`；实际执行结果来自后端。 |
| 评论 / 载体模型目录 / 创建对话 | `web/comments.js` / `runtime-catalog.js` / `leader-create.js`；自行拥有草稿、请求与清理。 |
| 人员操作 / leader 排序 | `web/people-ui.*` / `leader-order.*`；拥有操作确认、草稿和排序事务。 |
| 用量与额度 / 显示设置 | `web/usage-ui.js`、`usage.css` / `appearance-ui.js`；拥有缓存、筛选或偏好。 |
| 菜单 / 快速查找 / 提示音判定 | `web/ui-menu.js` / `command-palette.js` / `waiting-notifications.js`；分别拥有浮层交互、查找状态和通知策略。 |
| 目标内容 / 对话呈现 | `web/goal-ui.*` / `web/chat.js`；面板外层布局交给工作区模块。 |
| HTTP 安全、请求读取与响应 | `web.py`；不增加业务路径分支。 |
| 功能请求、校验与准入登记 | `web_api/`；`__init__.py` 组合能力，`registry.py` 定义接口。 |
| 工作区投影 / 后台投递 | `workspace_snapshot.py` / `notice_delivery.py`；不拥有原生协议。 |
| 远程通知 / 维护 | `notifications.py`、`connections.py` / `maintenance.py`；只接所需查询和执行端口。 |
| 目标 / 消息 / 人工答复规则 | `goals.py`、`goal_context.py`、`messaging.py`、`decision_actions.py`、`attention.py`、`todo.py` 等领域模块。 |
| 会话 / 目标执行协调 | `chat.py` / `goal_runtime.py`；维持明确的人员与执行作用域。 |
| 正式后台唯一性 | `service_lock.py`；入口在创建应用前持有 HOME 服务锁，不并行构造同 HOME 宿主。 |
| 共享执行状态 / 原生启动能力 | `execution_host.ExecutionStore` / `NativeLauncher`；单独拥有登记、历史和启动，不决定调度。 |
| 标准历史 / Claude 记录适配 | `session_journal.py` / `claude_events.py`；只有适配器依赖历史。 |
| 原生进程 / 更新准入 | `native_process.py` / `execution_admission.py`；不决定派工策略。 |
| Codex 原生协议 | `codex_runtime.py`、`codex_events.py`、`codex_client.py`。 |
| 数据 / 历史包 | `store.py`、`storage_service.py`、`storage_v2/`。 |
| 桌面连接、窗口和标签 | `desktop/src/core.cjs`、`launch-settings.cjs`、`tabs.cjs`；复用后端规则。 |

上表 Python 路径相对 `plugin/crew/`，前端路径相对 `plugin/crew/`；其他已有 `*-ui.js`、Git、通知、存储等模块继续拥有各自能力。

## 代码规范与新增功能

1. **先找所有者。** 规则、状态、错误和清理由对应模块集中拥有；不传整个上层对象，不靠 mixin、空壳转发或机械拆文件降低行数。函数与模板按语义换行，不为省行数压成难读的长行。
2. **UI 内容与布局分开。** 内容样式限定在功能根节点；位置、层级和留白归工作区模块。迁移后删去旧状态、事件分支与覆盖 CSS，只保留一份真相。
3. **前端接入。** 模块通过 `actions.register(owner, handlers)` 登记自己的 `data-act`，保留卸载函数；应用入口只组合快照和请求 / 刷新等窄端口。离页卸载渲染，作用域变化作废旧请求；销毁释放监听和登记，处理晚到回执。
4. **后端接入。** 功能在 `web_api/` 暴露 `register(...)`，用 `Registry.add(...)` 登记路径、处理函数和 `model_work / local_only / host_only`，由组合入口连接实际依赖；不修改 handler 的业务分支。
5. **业务可复用。** HTTP 只解析与映射，规则放领域模块供 CLI / 原生工具复用。事务、身份校验、准入与副作用先后次序是接口的一部分；真实错误和未知结果不能吞掉或自动重做。
6. **资源只有一个目录。** `web_assets.AssetCatalog` 发布选定后台 `crew/web/` 中受支持的 JS/CSS/SVG/ICO/WOFF2，并校验显式 HTML 入口。新增文件声明 HTML 顺序，重开隔离后台验收，无第二份 Python 白名单；HOME 与用户工作区不属于静态资源。
7. **测试接口。** 新模块直接测试公开行为，不用 `source.replace(...)` 暴露私有函数。关键旅程走真实 HTTP / DOM + 临时 HOME / 假载体，涵盖所涉载体、身份与作用域；结构检查不替代行为验收。
8. **提交前。** 同步受影响入口文档，检查依赖方向、资源引用和旧实现删除；基础接口变化跑完整回归，构建后比对包内源文件并复验。遵循根 AGENTS 的隔离、提交与合并规则。

接入样例直接看现有[前端用量模块](../../plugin/crew/web/usage-ui.js)、[HTTP 用量模块](../../plugin/crew/web_api/usage.py)及对应接口测试，不另维护一套示例代码。

## 调度与并发

保留现有调度规则。更新准入、成员开工与原生身份归属、目标生命周期、载体输入是不同职责，不得互相替代或复制 guard。并发范围、排队、优先级等策略需单独确认，再修改和验证。
