# Crew 重构交互清单

基线为 2026-11-27 的 `claude/spatial-studio-ui` HEAD `4e48844`，桌面源码版本 `0.3.18`。依据为[总计划阶段一](REBUILD_PLAN_2026-11-27.md)和[交接文档第 4 节](HANDOFF_2026-11-27.md)。清单根据此 HEAD 的入口、状态分支和样式得出。盘点扫描了 `frontend/` 与 `desktop/src/` 下 121 个范围内文件。

## 重构验收门禁

- 每条标识是一项验收检查。重构前用于对照基线，重构后按标识逐条核对对应入口、状态和结果。依赖列给出基线定位点，便于在实现拆分后查找迁移后的入口。
- 勾验时记下提交、交互标识、视口或平台、预期与实测结果。标有自动化的条目核对相应用例及其断言范围，剩余部分仍按验证方法操作。为每条最终结论留一份可追溯证据。
- 截图走查使用 1280×800、760×900、375×812 参考视口，涉及断点和粗指针时使用对应条件。外观和布局路径分别检查浅色、深色。桌面、远程、AI 载体和破坏性流程使用独立实例、合成数据和假载体。
- 位置均相对仓库根目录。自动化列列出源码可对应的现有测试，可能只覆盖业务层或该条中的一部分。`无` 表示在指定测试目录中未找到可对应的断言。本次交付做了静态取证，运行结果待阶段验收记录。

## 交互条目

### 工作台、工作区动态与状态刷新

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `workbench.new` | 点新对话或空页开始新对话 → 打开 Leader 创建表单 | `frontend/workspace-home.js:56`、`frontend/workspace-home.js:57` | `data-act=modal-leader` | `plugin/tests/test_workspace_home_ui.js::empty workspace invites a discussion, without automatically launching or creating a goal` | 手动 · 1280×800 · 点新对话或空页开始新对话，核对打开 Leader 创建表单 |
| `workbench.start-self` | 空白工作区（无 leader、无任务）点“自己搭”的开始新对话或画架构图 → 打开 Leader 创建表单或架构图，不自动启动 | `frontend/workspace-home.js:53` | `.wh-start`、`data-path=self` | `plugin/tests/test_workspace_home_ui.js::a blank workspace offers 自己搭 and 助手开局 as two equal entries that start nothing by themselves` | 手动 · 1280×800、375×812 · 新建空白工作区后点两个按钮 |
| `workbench.start-assistant` | 空白工作区点“让助手开局” → 打开全局助手（桌面为助手标签）并带 `#opening=<工作区>` | `frontend/workspace-home.js:53` | `data-path=assistant`、`/assistant.html#opening=` | `plugin/tests/test_workspace_home_ui.js::a blank workspace offers 自己搭 and 助手开局 as two equal entries that start nothing by themselves` | 手动 · 隔离桌面 · 点让助手开局，核对助手标签打开 |
| `workbench.settings` | 点当前工作区设置 → 打开工作区配置 | `frontend/workspace-home.js:57` | `data-act=workspace-settings` | 无 | 手动 · 1280×800 · 点当前工作区设置，核对打开工作区配置 |
| `workbench.working` | 点执行中计数 → 查看执行中的会话及派工 | `frontend/workspace-home.js:59`、`frontend/app.js:1576` | `data-tab=working` | `plugin/tests/test_workspace_home_ui.js::home names working sessions and dispatches separately from its task total` | 手动 · 1280×800 · 点执行中计数，核对查看执行中的会话及派工 |
| `workbench.architecture` | 点架构图 → 进入当前工作区架构图 | `frontend/workspace-home.js:59` | `data-view=architecture` | 无 | 手动 · 1280×800 · 点架构图，核对进入当前工作区架构图 |
| `workbench.review-home` | 在首页验收卡点击看报告、任务详情或验收动作 → 就地进入对应任务流程 | `frontend/workspace-home.js:42` | `.wh-review-actions` | `plugin/tests/test_goal_ui.js::review buttons decide on the shown report from the home card, the task banner or the reader`；`plugin/tests/test_workspace_home_ui.js::awaiting acceptance leads the home with report gist and hands-on steps` | 手动 · 1280×800 · 从首页各走入口一次 |
| `workbench.observation-home` | 点观察中条目 → 打开任务且可做后续验收 | `frontend/workspace-home.js:46` | `.wh-observing-row` | 无 | 手动 · 1280×800 · 点观察中条目，核对打开任务且可做后续验收 |
| `workbench.approval-home` | 点执行权限待确认计数 → 展开审批视图 | `frontend/workspace-home.js:60` | `data-tab=native-approvals` | 无 | 手动 · 1280×800 · 点执行权限待确认计数，核对展开审批视图 |
| `workbench.discussion-home` | 点继续对话 → 返回有讨论的 Leader | `frontend/workspace-home.js:61` | `.wh-discussion-row` | 无 | 手动 · 1280×800 · 点继续对话，核对返回有讨论的 Leader |
| `workbench.history-fold` | 展开已结束任务 → 浏览已结束卡片列表 | `frontend/workspace-home.js:61` | `.wh-history` | `plugin/tests/test_workspace_home_ui.js::task delivery stays within its task and completed work is folded` | 手动 · 1280×800 · 展开已结束任务，核对浏览已结束卡片列表 |
| `workbench.delivery-home` | 点可以查看的成果 → 打开对应轮次交付内容 | `frontend/workspace-home.js:62` | `.wh-delivery` | `plugin/tests/test_workspace_home_ui.js::task delivery stays within its task and completed work is folded` | 手动 · 1280×800 · 点可以查看的成果，核对打开对应轮次交付内容 |
| `workbench.overview-back` | 在工作区动态点返回工作台 → 恢复概览 | `frontend/app.js:856` | `data-tab=overview` | 无 | 手动 · 1280×800 · 在工作区动态点返回工作台，核对恢复概览 |
| `workbench.todos` | 点待你动手计数 → 打开人工待办页 | `frontend/app.js:856` | `.todo-summary`、`data-view=todos` | 无 | 手动 · 1280×800 · 点待你动手计数，核对打开人工待办页 |
| `workbench.approval` | 点执行权限待确认 → 显示当前所有审批卡 | `frontend/app.js:856` | `data-tab=native-approvals` | 无 | 手动 · 1280×800 · 点执行权限待确认，核对显示当前所有审批卡 |
| `workbench.goals` | 点目标 → 显示目标卡与计数 | `frontend/app.js:847`、`frontend/app.js:856` | `data-tab=goals` | 无 | 手动 · 1280×800 · 点目标，核对显示目标卡与计数 |
| `workbench.pending` | 点等你拍板 → 按归属呈现决策，支持跳到负责人 | `frontend/app.js:849`、`frontend/app.js:856` | `data-tab=pending`、`.ov-go` | 无 | 手动 · 1280×800 · 点等你拍板，核对按归属呈现决策，支持跳到负责人 |
| `workbench.library` | 点会话库查找与接入 → 打开本工作区会话库 | `frontend/app.js:856` | `data-view=adopt` | 无 | 手动 · 1280×800 · 点会话库查找与接入，核对打开本工作区会话库 |
| `workbench.working-list` | 点击在干活统计查看详情 → 看到实际在运行的人和派工并可打开会话 | `frontend/app.js:855` | `workingPeople`、`workingDispatches` | 无 | 手动 · 1280×800 · 点击在干活统计查看详情，核对看到实际在运行的人和派工并可打开会话 |
| `workbench.initial-retry` | 工作区首次状态读取失败后点重试 → 恢复加载或显示真实错误 | `frontend/app.js:1364`、`frontend/app.js:1389` | `loadState`、`retry` | `plugin/tests/test_app_state_ui.js::initial state failure shows the actual escaped error and retry can recover without toast or another timer` | 手动 · 1280×800 · 工作区首次状态读取失败后点重试，核对恢复加载或显示真实错误 |
| `workbench.refresh-focus` | 聚焦操作按钮或正在输入时遇到轮询 → 保留同一控件、文本、选区与滚动 | `frontend/app.js:215-233`、`frontend/app.js:1743` | `data-preserve`、`replace` | `plugin/tests/test_runtime_ui.js::live UI refresh preserves horizontal group scroll position` | 截图走查 · 1280px 在拍板自由回答或领队列表中等待状态改变 |
| `workbench.ime-refresh` | 中文输入法正在组词时轮询到达 → 保留编辑节点，组词完成后渲染最新状态 | `frontend/app.js:219`、`frontend/app.js:1405` | `compositionstart`、`compositionend` | 无 | 手动 · 1280×800 · 在拍板自由回答开始中文组词，模拟轮询后选词并核对内容 |

### 常驻左栏与工作区切换

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `rail.workspace-open` | 点 C. 标志 → 打开或关闭工作区切换清单 | `frontend/global-rail.js:191`、`frontend/app.js:1501` | `#workspace-entry`、`#workspace-popover` | 无 | 截图走查 · 1280px 在工作台、设置、助手和想法墙逐页打开 |
| `rail.workspace-select` | 选择工作区 → 前往目标，适用时复用最近路由 | `frontend/global-rail.js:42`、`frontend/global-rail.js:64`、`frontend/global-rail.js:67` | `.ws-item`、`readLastRoute` | 无 | 手动 · 1280×800 · 选择工作区，核对前往目标，适用时复用最近路由 |
| `rail.workspace-settings` | 点当前工作区设置行 → 收起切换层并进入配置 | `frontend/global-rail.js:56`、`frontend/app.js:1511` | `data-act=workspace-settings` | 无 | 手动 · 1280×800 · 点当前工作区设置行，核对收起切换层并进入配置 |
| `rail.workspace-overview` | 点击工作区总览与新建 → 回到工作区列表 | `frontend/global-rail.js:60` | `.ws-foot-btn`、`href=/` | 无 | 手动 · 1280×800 · 点击工作区总览与新建，核对回到工作区列表 |
| `rail.workspace-dismiss` | 点浮层外或按 Escape → 收起工作区切换，键盘路径恢复入口焦点 | `frontend/global-rail.js:188`、`frontend/global-rail.js:197`、`frontend/app.js:1487`、`frontend/app.js:1730` | `#workspace-popover` | 无 | 手动 · 1280×800 · 点浮层外或按 Escape，核对收起工作区切换，键盘路径恢复入口焦点 |
| `rail.nav-leaders` | 点 Leader 导航 → 展开或关闭人员面板，记住宽屏状态 | `frontend/app.js:334`、`frontend/app.js:1502` | `#leader-entry`、`crew.leadersOpen` | 无 | 手动 · 1280×800 · 点 Leader 导航，核对展开或关闭人员面板，记住宽屏状态 |
| `rail.nav-home` | 点主页 → 打开工作台，显示当前待处理数 | `frontend/app.js:415`、`frontend/app.js:1560` | `.rail-home`、`.rail-badge` | `plugin/tests/test_workspace_home_ui.js::home badge shares review, decision and TODO entries and ignores resolved work` | 手动 · 1280×800 · 点主页，核对打开工作台，显示当前待处理数 |
| `rail.nav-architecture` | 点架构图图标 → 打开当前工作区架构图 | `frontend/app.js:415`、`frontend/app.js:1560` | `data-view=architecture` | 无 | 手动 · 1280×800 · 点架构图图标，核对打开当前工作区架构图 |
| `rail.nav-notes` | 点想法墙图标 → 打开想法墙 | `frontend/app.js:415` | `href=/notes.html` | 无 | 手动 · 1280×800 · 点想法墙图标，核对打开想法墙 |
| `rail.nav-assistant` | 点助手图标 → 打开独立助手页面 | `frontend/app.js:415` | `href=/assistant.html` | 无 | 手动 · 1280×800 · 点助手图标，核对打开独立助手页面 |
| `rail.nav-git` | 点 Git 图标 → 打开 Git 页，待处理时显示数值 | `frontend/app.js:415`、`frontend/app.js:1560` | `.rail-git`、`.rail-badge` | `plugin/tests/test_runtime_ui.js::Git rail attention comes from state before any Git snapshot is read` | 手动 · 1280×800 · 点 Git 图标，核对打开 Git 页，待处理时显示数值 |
| `rail.nav-settings` | 点设置图标 → 打开当前设置页，重复点击关闭 | `frontend/app.js:1506` | `data-act=settings` | 无 | 手动 · 1280×800 · 点设置图标，核对打开当前设置页，重复点击关闭 |
| `rail.nav-channel` | 点 Leader 面板交流区 → 进入发言列表，入口有随工作区配色的未读数字 | `frontend/app.js:417`、`frontend/style.css:152` | `.leader-panel-channel`、`.b-unread` | 无 | 手动 · 1280×800 · 点 Leader 面板交流区，核对显示交流发言及未读计数 |
| `rail.nav-quota` | 点底部额度 → 展示完整用量，额度入口可见数字及重置时间 | `frontend/app.js:372`、`frontend/app.js:417` | `.leader-panel-quota` | `plugin/tests/test_runtime_ui.js::uncollected quota has only text and a collected zero has an empty determinate bar` | 手动 · 1280×800 · 点底部额度，核对展示完整用量，额度入口可见数字及重置时间 |
| `rail.nav-unread` | 任一 Leader 有未读消息 → 常驻 Leader 图标显示未读点 | `frontend/app.js:406`、`frontend/app.js:415` | `.rail-dot` | 无 | 手动 · 1280×800 · 任一 Leader 有未读消息，核对常驻 Leader 图标显示未读点 |
| `rail.standalone-theme` | 独立页切换主题或接收存储事件 → 与其他页面同步模式 | `frontend/global-rail.js:98`、`frontend/global-rail.js:184`、`frontend/global-rail.js:195` | `data-rail-act=theme`、`crew.theme` | 无 | 手动 · 1280×800 · 独立页切换主题或接收存储事件，核对与其他页面同步模式 |
| `rail.sticky-settings` | 打开设置后使用最左栏 → 工作区切换和导航仍可点 | `frontend/style.css:1108`、`frontend/settings-ui.css:93`、`frontend/app.js:1138` | `.sb`、`#settings-surface` | 无 | 截图走查 · 1280px 暗浅主题各打开设置后测试 C. 浮层 |
| `rail.restore-route` | 切换哈希、前进或后退 → 恢复对应会话与任务路由 | `frontend/app.js:1288`、`frontend/app.js:1313`、`frontend/app.js:1742` | `crew.lastProjectRoute`、`hashchange` | `plugin/tests/test_runtime_ui.js::first open shows the workspace overview while explicit conversation and TODO routes remain stable` | 手动 · 1280×800 · 切换哈希、前进或后退，核对恢复对应会话与任务路由 |
| `rail.web-workspace-entry` | 打开无项目参数的 Crew 首页 → 可以选择工作区，或进助手和设置 | `frontend/app.js:1374` | `.workspace-home`、`.project-entry` | 无 | 手动 · 1280×800 · 打开无项目参数的 Crew 首页，核对可以选择工作区，或进助手和设置 |
| `rail.route-owner-gone` | 当前会话被移除或任务离开该组 → 返回工作台或总会话并提示真实原因 | `frontend/app.js:1377-1379` | `loadState`、`navigate` | 无 | 手动 · 1280×800 · 当前会话被移除或任务离开该组，核对返回工作台或总会话并提示真实原因 |

### Leader 侧栏、团队与会话治理

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `leader.select` | 点击 Leader → 打开其总对话，宽屏默认展开任务栏 | `frontend/app.js:1557` | `data-act=leader` | 无 | 截图走查 · 在 1280 与 375 分别点击同一人，看聊天及任务栏 |
| `leader.pin` | 点行内图钉或菜单置顶项 → 在置顶分区与普通分区间移动 | `frontend/app.js:1545` | `data-act=leader-pin`、`.leader-pin` | `plugin/tests/test_runtime_ui.js::pinning keeps both parts ordered and saves the whole leader list` | 手动 · 1280×800 · 悬停再置顶，刷新后取消置顶 |
| `leader.pin-reveal` | 悬停或键盘聚焦 Leader 图钉 → 显露操作，置顶后常显 | `frontend/style.css:971-974` | `.leader-row`、`.leader-pin` | 无 | 截图走查 · 1280px 将指针移入移出，再 Tab 到图钉 |
| `leader.move-up` | 选择上移 → Leader 在所在分区中上移一格 | `frontend/app.js:1546` | `data-act=leader-up` | `plugin/tests/test_leader_order.py::test_order_success_persists_and_state_uses_saved_positions` | 手动 · 1280×800 · 同一区放三人，移动中间项 |
| `leader.move-down` | 选择下移 → Leader 在所在分区中下移一格 | `frontend/app.js:1546` | `data-act=leader-down` | `plugin/tests/test_leader_order.py::test_order_success_persists_and_state_uses_saved_positions` | 手动 · 1280×800 · 同一区放三人，移动中间项 |
| `leader.right-menu` | 右键会话表面 → 在指针附近打开会话菜单 | `frontend/app.js:1478` | `contextmenu`、`#menu` | 无 | 手动 · 1280×800 · 右键 Leader、成员和会话标题 |
| `leader.keyboard-menu` | 聚焦会话后按菜单键或 Shift+F10 → 打开同一会话菜单 | `frontend/app.js:1715` | `sessionSurface`、`ContextMenu` | 无 | 手动 · 1280×800 · 聚焦一行分别按两种快捷键 |
| `leader.menu-arrows` | 菜单内按上下键、Home 或 End → 在可用菜单项间移动和循环 | `frontend/app.js:1719` | `role=menu`、`menuItems` | 无 | 手动 · 1280×800 · 含禁用项时从首项往上，再跳到末项 |
| `leader.submenu` | 悬停、点击或按右方向键 → 展开会话子菜单并可进入子项 | `frontend/app.js:1483`、`frontend/app.js:1513`、`frontend/app.js:1723` | `data-act=submenu`、`openSubmenu` | 无 | 手动 · 1280×800 · 分别用鼠标和方向键打开同一子菜单 |
| `leader.menu-dismiss` | 按 Tab、Escape 或左方向键 → 离开菜单或回上一级并恢复焦点 | `frontend/app.js:1722` | `hideMenu`、`closeSubmenu` | 无 | 手动 · 1280×800 · 开启二级菜单后分别按各键 |
| `leader.rename` | 编辑会话名称 → 非空且改变时可保存并更新名称 | `frontend/app.js:1016`、`frontend/app.js:1073`、`frontend/app.js:1679` | `#session-rename-form`、`#rename-error` | `plugin/tests/test_session_ops.py::test_leader_rename_updates_group_members_pending_targets_and_survives_stale_start` | 手动 · 1280×800 · 输入空白、原名、新名，再保存 |
| `leader.look-dialog` | 选择 Leader 设置 → 查看名称、头像及配色编辑器 | `frontend/app.js:1023`、`frontend/app.js:1031` | `#leader-settings-form` | 无 | 手动 · 1280×800 · 从 Leader 菜单和资料卡进入 |
| `leader.look-swatches` | 点配色预设 → 更新头像和配色草稿 | `frontend/app.js:1541` | `data-act=look-color` | `plugin/tests/test_leader_appearance_http.py::test_color_and_avatar_show_in_state_and_the_avatar_is_served_by_version` | 手动 · 1280×800 · 点两种色并比较预览 |
| `leader.look-custom-color` | 通过颜色输入选择自定义颜色 → 预览新配色 | `frontend/app.js:1675` | `data-look-color` | `plugin/tests/test_leader_appearance_http.py::test_color_and_avatar_show_in_state_and_the_avatar_is_served_by_version` | 手动 · 1280×800 · 选非预设颜色并保存后刷新 |
| `leader.look-reset-color` | 点重置颜色 → 回到自动配色 | `frontend/app.js:1542` | `data-act=look-color-reset` | 无 | 手动 · 1280×800 · 自定义后再重置 |
| `leader.look-avatar` | 选头像图片 → 裁剪压缩后预览，错误在表单中显示 | `frontend/app.js:1044`、`frontend/app.js:1676` | `data-look-avatar`、`CrewLook` | `plugin/tests/test_leader_appearance_http.py::test_color_and_avatar_show_in_state_and_the_avatar_is_served_by_version`；`plugin/tests/test_leader_appearance_http.py::test_rejects_large_unknown_or_malformed_pictures_bad_colors_and_members` | 手动 · 1280×800 · 上传横图及无法解码文件 |
| `leader.look-avatar-remove` | 移除自定义头像 → 预览恢复默认头像 | `frontend/app.js:1543` | `data-act=look-avatar-remove` | 无 | 手动 · 1280×800 · 有头像时移除并保存 |
| `leader.look-save` | 保存 Leader 外观 → 列表与聊天头像同步更新 | `frontend/app.js:1060` | `#leader-settings-form` | `plugin/tests/test_leader_appearance_http.py::test_color_and_avatar_show_in_state_and_the_avatar_is_served_by_version` | 手动 · 1280×800 · 保存名称头像配色并重新进入 |
| `leader.trust-dialog` | 打开授权设置并提交范围 → 显示当前 Leader 的授权状态 | `frontend/app.js:1092`、`frontend/app.js:1101` | `#trust-form`、`/api/trust` | 无 | 手动 · 1280×800 · 填写范围保存再打开查看 |
| `leader.trust-mode` | 选择由我拍板或自主推进 → 保存模式，首次自主推进要求书面范围 | `frontend/app.js:1531` | `data-act=leader-mode`、`/api/trust` | `plugin/tests/test_goal_modes_reports.py::test_leader_grant_reaches_both_general_carriers_and_allows_autonomous_task_start` | 手动 · 1280×800 · 无历史范围开启，再切回 |
| `leader.lifecycle-undo` | 点击结束提示中的撤销 → 恢复原分组状态 | `frontend/app.js:1554` | `/api/group/undo` | `plugin/tests/test_session_ops.py::test_close_both_modes_and_undo_restore_exact_group_and_controls` | 手动 · 1280×800 · 结束含成员部门后点撤销 |
| `leader.wake` | 点叫醒 → 提交继续当前任务的消息并显示结果 | `frontend/app.js:1556` | `/api/wake` | `plugin/tests/test_leader_channel_http.py::test_human_can_wake_member` | 手动 · 1280×800 · 用假载体叫醒休眠会话 |
| `leader.member-chat` | 点成员会话 → 进入该成员历史及适用任务范围 | `frontend/app.js:1559` | `data-act=member-chat`、`memberScopes` | 无 | 手动 · 1280×800 · 从团队及任务入口分别点击成员 |
| `leader.member-history` | 点成员历史 → 打开成员对话视图 | `frontend/app.js:1671` | `data-act=member-history` | `plugin/tests/test_runtime_ui.js::session menus keep leader chat and member history without a VS Code handoff` | 手动 · 1280×800 · 在资料卡进入并核对会话身份 |
| `leader.member-card` | 打开资料卡 → 显示会话详情并将焦点移到关闭按钮 | `frontend/app.js:924`、`frontend/app.js:1582` | `#card`、`data-act=card-close` | 无 | 手动 · 1280×800 · 键盘打开，检查焦点和显示资料 |
| `leader.member-card-dismiss` | 关闭资料卡或点遮罩 → 关闭菜单并恢复打开前焦点 | `frontend/app.js:925`、`frontend/app.js:1494` | `#card-back`、`returnFocus` | 无 | 手动 · 1280×800 · 分别按关闭、Escape、遮罩 |
| `leader.move-member-menu` | 点换组 → 菜单显示可接收的其他 Leader | `frontend/app.js:1659` | `data-act=move-menu` | 无 | 手动 · 1280×800 · 在两部门间打开目标列表 |
| `leader.move-member` | 选择另一 Leader → 成员换组并显示新部门 | `frontend/app.js:1661` | `/api/roster/move` | 无 | 手动 · 1280×800 · 移动后检查两边分组 |
| `leader.leave-group` | 点退组 → 关闭资料卡并可从待收编重新找回 | `frontend/app.js:1662` | `/api/roster/remove` | 无 | 手动 · 1280×800 · 退组再打开会话库 |
| `leader.archive` | 归档会话 → 会话从当前视图移除并显示撤销 | `frontend/app.js:1663`、`frontend/app.js:1399` | `/api/session/archive` | `plugin/tests/test_runtime_ui.js::archive and delete conflicts toast the exact backend Chinese reason`；`plugin/tests/test_session_ops.py::test_archive_remove_restore_undo_preserve_original_snapshot` | 手动 · 1280×800 · 归档后点撤销并核对列表 |
| `leader.delete-confirm` | 点删除 → 看到级联删除会话和原生记录的确认说明 | `frontend/app.js:1178`、`frontend/app.js:1664` | `data-act=delete-confirm` | 无 | 手动 · 1280×800 · 在测试部门看确认说明后取消 |
| `leader.delete-result` | 确认删除 → 移除会话，原生删除部分失败时提示详情 | `frontend/app.js:1665` | `/api/session/delete` | `plugin/tests/test_runtime_ui.js::archive and delete conflicts toast the exact backend Chinese reason` | 手动 · 1280×800 · 隔离夹具验证成功及部分失败 |
| `leader.new-dialog` | 点新建 Leader → 出现创建表单 | `frontend/app.js:1264` | `#leader-form` | 无 | 手动 · 1280×800 · 从侧栏入口打开、取消、重开 |
| `leader.new-runtime` | 选择新 Leader 的 AI 载体 → 模型选项随载体变更并保持选项焦点 | `frontend/app.js:1626` | `data-act=leader-runtime` | `plugin/tests/test_runtime_ui.js::new leader auto selects a sole runtime and asks for a choice when both are installed` | 手动 · 1280×800 · 先选 CC 再选 Codex |
| `leader.new-model` | 选择新 Leader 的模型 → 更新表单内模型选择 | `frontend/app.js:1626` | `data-act=leader-model` | `plugin/tests/test_runtime_ui.js::creation preserves custom models and explicit efforts and reports known capability conflicts` | 手动 · 1280×800 · 选另一模型，看选中态 |
| `leader.new-effort` | 选择新 Leader 的推理强度 → 更新强度选项 | `frontend/app.js:1626` | `data-act=leader-effort` | `plugin/tests/test_runtime_ui.js::creation preserves custom models and explicit efforts and reports known capability conflicts` | 手动 · 1280×800 · 选择模型后切换强度 |
| `leader.new-speed` | 选择新 Codex Leader 的速度档位 → 更新速度选择 | `frontend/app.js:1626` | `data-act=leader-speed` | 无 | 手动 · 1280×800 · 选普通和快速并检查选中态 |
| `leader.new-submit` | 提交新 Leader → 成功后进入新会话，失败在表单内保留错误 | `frontend/app.js:1696` | `#leader-error`、`/api/leader` | `plugin/tests/test_runtime_ui.js::an unavailable tool has a clear error and cannot submit or update options` | 手动 · 1280×800 · 用假载体创建一次并模拟一次失败 |
| `leader.model-menu` | 点击模型标签 → 打开模型菜单并在发现完成后刷新 | `frontend/app.js:1633` | `data-act=model-menu`、`refreshRuntimes` | `plugin/tests/test_runtime_ui.js::existing session model menus use its own tool catalog and never expose runtime conversion` | 手动 · 1280×800 · 有缓存时展开，等后台更新 |
| `leader.model-refresh` | 点刷新模型列表 → 更新原生模型发现并按原入口重开菜单 | `frontend/app.js:1514` | `data-act=runtimes-refresh` | `plugin/tests/test_runtime_ui.js::runtime refresh corrects an automatic leader default while preserving the name and task draft` | 手动 · 1280×800 · 打开模型选项并点刷新，查看结果或错误 |
| `leader.set-model` | 在模型菜单点选模型 → 变更会话选项并显示提示 | `frontend/app.js:1648` | `/api/session/options`、`data-act=set-model` | `plugin/tests/test_runtime_ui.js::a combined model and effort change overrides both stored and applied options in the notice` | 手动 · 1280×800 · 分别切换 CC 和 Codex 的当轮配置 |
| `leader.set-effort` | 点选推理强度 → 保存当前会话的推理配置 | `frontend/app.js:1648` | `/api/session/options`、`data-act=set-effort` | `plugin/tests/test_runtime_ui.js::a combined model and effort change overrides both stored and applied options in the notice` | 手动 · 1280×800 · 切换强度后重开菜单 |
| `leader.set-speed` | 点选 Codex 速度档 → 保存当前会话的速度配置 | `frontend/app.js:1648` | `/api/session/options`、`data-act=set-speed` | `plugin/tests/test_runtime_ui.js::chat only offers supported standard and fast speeds while session settings can restore inheritance` | 手动 · 1280×800 · 切换档位并核对继承项 |
| `leader.dispatch-history` | 点临时协作记录 → 显示协作记录与更早条数 | `frontend/app.js:1577` | `data-act=dispatch-history`、`.dispatch-history` | 无 | 手动 · 1280×800 · 使用有多次协作的会话打开再关闭 |
| `leader.dispatch-fold` | 展开协作记录 → 状态刷新后保持该范围的展开状态 | `frontend/app.js:1699` | `data-dispatch-history`、`dispatchHistoryOpen` | 无 | 手动 · 1280×800 · 展开后跨越一次轮询 |
| `leader.modal-dismiss` | 点击遮罩或按 Escape → 关闭当前弹窗并还原焦点，保存中保持弹窗 | `frontend/app.js:1110`、`frontend/app.js:1493`、`frontend/app.js:1734` | `#backdrop`、`returnFocus` | 无 | 手动 · 1280×800 · 打开表单后分别测试遮罩与 Escape |
| `leader.modal-tab` | 在对话框首尾按 Tab 或 Shift+Tab → 焦点在可见控件间循环 | `frontend/app.js:1735` | `#backdrop`、`#card` | 无 | 手动 · 1280×800 · 用键盘走到首尾 |
| `leader.drag` | 拖动 Leader → 在目标行前后落位并保存排序 | `frontend/app.js:429`、`frontend/app.js:441`、`frontend/app.js:452` | `.leader-row[draggable]`、`/api/leaders/order` | `plugin/tests/test_leader_order.py::test_order_success_persists_and_state_uses_saved_positions`；`plugin/tests/test_leader_pins.py::test_pinned_leaders_show_first_in_saved_order_and_empty_clears` | 手动 · 1280×800 · 在同组及置顶边界拖动，刷新比较顺序 |
| `leader.row-unread` | 收到未读消息 → Leader 行出现蓝点与可读标签 | `frontend/app.js:328` | `.leader-unread-dot`、`aria-label` | 无 | 手动 · 1280×800 · 停留在其他会话，收到回复后回到该 Leader |
| `leader.active-goal` | 点运行中的任务徽记 → 转到归属 Leader 的任务范围 | `frontend/app.js:1558` | `data-act=active-goal`、`data-goal-id` | 无 | 手动 · 1280×800 · 点 Leader 与成员各自的运行任务 |
| `leader.team-work` | 团队有实际工作时查看 Leader 行 → 工作标记与成员状态同步 | `frontend/app.js:328`、`frontend/app.js:403` | `groupAvatar`、`groupWorkLabel` | `plugin/tests/test_working_overview_ui.js::the sidebar leader row summarises group work, keeps its group during member chats and marks leader autonomy on both carriers` | 手动 · 1280×800 · 团队有实际工作时查看 Leader 行，核对工作标记与成员状态同步 |
| `leader.branch-badge` | Leader 有关联分支 → 列表行显示 Git 分支信息 | `frontend/app.js:313`、`frontend/app.js:328` | `leaderBranches` | 无 | 手动 · 1280×800 · Leader 有关联分支，核对列表行显示 Git 分支信息 |
| `leader.branch-status` | 分支胶囊的后台 Git 扫描未完成或失败 → Leader 列表上方显示“分支信息计算中…”或“分支信息读取失败：原因”，失败行点开 Git 页；扫描完成后经事件流自动换成胶囊 | `frontend/app.js:343`、`frontend/app.js:445` | `.leader-git-status`、`state.git_branches.pending`、`state.git_branches.error` | `plugin/tests/test_runtime_ui.js::the leader list says while branch chips are computed and shows why their scan failed`；`plugin/tests/test_git_management.py::test_first_state_does_not_wait_for_the_branch_scan_and_gets_the_direct_result_after_it`、`::test_failed_branch_scan_shows_its_reason_until_a_scan_succeeds` | 手动 · 1280×800 与 375px · 在大仓库工作区冷启动后打开，核对先显示计算中、随后出现胶囊；浅色与深色各看一遍 |
| `leader.group-tree` | 菜单点查看会话结构 → 打开该部门聊天和右侧结构 | `frontend/app.js:1544` | `data-act=group-tree` | 无 | 手动 · 1280×800 · 菜单点查看会话结构，核对打开该部门聊天和右侧结构 |
| `leader.session-menu-button` | 点聊天头部省略号 → 打开当前会话操作菜单 | `frontend/app.js:744`、`frontend/app.js:1528` | `data-act=session-menu`、`#menu` | 无 | 手动 · 1280×800 · 点聊天头部省略号，核对打开当前会话操作菜单 |
| `leader.session-prompts` | 点行为配置与使用记录 → 打开该会话配置 | `frontend/app.js:986`、`frontend/app.js:1508` | `data-category=prompts`、`data-prompt-session` | 无 | 手动 · 1280×800 · 点行为配置与使用记录，核对打开该会话配置 |
| `leader.stop-menu` | 选择停下这一轮 → 显示中断请求；外部、离线或空闲时操作禁用 | `frontend/app.js:966`、`frontend/app.js:1670` | `/api/session/stop`、`stopReason` | 无 | 手动 · 1280×800 · 选择停下这一轮，核对显示中断请求；外部、离线或空闲时操作禁用 |
| `leader.group-only` | 选只结束 Leader → 成员退回待收编 | `frontend/app.js:979`、`frontend/app.js:1551` | `data-with-members=false`、`/api/group/close` | `plugin/tests/test_session_ops.py::test_close_both_modes_and_undo_restore_exact_group_and_controls` | 手动 · 1280×800 · 有两个成员时结束 Leader，核对成员进入待收编并用提示撤销 |
| `leader.group-with-members` | 选连执行一起归档 → 部门及成员一起归档 | `frontend/app.js:979`、`frontend/app.js:1551` | `data-with-members=true`、`/api/group/close` | `plugin/tests/test_session_ops.py::test_close_both_modes_and_undo_restore_exact_group_and_controls` | 手动 · 1280×800 · 有两个成员时选择一起归档，核对成员归档并用提示撤销 |

### 对话、状态和会话结构

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `chat.context-ring.level` | 查看压缩圆环 → 显示用量或未知态，50% 与 80% 分别跨入警告和危险色 | `frontend/chat.js:22` | `.context-ring`、`CONTEXT_WARN`、`CONTEXT_DANGER` | `plugin/tests/test_runtime_ui.js::every context ring is a named native button at low and high usage across both carriers and roles` | 截图走查 · 1280px 查看 49%、50%、80% 和未知四种快照 |
| `chat.context-ring.last-observed` | 查看离线会话圆环 → 提示上次已用额度或上限未知 | `frontend/chat.js:14` | `usageText`、`last_observed`、`transcript` | `plugin/tests/test_runtime_ui.js::offline conversations with history keep a usable compact ring without showing a percentage` | 手动 · 1280×800 · 用离线夹具悬停或聚焦圆环 |
| `chat.context-ring.leader` | 点 Leader 行的压缩圆环 → 操作指向该行会话 | `frontend/app.js:332` | `.leader-row .context-ring`、`data-id` | 无 | 手动 · 1280×800 · 两人都显示圆环时点击非当前人圆环 |
| `chat.context-ring.header-request` | 点总对话头部圆环 → 直接请求压缩，忙时排队 | `frontend/app.js:1638`、`frontend/app.js:1185` | `#chat-head`、`data-act=context-compact`、`/api/session/compact` | `plugin/tests/test_runtime_ui.js::a ring queues only its own session, suppresses duplicate clicks, and clears on actual completion` | 手动 · 1280×800 · 从空闲和繁忙总对话点环，检查请求与排队 |
| `chat.context-ring.phases` | 请求压缩 → 圆环显示请求中、排队或压缩中，并保持焦点 | `frontend/chat.js:25`、`frontend/chat.js:34`、`frontend/app.js:1185` | `aria-busy`、`aria-disabled`、`is-queued`、`is-compacting` | `plugin/tests/test_runtime_ui.js::a ring queues only its own session, suppresses duplicate clicks, and clears on actual completion` | 手动 · 1280×800 · 默认和高用量各用键盘触发，验证忙时三个阶段、重复点击及焦点 |
| `chat.context-ring.retry` | 压缩失败后点圆环 → 显示失败原因并能重新请求 | `frontend/chat.js:25`、`frontend/app.js:1185` | `.context-ring.has-failed` | `plugin/tests/test_runtime_ui.js::failed or cancelled compaction restores its ring; a fast completion cannot be reset by the HTTP receipt` | 手动 · 1280×800 · 模拟失败后悬停看原因，再点击 |
| `chat.context-ring.result` | 原生压缩完成 → 提示前后用量，失败显示实际错误 | `frontend/chat.js:1194`、`frontend/chat.js:1200`、`frontend/app.js:1192` | `watchCompact`、`notifyCompact` | `plugin/tests/test_chat_connection.js::compact watchers use finite history reads and finish once on a new native receipt` | 手动 · 1280×800 · 依次回放成功、失败和未知用量事件 |
| `chat.activity-phase` | 会话忙时 → 显示请求、发送、生成或停止等阶段 | `frontend/chat.js:124`、`frontend/chat.js:741` | `CrewActivity`、`.chat-activity` | `plugin/tests/test_chat_connection.js::state polls update activity without regenerating transcript HTML or replacing its nodes` | 手动 · 1280×800 · 逐阶段回放并检查结束时收起 |
| `chat.activity-clock` | 等待输出 → 服务端时钟校准后持续更新时间 | `frontend/chat.js:45`、`frontend/chat.js:79` | `CrewActivity.timestamp`、`data-activity-key` | `plugin/tests/test_chat_connection.js::rendering an idle neighbor keeps a newly inserted activity clock moving` | 手动 · 1280×800 · 延迟数秒观察计时并切换标签 |
| `chat.activity-tokens` | 流式生成 → 平滑更新并格式化 token 数 | `frontend/chat.js:53`、`frontend/chat.js:79` | `CrewActivity`、`renderActivity` | `plugin/tests/test_chat_connection.js::Claude and Codex chats render live tokens without a page-level activity sync` | 手动 · 1280×800 · 持续输出与结束各观察一次 |
| `chat.stop-turn` | 忙时点停止 → 请求中断本轮并显示状态或错误 | `frontend/chat.js:474`、`frontend/chat.js:1120` | `.chat-stop`、`.chat-interrupt-status`、`/api/session/stop` | `plugin/tests/test_chat_connection.js::the general stop waits for its process-exit receipt and then confirms the identity is stopped`；`plugin/tests/test_chat_connection.js::interrupt failure is explicit and can be retried without discarding a draft` | 手动 · 1280×800 · 假载体输出时点停止 |
| `chat.readonly` | 打开只读会话 → 看见原因与历史，输入框撤下 | `frontend/chat.js:1080` | `.readonly-note` | `plugin/tests/test_chat_connection.js::readonly status transitions preserve the same conversation draft and reading position without sending` | 手动 · 1280×800 · 打开未托管会话 |
| `chat.date-divider` | 跨日查看对话 → 按日期显示今日、昨日或具体日期 | `frontend/chat.js:692` | `.date-div`、`dayOf` | 无 | 手动 · 1280×800 · 回放跨日和跨年记录 |
| `chat.unread-divider` | 进入有未读的会话 → 定位离开后消息分隔线 | `frontend/chat.js:697`、`frontend/chat.js:731` | `.unread-div`、`unreadAnchor` | `plugin/tests/test_chat_connection.js::an unread anchor evicted from the new tail window positions the reader at the window top`；`plugin/tests/test_chat_connection.js::general conversation unread dividers follow reply text, excluding tools and unchanged snapshots` | 手动 · 1280×800 · 在其他会话收消息后首次打开 |
| `chat.follow-bottom` | 在底部接收输出 → 自动跟随最新消息 | `frontend/chat.js:688`、`frontend/chat.js:736` | `.chat-scroll`、`atBottom` | 无 | 手动 · 1280×800 · 留在底部连续接收三条 |
| `chat.preserve-anchor` | 向上阅读时接收增量 → 保持首个可见消息的位置 | `frontend/chat.js:700`、`frontend/chat.js:737` | `.chat-scroll`、`anchor` | `plugin/tests/test_chat_connection.js::a tail window renders orphan tool results, restores their owner on prepend and keeps the reading anchor` | 手动 · 1280×800 · 向上滚屏并流式更新 |
| `chat.restore-position` | 离开再返回 → 恢复该聊天阅读位置 | `frontend/chat.js:1084`、`frontend/chat.js:1160`、`frontend/chat.js:1182` | `cache.scrollTop`、`transcripts` | `plugin/tests/test_chat_connection.js::cached transcripts paint synchronously and catch up only through SSE after their cursor`；`plugin/tests/test_chat_connection.js::readonly status transitions preserve the same conversation draft and reading position without sending` | 手动 · 1280×800 · 在两会话和任务间来回切换 |
| `chat.jump-latest` | 点回到最新 → 滚到底部并更新已读 | `frontend/chat.js:1083` | `.jump`、`scheduleRead` | 无 | 手动 · 1280×800 · 向上滚动后点回到最新 |
| `chat.load-earlier` | 点加载更早记录 → 向上追加历史并保持视觉位置 | `frontend/chat.js:970`、`frontend/chat.js:1086` | `data-chat-earlier` | `plugin/tests/test_chat_connection.js::a tail window renders orphan tool results, restores their owner on prepend and keeps the reading anchor` | 手动 · 1280×800 · 超过一页时加载两次 |
| `chat.message-focus` | 通过消息定位入口打开聊天 → 滚到指定消息并高亮 | `frontend/chat.js:724`、`frontend/chat.js:957` | `.chat-focus`、`applyFocus` | 无 | 手动 · 1280×800 · 使用带消息定位的入口进入 |
| `chat.details-fold` | 展开或收起工具与过程块 → 更新展开态并保留在会话缓存 | `frontend/chat.js:1095` | `data-chat-fold`、`cache.expanded` | `plugin/tests/test_chat_connection.js::expanded process survives revisiting and still permits expanding the original tool` | 手动 · 1280×800 · 展开后流式刷新与切走返回 |
| `chat.channel-jump` | 点交流消息链接 → 定位交流区对应消息 | `frontend/chat.js:1087` | `data-chat-channel` | 无 | 手动 · 1280×800 · 从交谈事件进入并看目标 |
| `chat.routed-task` | 点已转任务链接 → 打开正确任务，失败给出提示 | `frontend/chat.js:1089` | `data-chat-goal`、`onRouted` | 无 | 手动 · 1280×800 · 回放链接并使用不可用目标再试 |
| `chat.read-visibility` | 切换标签可见性 → 隐藏时断开，回到可见时补读并重连 | `frontend/chat.js:851`、`frontend/chat.js:1108` | `visibilitychange`、`crew-tab-visibility` | `plugin/tests/test_chat_connection.js::hidden browser and desktop conversations release their stream and resume after the last event` | 手动 · 1280×800 · 切走时收到消息，切回前后看未读 |
| `chat.connection-retry` | 连接或历史读取失败 → 显示状态并重试更新 | `frontend/chat.js:865`、`frontend/chat.js:874`、`frontend/chat.js:919` | `.chat-connection-error`、`historyRetryTimer` | `plugin/tests/test_chat_connection.js::compact history transport failures report the real cause and retry reads without finishing or resending` | 手动 · 1280×800 · 隔离环境模拟断连再恢复 |
| `chat.file-preview` | 点击消息中的本地路径 → 显示预览及对应平台操作 | `frontend/chat.js:257` | `initFiles`、`fileContentUrl` | `plugin/tests/test_documents_ui.js::beside a conversation a document opens in the reading pane and names itself for the composer` | 手动 · 1280×800 · 分别打开文本、图片与不可预览文件 |
| `chat.file-open` | 桌面预览中点打开 → 交给系统默认应用 | `frontend/chat.js:313` | `.file-open` | `plugin/tests/test_documents_ui.js::document preview carries a numeric ID through resolve, content, open and reveal` | 手动 · 1280×800 · 打开临时文档 |
| `chat.file-reveal` | 桌面预览中点在文件夹显示 → 定位本地文件 | `frontend/chat.js:313` | `.file-reveal` | `plugin/tests/test_documents_ui.js::document preview carries a numeric ID through resolve, content, open and reveal` | 手动 · 1280×800 · 打开已知附件后点定位 |
| `chat.file-download` | 网页预览中点下载 → 取回所选文件 | `frontend/chat.js:316` | `.file-download`、`fileDownloadUrl` | 无 | 手动 · 1280×800 · 下载并比较文件内容 |
| `chat.file-close` | 点关闭或按 Escape → 收起文件预览 | `frontend/chat.js:308`、`frontend/chat.js:311`、`frontend/chat.js:320` | `.file-close` | 无 | 手动 · 1280×800 · 分别关闭阅读器和独立预览面板 |
| `chat.code-copy` | 点击代码块复制 → 复制原代码并给出反馈 | `frontend/chat.js:242` | `.code-copy`、`CrewBrowser` | 无 | 手动 · 1280×800 · 复制后粘贴并核对缩进 |
| `chat.markdown` | 看带中文标点加粗、链接或路径的消息 → 显示对应富文本 | `frontend/chat.js:146`、`frontend/chat.js:163`、`frontend/chat.js:571` | `linkedText`、`markdown-it` | `plugin/tests/test_chat_markdown.js::a leader without an optional goal renders empty inline text instead of breaking the workspace` | 手动 · 1280×800 · 回放含四种元素的消息 |
| `chat.context-ring.compact-confirm` | 选会话菜单的压缩项 → 显示确认对话框，确认才提交 | `frontend/app.js:1171`、`frontend/app.js:1643`、`frontend/app.js:1645` | `data-act=compact`、`data-act=compact-confirm` | 无 | 手动 · 1280×800 · 先取消，再确认，核对是否发请求 |
| `chat.context-ring.external` | 点原工具会话的压缩入口 → 提示在原工具输入 /compact | `frontend/app.js:1189` | `item.where=vscode`、`toast` | `plugin/tests/test_runtime_ui.js::an externally owned ring gives the exact native-tool instruction without starting another process` | 手动 · 1280×800 · 使用原工具中的会话夹具点击 |
| `chat.stop-person-confirm` | 中断涉及负责人任务时 → 弹出含影响任务清单的二次确认 | `frontend/app.js:1111`、`frontend/app.js:1512`、`frontend/chat.js:474` | `#stop-person-title`、`data-act=confirm-stop-person` | `plugin/tests/test_chat_connection.js::the general stop confirmation locks repeated clicks and cancellation sends no stop request` | 手动 · 1280×800 · 分别点击返回和确认，核对取消无请求 |
| `chat.tree-toggle` | 点会话结构图标 → 展开或收起结构分区 | `frontend/app.js:1602`、`frontend/app.js:655` | `data-act=right-open`、`#right-panel` | 无 | 手动 · 1280×800 · 点会话结构图标，核对展开或收起结构分区 |
| `chat.tree-close` | 点结构分区关闭 → 隐藏并回焦头部入口 | `frontend/app.js:1621` | `data-act=right-close` | 无 | 手动 · 1280×800 · 点结构分区关闭，核对隐藏并回焦头部入口 |
| `chat.tree-history` | 在结构区展开派发历史 → 按人员和任务保留折叠状态 | `frontend/app.js:620`、`frontend/app.js:1699` | `data-dispatch-history`、`.rp-history` | 无 | 手动 · 1280×800 · 在结构区展开派发历史，核对按人员和任务保留折叠状态 |
| `chat.tree-dispatch` | 查看会话结构 → 按当前对话范围看到派发及运行计数 | `frontend/app.js:614`、`frontend/app.js:626` | `.rp-dispatch`、`conversationDispatches` | 无 | 手动 · 1280×800 · 查看会话结构，核对按当前对话范围看到派发及运行计数 |
| `chat.member-default-scope` | 进入只有任务记录的成员 → 默认选任务记录，已有总对话时优先总对话 | `frontend/app.js:683` | `memberScopes`、`general_has_output` | 无 | 手动 · 1280×800 · 分别打开只有任务历史与同时有总对话的两人 |
| `chat.message-meta` | 移动指针到消息 → 显示消息辅助信息，有待发或失败状态时常显 | `frontend/style.css:364-366`、`frontend/chat.js:615` | `.m-meta`、`.m-user.pending`、`.m-user.failed` | 无 | 截图走查 · 1280px 悬停正常消息，再比较待发和失败消息 |

### 输入框和技能

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `composer.send-key` | 输入后按 Enter → 桌面发送，Shift+Enter 或粗指针换行，IME 组词不误发 | `frontend/chat.js:1135` | `CrewBrowser.chatSendKey` | 无 | 截图走查 · 1280px 键盘测试 Enter、Shift+Enter 及中文组词；375 粗指针按 Enter |
| `composer.send-button` | 点发送 → 非空文本或图片进入发送流程 | `frontend/chat.js:1152` | `.composer`、`.send` | `plugin/tests/test_chat_connection.js::optimistic input is visible before send starts and merges a same-ID receipt without duplication` | 手动 · 1280×800 · 分别发送文本、纯图片与混合内容 |
| `composer.resize` | 输入多行 → 编辑区随内容增高并更新可发送状态 | `frontend/chat.js:491`、`frontend/chat.js:1134` | `resizeInput`、`.composer textarea` | 无 | 手动 · 1280×800 · 输入多行后删回 |
| `composer.draft-text` | 输入后切换会话或刷新 → 恢复对应会话与任务的草稿 | `frontend/chat.js:439`、`frontend/chat.js:1067`、`frontend/chat.js:1134` | `crew.draft:工作区:人员[:goal:任务]`、`localStorage` | `plugin/tests/test_chat_connection.js::readonly status transitions preserve the same conversation draft and reading position without sending` | 手动 · 1280×800 · 总对话和两个任务分别输入再刷新 |
| `composer.draft-images` | 添加图片后切页或刷新 → 恢复该会话图片草稿 | `frontend/chat.js:515`、`frontend/chat.js:1073` | `cache.images`、`crew.draft:工作区:人员[:goal:任务]` | 无 | 手动 · 1280×800 · 两会话放不同图片后刷新 |
| `composer.send-failure` | 发送失败 → 按编辑版本保留草稿并提示错误 | `frontend/chat.js:995` | `cache.version`、`.chat-delivery-status` | `plugin/tests/test_chat_connection.js::rejected and uncertain sends retain their draft, true reason and one marked message without automatic retry` | 手动 · 1280×800 · 延迟失败期间继续输入，对照原内容 |
| `composer.delivery-state` | 提交输入 → 显示发送、排队或交付异常 | `frontend/chat.js:452`、`frontend/chat.js:995` | `pendingSendSeq`、`.chat-delivery-status` | `plugin/tests/test_chat_connection.js::rejected and uncertain sends retain their draft, true reason and one marked message without automatic retry`；`plugin/tests/test_chat_connection.js::busy input sends a stable idempotent message and keeps failed drafts until actual acceptance` | 手动 · 1280×800 · 分别模拟接受、排队与失败 |
| `composer.image-paste` | 粘贴截图 → 输入框显示待发送缩略图 | `frontend/chat.js:1139` | `onpaste`、`.attach` | 无 | 手动 · 1280×800 · 复制截图粘贴 |
| `composer.image-picker` | 点添加图片 → 可多选图片并显示缩略图 | `frontend/chat.js:1144` | `.chat-image-button`、`.chat-image-file` | 无 | 手动 · 1280×800 · 选择两张，再重选同一张 |
| `composer.image-limit` | 选非图片、超 5 MB 或第六张 → 显示限制提示 | `frontend/chat.js:508` | `addImages`、`.attach` | 无 | 手动 · 1280×800 · 分别提交三种边界输入 |
| `composer.image-remove` | 点缩略图移除 → 删除所选图片并更新草稿 | `frontend/chat.js:1146` | `data-remove-image` | 无 | 手动 · 1280×800 · 附两张图后移除第一张 |
| `composer.task-bind` | 点任务标签 → 切换下一条输入是否补充给所选任务 | `frontend/chat.js:350`、`frontend/chat.js:1123` | `.chat-task-binding`、`taskBound` | `plugin/tests/test_chat_connection.js::task binding follows the selected task, resets on switch and only scopes a highlighted send` | 手动 · 1280×800 · 有任务时开关并核对归属 |
| `composer.note-prefill` | 从想法墙带入想法 → 附标签，空输入时预填 | `frontend/chat.js:388` | `setNote`、`.chat-note-chip` | `plugin/tests/test_chat_connection.js::an idea from the wall rides on exactly one send as note_id, prefilling only an empty composer` | 手动 · 1280×800 · 空和非空草稿各试一次 |
| `composer.note-remove` | 点想法标签移除 → 去掉想法关联并回焦输入框 | `frontend/chat.js:1125` | `.chat-note-chip` | 无 | 手动 · 1280×800 · 从想法墙进入后移除 |
| `composer.reading-toggle` | 点正在看标签 → 切换本条输入是否附带阅读对象 | `frontend/chat.js:370`、`frontend/chat.js:1127` | `.chat-reading-chip`、`reading.attached` | 无 | 手动 · 1280×800 · 打开报告后切换两次并核对 aria-pressed |
| `composer.numbered-list` | 列表内换行 → 延续列表编号或符号 | `frontend/chat.js:1129`、`frontend/browser-utils.js:60` | `CrewBrowser.enableChatLists`、`beforeinput` | 无 | 手动 · 1280×800 · 有序和无序分别连续换行并结束空列表 |
| `composer.skill-trigger` | 输入斜杠或美元符触发词 → 获取当前会话可用技能 | `frontend/skill-picker.js:27`、`frontend/chat.js:1130` | `CrewSkillPicker`、`/api/chat/skills` | 无 | 手动 · 1280×800 · 在 CC 与 Codex 各试 |
| `composer.skill-filter` | 输入关键词 → 菜单按匹配过滤排序 | `frontend/skill-picker.js:50`、`frontend/skill-picker.js:75` | `rank`、`.skill-picker` | 无 | 手动 · 1280×800 · 分别输入部分匹配与无匹配关键词 |
| `composer.skill-keys` | 按方向键、Enter 或 Escape → 导航、选择或关闭菜单 | `frontend/skill-picker.js:83` | `CrewSkillPicker.keydown` | 无 | 手动 · 1280×800 · 键盘选第二项，再开面板按 Escape |
| `composer.skill-pick` | 选择技能 → 替换光标处技能词并回焦 | `frontend/skill-picker.js:65`、`frontend/skill-picker.js:96` | `pick`、`selectionStart` | 无 | 手动 · 1280×800 · 在句中插入并核对余文与光标 |
| `composer.skill-refresh` | 点刷新技能 → 重新发现技能并更新菜单 | `frontend/skill-picker.js:33`、`frontend/skill-picker.js:96` | `load(refresh)`、`refresh=1` | 无 | 手动 · 1280×800 · 点刷新并模拟请求失败 |
| `composer.native-undo-list` | 自动续列表后按原生撤销 → 回退生成的列表符号 | `frontend/browser-utils.js:67` | `execCommand`、`insertText` | 无 | 截图走查 · 1280px 输入 1. 文本后 Shift+Enter，再按 Ctrl／⌘+Z |
| `composer.list-code-fence` | 代码围栏里换行 → 保留普通换行，不生成列表符号 | `frontend/browser-utils.js:39`、`frontend/browser-utils.js:58` | `insideCodeFence` | 无 | 手动 · 1280×800 · 代码围栏里换行，核对保留普通换行，不生成列表符号 |

### 任务执行、交付与验收

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `task.pane-toggle` | 点击对话头部任务按钮 → 显示或收起任务栏，详情中返回任务列表 | `frontend/app.js:1499` | `data-act=tasks-toggle`、`#task-list` | `plugin/tests/test_goal_ui.js::floating list owns creation and detail owns the task controls` | 截图走查 · 在 1280 和 375 的总对话及任务详情分别点击 |
| `task.pane-back` | 点任务详情返回 → 回到该 Leader 的任务清单 | `frontend/app.js:1500` | `data-act=tasks-back` | 无 | 手动 · 1280×800 · 点任务详情返回，核对回到该 Leader 的任务清单 |
| `task.list-groups` | 查看任务清单 → 按待你验收、观察中、进行中、已结束分组并计数 | `frontend/goal-ui.js:148` | `.task-list-group`、`.task-list-head` | `plugin/tests/test_goal_ui.js::task list groups review, active and expanded ended goals and omits empty groups` | 手动 · 1280×800 · 各放一项，核对计数和排序 |
| `task.ended-toggle` | 点已结束分组 → 折叠或展开并记住该 Leader 的状态 | `frontend/goal-ui.js:349` | `data-goal-act=ended-toggle`、`endedClosed` | `plugin/tests/test_goal_ui.js::task list groups review, active and expanded ended goals and omits empty groups` | 手动 · 1280×800 · 点已结束分组，核对折叠或展开并记住该 Leader 的状态 |
| `task.open` | 点任务卡 → 进入对应任务详情 | `frontend/goal-ui.js:328` | `data-goal-act=open` | `plugin/tests/test_goal_ui.js::selecting a goal opens floating details and marks its leader task row` | 手动 · 1280×800 · 点任务卡，核对进入对应任务详情 |
| `task.create-dialog` | 点建立目标 → 聚焦标题并显示需求及逐行验收标准表单 | `frontend/goal-ui.js:304` | `data-goal-create` | `plugin/tests/test_goal_ui.js::floating list owns creation and detail owns the task controls` | 手动 · 1280×800 · 点建立目标，核对聚焦标题并显示需求及逐行验收标准表单 |
| `task.create-submit` | 提交表单 → 去掉空验收行，打开新任务或显示错误 | `frontend/goal-ui.js:317` | `/api/goals/create` | `plugin/tests/test_goal_actions.py::test_create_with_start_begins_work_and_without_start_only_queues` | 手动 · 1280×800 · 填两条标准及一空行后提交，再模拟拒绝 |
| `task.mode` | 点任务的由我拍板或自主推进 → 保存该任务决策方式并显示提示 | `frontend/goal-ui.js:331` | `/api/goals/agent`、`mode.set` | `plugin/tests/test_goal_modes_reports.py::test_new_goals_ignore_retired_leader_trust_and_mode_changes_are_only_configuration` | 手动 · 1280×800 · 点任务的由我拍板或自主推进，核对保存该任务决策方式并显示提示 |
| `task.start` | 点开始推进 → 提交排队目标的启动请求并刷新状态 | `frontend/goal-ui.js:162`、`frontend/goal-ui.js:359` | `/api/goals/action`、`data-action=start` | `plugin/tests/test_goal_http.py::test_start_button_launches_new_goal_once_without_chat_send` | 手动 · 1280×800 · 点开始推进，核对提交排队目标的启动请求并刷新状态 |
| `task.resume` | 点继续推进 → 暂停或等用户目标恢复推进 | `frontend/goal-ui.js:162`、`frontend/goal-ui.js:359` | `/api/goals/action`、`data-action=resume` | `plugin/tests/test_goal_ui.js::goal details show structured acceptance titles, newest report and explicit reopen or resume actions`；`plugin/tests/test_goal_http.py::test_claude_paused_resume_and_message_keep_round_execution_and_history` | 手动 · 1280×800 · 点继续推进，核对暂停或等用户目标恢复推进 |
| `task.pause` | 点暂停目标 → 显示处理中及随后暂停状态 | `frontend/goal-ui.js:162`、`frontend/goal-ui.js:359` | `/api/goals/action`、`data-action=pause` | `plugin/tests/test_goal_http.py::test_pause_is_durable_before_interrupt_and_late_native_hooks_cannot_reopen` | 手动 · 1280×800 · 点暂停目标，核对显示处理中及随后暂停状态 |
| `task.cancel-dialog` | 点取消目标 → 弹出说明并聚焦返回按钮 | `frontend/goal-ui.js:270`、`frontend/goal-ui.js:359` | `data-goal-confirm=cancel` | `plugin/tests/test_goal_ui.js::cancel needs explicit confirmation and reject requires a preserved human note` | 手动 · 1280×800 · 点取消目标，核对弹出说明并聚焦返回按钮 |
| `task.cancel-commit` | 确认取消 → 执行停止，保留对话、文档和交付记录 | `frontend/goal-ui.js:313` | `/api/goals/action`、`data-goal-confirm` | 无 | 手动 · 1280×800 · 确认取消，核对执行停止，保留对话、文档和交付记录 |
| `task.transfer-dialog` | 点交给其他 Leader → 显示候选负责人和可选交接说明 | `frontend/goal-ui.js:296` | `data-goal-transfer` | 无 | 手动 · 1280×800 · 点交给其他 Leader，核对显示候选负责人和可选交接说明 |
| `task.transfer-commit` | 确认交接 → 执行中保留交接中状态，完成后跟随到新负责人 | `frontend/goal-ui.js:286`、`frontend/goal-ui.js:315` | `/api/goals/action`、`transfer_pending` | `plugin/tests/test_goal_transfer.py::test_running_task_waits_for_the_turn_then_moves_people_work_and_branches` | 手动 · 1280×800 · 对空闲和执行中任务分别交接并检查记录 |
| `task.review-wait` | 查看待验收或观察中任务 → 显示状态、验收动作及可选怎么实测 | `frontend/goal-ui.js:168` | `.task-review-banner`、`.task-user-test` | 无 | 手动 · 1280×800 · 查看待验收或观察中任务，核对显示状态、验收动作及可选怎么实测 |
| `task.accept` | 点通过 → 任务完成并提示验收通过 | `frontend/goal-ui.js:281`、`frontend/goal-ui.js:354` | `data-review=accept`、`report_id` | `plugin/tests/test_goal_ui.js::review buttons decide on the shown report from the home card, the task banner or the reader` | 手动 · 1280×800 · 点通过，核对任务完成并提示验收通过 |
| `task.observe` | 点先观察 → 任务转为观察中，仍可通过或退回 | `frontend/goal-ui.js:281`、`frontend/goal-ui.js:354` | `data-review=observe` | `plugin/tests/test_goal_ui.js::review buttons decide on the shown report from the home card, the task banner or the reader`；`plugin/tests/test_goal_acceptance.py::test_observing_keeps_the_task_open_until_the_user_decides` | 手动 · 1280×800 · 点先观察，核对任务转为观察中，仍可通过或退回 |
| `task.reject-dialog` | 点需修改 → 弹出必填理由并聚焦输入框 | `frontend/goal-ui.js:270`、`frontend/goal-ui.js:354` | `data-review=reject`、`data-goal-confirm` | `plugin/tests/test_goal_ui.js::cancel needs explicit confirmation and reject requires a preserved human note` | 手动 · 1280×800 · 点需修改，核对弹出必填理由并聚焦输入框 |
| `task.reject-submit` | 填理由提交 → 在原任务续做，保留原报告和验收记录，续上失败给出提示 | `frontend/goal-ui.js:283`、`frontend/goal-ui.js:313` | `/api/goals/action`、`action=reject` | `plugin/tests/test_goal_ui.js::cancel needs explicit confirmation and reject requires a preserved human note`；`plugin/tests/test_goal_http.py::test_rejection_that_cannot_resume_the_task_tells_the_leader_in_general_chat`；`plugin/tests/test_goal_acceptance.py::test_rejection_requires_a_note_and_continues_in_the_same_round` | 手动 · 1280×800 · 分别提交空原因、成功及续上失败夹具 |
| `task.previous-review` | 展开之前的验收记录 → 显示评语与原始报告保留说明 | `frontend/goal-ui.js:174` | `.goal-review` | `plugin/tests/test_goal_ui.js::delivered tasks invite direct follow-up and a new round keeps the earlier human review visible`；`plugin/tests/test_goal_acceptance.py::test_human_acceptance_completes_without_altering_the_ai_report` | 手动 · 1280×800 · 展开之前的验收记录，核对显示评语与原始报告保留说明 |
| `task.action-error` | 提交命中版本冲突 → 展示实际错误并重读任务 | `frontend/goal-ui.js:293` | `expected_version`、`.goal-action-error` | `plugin/tests/test_goal_ui.js::task actions show pending and retained failure feedback and allow a deliberate retry` | 手动 · 1280×800 · 两个窗口操作同任务旧版本 |
| `task.dialog-close` | 在任务弹窗点击返回或按 Escape → 关闭并恢复相关操作焦点 | `frontend/goal-ui.js:351`、`frontend/goal-ui.js:364` | `.goal-create-form`、`restoreFocus` | 无 | 手动 · 1280×800 · 在任务弹窗点击返回或按 Escape，核对关闭并恢复相关操作焦点 |
| `task.dialog-tab` | 任务表单首尾按 Tab 或 Shift+Tab → 焦点循环 | `frontend/goal-ui.js:370` | `formOverlay` | 无 | 手动 · 1280×800 · 任务表单首尾按 Tab 或 Shift+Tab，核对焦点循环 |
| `task.member-exec` | 点进度里的成员执行 → 打开该成员在这项任务的对话 | `frontend/goal-ui.js:330` | `data-goal-act=member-exec` | 无 | 手动 · 1280×800 · 点进度里的成员执行，核对打开该成员在这项任务的对话 |
| `task.leader-exec` | 点执行者卡片 → 在 Leader 总对话与该任务执行对话之间切换 | `frontend/goal-ui.js:329`、`frontend/goal-ui.js:186`、`frontend/app.js:1473` | `data-goal-act=exec-chat`、`aria-pressed` | `plugin/tests/test_goal_ui.js::execution selection keeps the same round and newest generation across amendments` | 手动 · 1280×800 · 点执行者卡片，核对在 Leader 总对话与该任务执行对话之间切换 |
| `task.leader-general` | 点回到总对话 → 清除执行模式并回到总对话 | `frontend/app.js:1620` | `data-act=leader-general` | 无 | 手动 · 1280×800 · 点回到总对话，核对清除执行模式并回到总对话 |
| `task.member-scope` | 切换成员任务范围 → 查看该成员在指定任务的记录 | `frontend/app.js:1619` | `data-act=member-scope`、`memberScopes` | `plugin/tests/test_chat_connection.js::a member page can show one task execution record and talk to that task` | 手动 · 1280×800 · 切换成员任务范围，核对查看该成员在指定任务的记录 |
| `task.refresh` | 任务读取失败后点重新读取 → 重试加载当前详情 | `frontend/goal-ui.js:352` | `data-goal-act=refresh` | `plugin/tests/test_goal_ui.js::failed detail and report reads stay visible with a deliberate retry` | 手动 · 1280×800 · 任务读取失败后点重新读取，核对重试加载当前详情 |
| `task.delivery` | 点看完整报告 → 打开当前交付的阅读器 | `frontend/goal-ui.js:251`、`frontend/goal-ui.js:340` | `data-goal-act=delivery` | `plugin/tests/test_goal_ui.js::one delivery click from the list opens the selected goal's current report in the isolated reader and returns to the task` | 手动 · 1280×800 · 点看完整报告，核对打开当前交付的阅读器 |
| `task.retry-report` | 报告加载失败后点重试 → 重新读取报告正文 | `frontend/goal-ui.js:265`、`frontend/goal-ui.js:347` | `data-goal-act=retry-report` | `plugin/tests/test_goal_ui.js::failed detail and report reads stay visible with a deliberate retry` | 手动 · 1280×800 · 报告加载失败后点重试，核对重新读取报告正文 |
| `task.report-close` | 点关闭报告 → 返回任务并聚焦交付入口 | `frontend/goal-ui.js:269`、`frontend/goal-ui.js:353` | `data-goal-act=close-report` | `plugin/tests/test_goal_ui.js::one delivery click from the list opens the selected goal's current report in the isolated reader and returns to the task` | 手动 · 1280×800 · 点关闭报告，核对返回任务并聚焦交付入口 |
| `task.open-folder` | 点产物文件夹 → 定位报告产物，失败显示原因 | `frontend/goal-ui.js:341` | `/api/goals/report/folder` | `plugin/tests/test_goal_ui.js::every report offers 打开文件夹 on this computer only, naming the folder it opens` | 手动 · 1280×800 · 点产物文件夹，核对定位报告产物，失败显示原因 |
| `task.conclusion-fold` | 点结论摘要 → 独立切换当前报告结论展开态 | `frontend/goal-ui.js:348` | `data-goal-act=conclusion-toggle` | 无 | 手动 · 1280×800 · 点结论摘要，核对独立切换当前报告结论展开态 |
| `task.notes-fold` | 点报告说明 → 独立切换当前报告说明展开态 | `frontend/goal-ui.js:348` | `data-goal-act=report-notes-toggle` | 无 | 手动 · 1280×800 · 点报告说明，核对独立切换当前报告说明展开态 |
| `task.supplements` | 点补充材料 → 展开或收起该任务或首页集合 | `frontend/goal-ui.js:339` | `data-goal-act=supplements-toggle` | `plugin/tests/test_goal_ui.js::acceptance supplements stay folded, expand in version order and preserve authors and original text` | 手动 · 1280×800 · 点补充材料，核对展开或收起该任务或首页集合 |
| `task.stage-edit` | 点编辑阶段说明 → 编辑并保存为新版本 | `frontend/goal-ui.js:338`、`frontend/stage-report-ui.js:5`、`frontend/stage-report-ui.js:15-16` | `data-goal-act=edit-stage`、`CrewStageReport` | `plugin/tests/test_goal_modes_reports.py::test_publish_retry_user_edits_and_final_delivery_remain_distinct` | 手动 · 1280×800 · 点编辑阶段说明，核对编辑并保存为新版本 |
| `task.work-checklist` | 展开工作清单项 → 显示条件、负责人、依赖或复核说明 | `frontend/goal-work-ui.js:5` | `.goal-work`、`data-work-status` | 无 | 手动 · 1280×800 · 展开工作清单项，核对显示条件、负责人、依赖或复核说明 |
| `task.work-progress` | 查看工作清单进度 → 显示实际完成数及未取消总数 | `frontend/goal-work-ui.js:7` | `.goal-work header` | `plugin/tests/test_goal_ui.js::task cards use status-specific summaries, actual checklist progress and conditional metadata` | 手动 · 1280×800 · 查看工作清单进度，核对显示实际完成数及未取消总数 |
| `task.stage-draft` | 编辑阶段说明后关闭再打开 → 恢复该目标该报告的未保存内容 | `frontend/stage-report-ui.js:8-15` | `drafts`、`goal.id`、`report.id` | 无 | 手动 · 1280×800 · 编辑阶段说明后关闭再打开，核对恢复该目标该报告的未保存内容 |
| `task.stage-save-error` | 保存阶段说明失败 → 草稿保留，显示错误并可再次提交 | `frontend/stage-report-ui.js:15-18` | `request_id`、`expected_version` | 无 | 手动 · 1280×800 · 保存阶段说明失败，核对草稿保留，显示错误并可再次提交 |
| `task.stage-close` | 点击关闭或按 Escape → 收起阶段说明编辑器，保存中的操作保持锁定 | `frontend/stage-report-ui.js:7` | `data-close`、`dialog.oncancel` | 无 | 手动 · 1280×800 · 点击关闭或按 Escape，核对收起阶段说明编辑器，保存中的操作保持锁定 |

### 边看边聊与任务文档

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `reader.layout` | 打开报告或文档 → 宽屏并排，窄屏上下堆叠，收起聊天可进入全宽 | `frontend/reader-ui.js:30`、`frontend/reader-ui.js:55` | `CrewReader`、`.reader-active` | 无 | 截图走查 · 1280px、760、375 分别打开同一报告 |
| `reader.chat-toggle` | 点显示或收起对话 → 在全宽阅读和边看边聊间切换 | `frontend/reader-ui.js:110`、`frontend/reader-ui.js:148` | `toggleChat`、`data-reader-act=chat` | 无 | 手动 · 1280×800 · 宽屏打开文档后收起再展开 |
| `reader.drag-divider` | 拖分隔条 → 调整对话宽度并保存 | `frontend/reader-ui.js:126` | `.reader-divider`、`crew.readerChatWidth` | 无 | 截图走查 · 1280px 拖动后关闭再打开 |
| `reader.reset-divider` | 双击分隔条 → 恢复默认宽度 | `frontend/reader-ui.js:140` | `.reader-divider`、`CHAT_DEFAULT` | 无 | 手动 · 1280×800 · 双击分隔条，核对恢复默认宽度 |
| `reader.keys-divider` | 聚焦分隔条按左右键 → 按 24px 步长调整对话宽度并保存 | `frontend/reader-ui.js:141` | `.reader-divider`、`resizeTo` | 无 | 手动 · 1280×800 · 聚焦分隔条按左右键，核对按键盘步长调整宽度 |
| `reader.escape` | 阅读时按 Escape → 关闭当前阅读层并回到原入口 | `frontend/reader-ui.js:89`、`frontend/goal-ui.js:364` | `CrewReader.escape` | 无 | 手动 · 1280×800 · 阅读时按 Escape，核对关闭当前阅读层并回到原入口 |
| `reader.quote-selection` | 选正文文字并点引用 → 将多行引文附加至编辑框并回焦 | `frontend/reader-ui.js:156`、`frontend/reader-ui.js:178`、`frontend/chat.js:396` | `selectionchange`、`CrewChat.quote` | 无 | 手动 · 1280×800 · 先输入草稿，再选择跨行文字引用 |
| `reader.quote-frame` | 在嵌入报告内选文本 → 宿主侧出现引用按钮 | `frontend/reader-ui.js:190`、`frontend/reader-ui.js:201` | `selectionProbe`、`postMessage` | 无 | 手动 · 1280×800 · 在报告 iframe 选两行文字再点击引用 |
| `reader.documents` | 从任务文档入口打开文档 → 进入对应内容阅读器 | `frontend/documents.js:18`、`frontend/documents.js:44`、`frontend/chat.js:257` | `CrewDocuments` | `plugin/tests/test_documents_ui.js::beside a conversation a document opens in the reading pane and names itself for the composer` | 手动 · 1280×800 · 在有文档的任务详情逐一打开文档 |
| `reader.document-pin` | 点星标 → 设置或取消该任务文件重要标记 | `frontend/documents.js:44` | `data-doc-act=pin`、`data-doc-id` | `plugin/tests/test_documents_ui.js::task document actions bind numeric document IDs and retain the important mark without a hidden center` | 手动 · 1280×800 · 点星标，核对设置或取消该任务文件重要标记 |
| `reader.document-pin-reset` | 点恢复自动标记 → 取消用户覆盖以恢复自动标识 | `frontend/documents.js:44` | `data-doc-act=reset-pin` | 无 | 手动 · 1280×800 · 点恢复自动标记，核对取消用户覆盖以恢复自动标识 |
| `reader.document-direct-open` | 点文件卡打开文件 → 在本机以系统方式打开 | `frontend/documents.js:44` | `data-doc-act=open` | 无 | 手动 · 1280×800 · 点文件卡打开文件，核对在本机以系统方式打开 |
| `reader.document-card-download` | 在远程文件卡点下载到本机 → 取回该文档 | `frontend/documents.js:44` | `data-doc-act=download` | 无 | 手动 · 1280×800 · 在远程文件卡点下载到本机，核对取回该文档 |
| `reader.document-source` | 点回到来源对话 → 返回提交者对应的会话或任务 | `frontend/documents.js:44` | `data-doc-act=source`、`data-doc-scope` | 无 | 手动 · 1280×800 · 点回到来源对话，核对返回提交者对应的会话或任务 |
| `reader.divider-home` | 聚焦分隔线后按 Home → 将对话宽度缩到最小并保存 | `frontend/reader-ui.js:145` | `.reader-divider`、`CHAT_MIN` | 无 | 手动 · 1280×800 · 聚焦分隔线后按 Home，核对将对话宽度缩到最小并保存 |
| `reader.divider-end` | 聚焦分隔线后按 End → 将对话宽度增到最大并保存 | `frontend/reader-ui.js:146` | `.reader-divider`、`READER_MIN` | 无 | 手动 · 1280×800 · 聚焦分隔线后按 End，核对将对话宽度增到最大并保存 |
| `reader.remember-mode` | 桌面切换阅读时是否显示聊天 → 下次打开按偏好恢复 | `frontend/reader-ui.js:110-115` | `crew.readerChat`、`current.chat` | 无 | 手动 · 1280×800 · 桌面切换阅读时是否显示聊天，核对下次打开按偏好恢复 |
| `reader.restore-leader` | 打开阅读器再关闭 → 临时让出 Leader 栏并恢复原状态 | `frontend/app.js:1464-1468` | `CrewReader.configure`、`collapseLeaders`、`restoreLeaders` | 无 | 手动 · 1280×800 · 打开阅读器再关闭，核对临时让出 Leader 栏并恢复原状态 |

### 待关注、人工待办与拍板

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `attention.presence` | 打开在线状态浮层 → 可查看状态，Escape 关闭并回焦 | `frontend/app.js:1504`、`frontend/app.js:1731` | `data-act=presence-toggle`、`#presence-entry` | 无 | 手动 · 1280×800 · 用键盘打开关闭 |
| `attention.away` | 切换离开状态 → 刷新当前通知状态 | `frontend/app.js:1594` | `/api/notifications/away` | `plugin/tests/test_connections.py::test_disconnect_reconnect_reset_binding_and_away_survive_reload` | 手动 · 1280×800 · 点击离开再恢复 |
| `attention.toggle` | 点击待关注入口 → 展开或收起待关注面板 | `frontend/app.js:1600` | `data-act=attention` | 无 | 截图走查 · 1280px 和 375 分别切换 |
| `attention.section-close` | 点击待关注分区关闭 → 隐藏分区并把焦点还给顶部入口 | `frontend/app.js:1622` | `data-act=side-section-close` | 无 | 手动 · 1280×800 · 打开分区后关闭 |
| `attention.todo-expand` | 点击人工待办标题 → 展开或收起详情 | `frontend/app.js:1561` | `data-act=todo-toggle` | `plugin/tests/test_runtime_ui.js::personal action cards escape titles and show source, blocking, expandable steps and verification` | 手动 · 1280×800 · 用有多条待办的工作区逐条展开 |
| `attention.todo-action` | 选处理状态 → 展开备注表单并聚焦，保留已有备注 | `frontend/app.js:1562` | `todoActions`、`#todo-note-ID` | `plugin/tests/test_runtime_ui.js::a stale action retains the user's reason and asks them to review updated content` | 手动 · 1280×800 · 填写备注后切另一处理状态 |
| `attention.todo-cancel` | 取消处理 → 收起备注处理表单 | `frontend/app.js:1569` | `data-act=todo-action-close` | 无 | 手动 · 1280×800 · 打开处理表单后取消 |
| `attention.todo-submit` | 提交处理状态与备注 → 保存处理结果或显示冲突错误 | `frontend/app.js:1690` | `data-todo-form`、`resolveTodo` | `plugin/tests/test_runtime_ui.js::resolving a personal action posts its version and undo uses the exact server receipt` | 手动 · 1280×800 · 用隔离数据提交，另用旧版本触发冲突 |
| `attention.todo-ask` | 点找负责人 → 跳到归属 Leader 的对话 | `frontend/app.js:1570` | `data-act=todo-ask` | 无 | 手动 · 1280×800 · 从有归属的待办进入并核对标题 |
| `attention.decision-fold` | 展开拍板或背景 → 刷新后保持各卡片展开状态 | `frontend/app.js:1584`、`frontend/app.js:1708` | `decisionsOpen`、`sideDecisionsOpen` | `plugin/tests/test_runtime_ui.js::legacy decisions expose missing explanations, compact the title and fold the full source without inventing reasons` | 手动 · 1280×800 · 展开两张卡后等轮询 |
| `attention.decision-clarify` | 点请补充说明 → 暂时禁用按钮并显示请求结果 | `frontend/app.js:1585` | `/api/decision/clarify` | 无 | 手动 · 1280×800 · 使用待拍板夹具点击一次 |
| `attention.decision-discuss` | 点讨论 → 进入正确的任务讨论或总对话 | `frontend/app.js:1586` | `/api/decision/discuss`、`conversation_goal_id` | `plugin/tests/test_runtime_ui.js::a decision first invites discussion and exposes structured choices only after ready` | 手动 · 1280×800 · 分别从有无任务归属的拍板进入 |
| `attention.decision-ready` | 点准备好 → 更新拍板状态 | `frontend/app.js:1586` | `/api/decision/ready` | `plugin/tests/test_runtime_ui.js::a decision first invites discussion and exposes structured choices only after ready` | 手动 · 1280×800 · 打开讨论后选择准备好 |
| `attention.decision-option` | 点预设选项 → 提交对应拍板答案 | `frontend/app.js:1593` | `data-act=answer-option` | `plugin/tests/test_runtime_ui.js::decision briefs show human context, rationale and independent consequences while preserving original choice indices` | 手动 · 1280×800 · 在夹具中选择第二项 |
| `attention.decision-free` | 填写自定义答案并提交 → 发送非空拍板回复 | `frontend/app.js:1691` | `data-answer-form` | 无 | 手动 · 1280×800 · 提交空白再提交带内容答案 |
| `attention.goal-review` | 点击任务验收提醒 → 打开任务并聚焦交付入口，任务消失时提示刷新 | `frontend/app.js:1601` | `data-act=goal-review`、`taskDeliveryFocus` | 无 | 手动 · 1280×800 · 分别使用有效及已移除任务 |
| `attention.connection-link` | 在在场浮层点连接设置 → 打开消息连接设置 | `frontend/app.js:416`、`frontend/app.js:1505` | `data-act=connection-settings` | 无 | 手动 · 1280×800 · 在在场浮层点连接设置，核对打开消息连接设置 |
| `attention.presence-status` | 消息渠道断开或报错时查看在场入口 → 显示连接状态，离开时新增故障闪动 | `frontend/app.js:409`、`frontend/app.js:415` | `.presence-dot`、`.presence-flash` | 无 | 手动 · 1280×800 · 消息渠道断开或报错时查看在场入口，核对显示连接状态，离开时新增故障闪动 |
| `attention.scope-count` | 会话待关注事项变化 → 顶部出现对应数量和可读标签 | `frontend/app.js:744` | `.attention-count`、`aria-label` | 无 | 手动 · 1280×800 · 会话待关注事项变化，核对顶部出现对应数量和可读标签 |
| `attention.todo-undo` | 处理个人待办后点撤销 → 使用回执恢复原状态 | `frontend/app.js:801` | `resolveTodo`、`toast` | `plugin/tests/test_runtime_ui.js::resolving a personal action posts its version and undo uses the exact server receipt` | 手动 · 1280×800 · 处理一条带版本的待办并撤销 |
| `attention.todo-linked-task` | 点待办的关联任务 → 打开对应任务 | `frontend/app.js:779` | `.todo-linked-tasks`、`data-goal-act=open` | 无 | 手动 · 1280×800 · 点待办的关联任务，核对打开对应任务 |
| `attention.todo-done` | 点做完了 → 打开可选完成备注 | `frontend/app.js:780`、`frontend/app.js:1562` | `data-status=done` | 无 | 手动 · 1280×800 · 点做完了，核对打开可选完成备注 |
| `attention.todo-decline` | 点不做了 → 打开可选原因输入 | `frontend/app.js:780`、`frontend/app.js:1562` | `data-status=cancelled` | 无 | 手动 · 1280×800 · 点不做了，核对打开可选原因输入 |
| `attention.toast-dismiss` | 操作得到普通或可撤销提示 → 提示分别在约五或十二秒后移除 | `frontend/app.js:209-212` | `#toast-root`、`role=status` | 无 | 手动 · 1280×800 · 触发两种结果并观察持续时间 |

### 原生执行权限审批

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `approval.expand` | 展开权限卡 → 查看申请人、归属、原因及许可范围，刷新保留展开态 | `frontend/native-approval-ui.js:22`、`frontend/native-approval-ui.js:33` | `data-native-approval-id` | `plugin/tests/test_native_approval_ui.js::native approvals are scoped, escaped and only display a single-command choice without any automatic request` | 手动 · 1280×800 · 展开权限卡，核对查看申请人、归属、原因及许可范围，刷新保留展开态 |
| `approval.command-details` | 展开命令与路径 → 查看命令、工作目录和受影响路径 | `frontend/native-approval-ui.js:22`、`frontend/native-approval-ui.js:33` | `data-native-command-details` | `plugin/tests/test_native_approval_ui.js::native approvals are scoped, escaped and only display a single-command choice without any automatic request` | 手动 · 1280×800 · 展开命令与路径，核对查看命令、工作目录和受影响路径 |
| `approval.allow-once` | 点允许这一次 → 提交单次授权并刷新条目 | `frontend/native-approval-ui.js:25`、`frontend/native-approval-ui.js:32` | `/api/native-approval`、`data-native-approval-act=allow` | `plugin/tests/test_native_approval_ui.js::failed human approval stays expanded and retryable, then successful receipt removes it`；`plugin/tests/test_native_approvals.py::test_real_http_binds_project_and_allows_approve_or_deny_once` | 手动 · 1280×800 · 点允许这一次，核对提交单次授权并刷新条目 |
| `approval.deny` | 点拒绝 → 提交拒绝并刷新条目 | `frontend/native-approval-ui.js:25`、`frontend/native-approval-ui.js:32` | `/api/native-approval`、`data-native-approval-act=deny` | `plugin/tests/test_native_approvals.py::test_real_http_binds_project_and_allows_approve_or_deny_once` | 手动 · 1280×800 · 点拒绝，核对提交拒绝并刷新条目 |
| `approval.error` | 提交审批失败 → 卡内保留原因并显示提示 | `frontend/native-approval-ui.js:29` | `.native-approval-error` | `plugin/tests/test_native_approval_ui.js::failed human approval stays expanded and retryable, then successful receipt removes it` | 手动 · 1280×800 · 提交审批失败，核对卡内保留原因并显示提示 |

### Leader 交流区

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `channel.targets-one` | 点名一位 Leader → 切换该人选中态 | `frontend/channel.js:216` | `data-channel-to`、`selected` | `plugin/tests/test_leader_channel_http.py::test_user_post_and_mention` | 手动 · 1280×800 · 点名一位 Leader，核对切换该人选中态 |
| `channel.targets-all` | 点全体 → 切换全选并清空逐人选择 | `frontend/channel.js:220` | `data-channel-to=*`、`allLeaders` | 无 | 手动 · 1280×800 · 点全体，核对切换全选并清空逐人选择 |
| `channel.targets-except` | 全选时点某位 Leader → 改为选中其余所有人 | `frontend/channel.js:221` | `allLeaders`、`selected` | 无 | 手动 · 1280×800 · 全选时点某位 Leader，核对改为选中其余所有人 |
| `channel.send` | 点发送 → 公开发言进入交流区，点名时显示叫醒对象 | `frontend/channel.js:176`、`frontend/channel.js:233` | `/api/channel`、`.channel-send` | `plugin/tests/test_leader_channel_http.py::test_user_post_and_mention` | 手动 · 1280×800 · 以无点名、一个点名、全体三种方式提交 |
| `channel.send-key` | 输入中按 Enter → 桌面发送，组合输入保持，Shift+Enter 换行 | `frontend/channel.js:228` | `CrewBrowser.chatSendKey`、`compositionstart` | 无 | 手动 · 1280×800 · 输入中按 Enter，核对桌面发送，组合输入保持，Shift+Enter 换行 |
| `channel.send-edit` | 发送未返回时继续输入 → 成功回包保留后续输入 | `frontend/channel.js:185` | `context.version` | 无 | 手动 · 1280×800 · 延迟回包后输入新文字 |
| `channel.send-error` | 交流发送失败 → 显示原因并保留输入供重试 | `frontend/channel.js:190` | `context.toast`、`composerState` | 无 | 手动 · 1280×800 · 交流发送失败，核对显示原因并保留输入供重试 |
| `channel.lists` | 在交流区列表换行 → 续列表或结束空项 | `frontend/channel.js:203` | `CrewBrowser.enableChatLists` | 无 | 手动 · 1280×800 · 在交流区列表换行，核对续列表或结束空项 |
| `channel.mention-status` | 查看发言中的点名条目 → 显示各人的送达状态与原因 | `frontend/channel.js:23-28` | `.channel-mentions`、`mentionHTML` | `plugin/tests/test_leader_channel_http.py::test_unknown_mention_and_invalid_read_fail_without_write` | 手动 · 1280×800 · 对照回放各状态 |
| `channel.quote-jump` | 点击回复摘要 → 滚到并高亮原发言，缺失时提示 | `frontend/channel.js:35`、`frontend/channel.js:210` | `data-channel-jump`、`.channel-flash` | 无 | 手动 · 1280×800 · 点击回复摘要，核对滚到并高亮原发言，缺失时提示 |
| `channel.private-detail` | 点私聊详情 → 打开发言者会话并定位时间或用户原话 | `frontend/channel.js:41`、`frontend/channel.js:211` | `data-channel-detail`、`openChat` | `plugin/tests/test_leader_channel_http.py::test_user_channel_words_show_in_private_as_the_users_message` | 手动 · 1280×800 · 点私聊详情，核对打开发言者会话并定位时间或用户原话 |
| `channel.older` | 点加载更早的 → 分页前插并保留滚动位置 | `frontend/channel.js:139`、`frontend/channel.js:204` | `.channel-older`、`/api/channel?before` | `plugin/tests/test_leader_channel_http.py::test_pagination_and_read_state` | 手动 · 1280×800 · 点加载更早的，核对分页前插并保留滚动位置 |
| `channel.digest-request` | 点整理 → 禁用入口并显示整理中，失败提示原因 | `frontend/channel.js:76`、`frontend/channel.js:199` | `/api/channel/digest`、`.channel-pin` | `plugin/tests/test_leader_channel_http.py::test_digest_http_no_new_concurrent_and_success_fields` | 手动 · 1280×800 · 点整理，核对禁用入口并显示整理中，失败提示原因 |
| `channel.digest-fold` | 展开或收起要点 → 查看摘要、累计条数、模型及费用 | `frontend/channel.js:60` | `.channel-digest` | `plugin/tests/test_channel_runtime_ui.js::digest metadata shows the actual Codex model and unknown fees without implying a zero charge` | 手动 · 1280×800 · 展开或收起要点，核对查看摘要、累计条数、模型及费用 |
| `channel.originals-toggle` | 点已整理的原始发言 → 展开或收起，并首次加载列表 | `frontend/channel.js:206` | `.channel-originals-toggle` | 无 | 手动 · 1280×800 · 点已整理的原始发言，核对展开或收起，并首次加载列表 |
| `channel.originals-older` | 点已整理发言中的加载更早 → 继续前插原始发言 | `frontend/channel.js:89`、`frontend/channel.js:209` | `.channel-originals-older` | 无 | 手动 · 1280×800 · 点已整理发言中的加载更早，核对继续前插原始发言 |
| `channel.reading` | 滚到底部或切回标签 → 符合条件时同步已读 | `frontend/channel.js:108`、`frontend/channel.js:213-215` | `markRead`、`visibilitychange` | `plugin/tests/test_leader_channel_http.py::test_pagination_and_read_state` | 手动 · 1280×800 · 滚到底部或切回标签，核对符合条件时同步已读 |
| `channel.refresh-position` | 交流有新发言 → 底部跟随，向上阅读时保留位置 | `frontend/channel.js:152`、`frontend/channel.js:168` | `refresh`、`atBottom` | 无 | 手动 · 1280×800 · 交流有新发言，核对底部跟随，向上阅读时保留位置 |

### 架构图

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `architecture.load-retry` | 架构图读取失败后点重读 → 重新请求架构快照 | `frontend/architecture.js:293`、`frontend/architecture.js:793` | `/api/domains`、`data-arch-act=reload` | 无 | 手动 · 1280×800 · 架构图读取失败后点重读，核对重新请求架构快照 |
| `architecture.new` | 点新建 part → 在画布增加节点 | `frontend/architecture.js:781` | `data-arch-act=new` | `plugin/tests/test_domains.py::test_board_round_trip_paths_in_state_and_conflicts` | 手动 · 1280×800 · 点新建 part，核对在画布增加节点 |
| `architecture.new-here` | 在空白处右键选择在这里新建 → 节点落在菜单对应的画布位置 | `frontend/architecture.js:774` | `data-cmd=here` | 无 | 手动 · 1280×800 · 在空白处右键选择在这里新建，核对节点落在菜单对应的画布位置 |
| `architecture.new-child` | 节点菜单选新建子 part → 创建并连接子节点 | `frontend/architecture.js:769` | `data-cmd=child` | 无 | 手动 · 1280×800 · 节点菜单选新建子 part，核对创建并连接子节点 |
| `architecture.rename-start` | 用菜单、双击标题或 F2 → 打开节点内联编辑框 | `frontend/architecture.js:768`、`frontend/architecture.js:795`、`frontend/architecture.js:817` | `.arch-rename` | 无 | 手动 · 1280×800 · 用菜单、双击标题或 F2，核对打开节点内联编辑框 |
| `architecture.rename-finish` | 按 Enter 或失焦 → 保存新名，Escape 取消 | `frontend/architecture.js:803`、`frontend/architecture.js:822` | `finishRename` | `plugin/tests/test_domains.py::test_tree_paths_layout_direction_and_version` | 手动 · 1280×800 · 按 Enter 或失焦，核对保存新名，Escape 取消 |
| `architecture.fold` | 点折叠箭头或菜单 → 隐藏或恢复该节点子树与成员 | `frontend/architecture.js:421`、`frontend/architecture.js:770`、`frontend/architecture.js:791` | `data-arch-act=fold`、`collapse` | `plugin/tests/test_domains.py::test_tree_paths_layout_direction_and_version` | 手动 · 1280×800 · 点折叠箭头或菜单，核对隐藏或恢复该节点子树与成员 |
| `architecture.select-branch` | 节点菜单选中这一支 → 高亮该节点与后代 | `frontend/architecture.js:771` | `data-cmd=branch` | 无 | 手动 · 1280×800 · 节点菜单选中这一支，核对高亮该节点与后代 |
| `architecture.select-all` | 画布内按 Ctrl／⌘+A → 选中当前可见节点 | `frontend/architecture.js:815` | `shownIds`、`selection` | 无 | 手动 · 1280×800 · 画布内按 Ctrl／⌘+A，核对选中当前可见节点 |
| `architecture.marquee` | 拖框 → 选中范围内节点，追加时保留已有选区 | `frontend/architecture.js:713` | `.arch-marquee` | 无 | 手动 · 1280×800 · 拖框，核对选中范围内节点，追加时保留已有选区 |
| `architecture.node-drag` | 拖动节点或选区 → 移动节点，按落点决定重排或挂入目标 | `frontend/architecture.js:624`、`frontend/architecture.js:644` | `.arch-part`、`finishDrag` | `plugin/tests/test_domains.py::test_tree_paths_layout_direction_and_version` | 手动 · 1280×800 · 拖动节点或选区，核对移动节点，按落点决定重排或挂入目标 |
| `architecture.member-drag` | 拖动成员到节点或未归属区 → 更新归属并显示可落点 | `frontend/architecture.js:672`、`frontend/architecture.js:682` | `.arch-ghost`、`.drop-target` | `plugin/tests/test_domains.py::test_many_leaders_per_part_and_one_lead` | 手动 · 1280×800 · 拖动成员到节点或未归属区，核对更新归属并显示可落点 |
| `architecture.member-open` | 在成员菜单点进入对话 → 打开对应 Leader 聊天 | `frontend/architecture.js:764` | `data-cmd=open` | 无 | 手动 · 1280×800 · 在成员菜单点进入对话，核对打开对应 Leader 聊天 |
| `architecture.member-lead` | 在成员菜单设负责人 → 该节点显示其负责人标识 | `frontend/architecture.js:765` | `data-cmd=lead` | `plugin/tests/test_domains.py::test_many_leaders_per_part_and_one_lead` | 手动 · 1280×800 · 在成员菜单设负责人，核对该节点显示其负责人标识 |
| `architecture.member-unlead` | 取消负责人 → 降为普通归属成员 | `frontend/architecture.js:766` | `data-cmd=unlead` | `plugin/tests/test_domains.py::test_many_leaders_per_part_and_one_lead` | 手动 · 1280×800 · 取消负责人，核对降为普通归属成员 |
| `architecture.member-leave` | 选移出到未归属 → 行回到未归属面板 | `frontend/architecture.js:767` | `data-cmd=leave` | `plugin/tests/test_domains.py::test_many_leaders_per_part_and_one_lead` | 手动 · 1280×800 · 选移出到未归属，核对行回到未归属面板 |
| `architecture.wire-connect` | 从端点拖向有效节点 → 改变父子连线 | `frontend/architecture.js:700` | `.arch-pin`、`finishWire` | 无 | 手动 · 1280×800 · 从端点拖向有效节点，核对改变父子连线 |
| `architecture.wire-detach` | 把入端点拖到空白 → 从父节点移出 | `frontend/architecture.js:704` | `detachPart` | 无 | 手动 · 1280×800 · 把入端点拖到空白，核对从父节点移出 |
| `architecture.wire-new` | 把出端点拖到空白 → 在释放处新建子节点 | `frontend/architecture.js:705` | `createPart` | 无 | 手动 · 1280×800 · 把出端点拖到空白，核对在释放处新建子节点 |
| `architecture.detach-menu` | 点节点菜单移出 → 成为无父节点 part | `frontend/architecture.js:772` | `data-cmd=detach` | 无 | 手动 · 1280×800 · 点节点菜单移出，核对成为无父节点 part |
| `architecture.delete` | 从菜单删除或按 Delete／Backspace → 删除选中 part | `frontend/architecture.js:773`、`frontend/architecture.js:816` | `deleteParts` | `plugin/tests/test_domains.py::test_deleting_a_part_moves_its_children_and_all_leaders_up_one_level` | 手动 · 1280×800 · 从菜单删除或按 Delete／Backspace，核对删除选中 part |
| `architecture.tidy` | 点自动排列 → 重排画布节点 | `frontend/architecture.js:775`、`frontend/architecture.js:784` | `data-arch-act=tidy` | 无 | 手动 · 1280×800 · 点自动排列，核对重排画布节点 |
| `architecture.direction` | 切换 TB 或 LR → 在上下与左右布局间切换，连线端点随之变更 | `frontend/architecture.js:785` | `data-direction`、`direction` | `plugin/tests/test_domains.py::test_tree_paths_layout_direction_and_version` | 手动 · 1280×800 · 切换 TB 或 LR，核对在上下与左右布局间切换，连线端点随之变更 |
| `architecture.shelf` | 点未归属区标题 → 展开或折叠并记住状态 | `frontend/architecture.js:226`、`frontend/architecture.js:786` | `crew.architecture.shelf` | 无 | 手动 · 1280×800 · 点未归属区标题，核对展开或折叠并记住状态 |
| `architecture.undo` | 点撤销或按 Ctrl／⌘+Z → 回退可撤销编辑 | `frontend/architecture.js:782`、`frontend/architecture.js:813` | `data-arch-act=undo` | `plugin/tests/test_domains.py::test_undo_batch_restores_a_deleted_part_with_the_same_id` | 手动 · 1280×800 · 点撤销或按 Ctrl／⌘+Z，核对回退可撤销编辑 |
| `architecture.redo` | 点重做或按 Ctrl／⌘+Shift+Z、Ctrl／⌘+Y → 重做编辑 | `frontend/architecture.js:783`、`frontend/architecture.js:813`、`frontend/architecture.js:814` | `data-arch-act=redo` | 无 | 手动 · 1280×800 · 点重做或按 Ctrl／⌘+Shift+Z、Ctrl／⌘+Y，核对重做编辑 |
| `architecture.fit` | 点显示全部或按 Home → 让可见节点适配画布 | `frontend/architecture.js:776`、`frontend/architecture.js:787`、`frontend/architecture.js:818` | `fit` | 无 | 手动 · 1280×800 · 点显示全部或按 Home，核对让可见节点适配画布 |
| `architecture.fit-selection` | 选中节点按 F → 镜头聚焦选区 | `frontend/architecture.js:818` | `fit`、`selection` | 无 | 手动 · 1280×800 · 选中节点按 F，核对镜头聚焦选区 |
| `architecture.zoom-in` | 点加号 → 以画布中心放大 | `frontend/architecture.js:788` | `data-arch-act=zoom-in` | 无 | 手动 · 1280×800 · 点加号，核对以画布中心放大 |
| `architecture.zoom-out` | 点减号 → 以画布中心缩小 | `frontend/architecture.js:789` | `data-arch-act=zoom-out` | 无 | 手动 · 1280×800 · 点减号，核对以画布中心缩小 |
| `architecture.zoom-reset` | 点倍率 → 恢复 100% | `frontend/architecture.js:790` | `data-arch-act=zoom-reset` | 无 | 手动 · 1280×800 · 点倍率，核对恢复 100% |
| `architecture.wheel` | 滚轮滚动或横向手势 → 围绕指针缩放或平移 | `frontend/architecture.js:724` | `wheel`、`zoomAt` | 无 | 手动 · 1280×800 · 滚轮滚动或横向手势，核对围绕指针缩放或平移 |
| `architecture.pinch` | 双指捏合 → 在两指中点附近调整镜头 | `frontend/architecture.js:607`、`frontend/architecture.js:611` | `startPinch`、`movePinch` | 无 | 手动 · 1280×800 · 双指捏合，核对在两指中点附近调整镜头 |
| `architecture.camera-remember` | 平移缩放后再回架构图 → 恢复工作区相机位置 | `frontend/architecture.js:216`、`frontend/architecture.js:219` | `cameraKey`、`localStorage` | 无 | 手动 · 1280×800 · 平移缩放后再回架构图，核对恢复工作区相机位置 |
| `architecture.context` | 单击右键 → 画布、节点和成员各显示对应菜单 | `frontend/architecture.js:733`、`frontend/architecture.js:801` | `.arch-menu`、`data-cmd` | 无 | 手动 · 1280×800 · 单击右键，核对画布、节点和成员各显示对应菜单 |
| `architecture.escape` | 按 Escape → 取消当前手势、菜单或选中态 | `frontend/architecture.js:811`、`frontend/architecture.js:819` | `cancelGesture`、`closeMenu` | 无 | 手动 · 1280×800 · 按 Escape，核对取消当前手势、菜单或选中态 |
| `architecture.return-chat` | 从会话团队栏进入架构图后点返回 → 还原原会话、执行范围与团队栏焦点 | `frontend/app.js:1603`、`frontend/app.js:1609` | `architectureReturn`、`.rp-architecture` | 无 | 手动 · 1280×800 · 从会话团队栏进入架构图后点返回，核对还原原会话、执行范围与团队栏焦点 |
| `architecture.pan` | 中键、右键或按住空格拖动 → 平移画布，右键原地释放则打开菜单 | `frontend/architecture.js:541`、`frontend/architecture.js:574` | `spaceDown`、`camera` | 无 | 手动 · 1280×800 · 中键、右键或按住空格拖动，核对平移画布，右键原地释放则打开菜单 |
| `architecture.touch-pan` | 单指拖动空白 → 平移画布 | `frontend/architecture.js:559`、`frontend/architecture.js:574` | `pointerType`、`camera` | 无 | 截图走查 · 375px 触屏拖动空白及缩放 |
| `architecture.add-select` | 按 Shift、Ctrl 或 ⌘ 点击节点 → 将节点加入或移出选区 | `frontend/architecture.js:554` | `selection`、`renderSelection` | 无 | 手动 · 1280×800 · 按 Shift、Ctrl 或 ⌘ 点击节点，核对将节点加入或移出选区 |
| `architecture.member-tap` | 点架构图 Leader 行 → 进入对应对话 | `frontend/architecture.js:589`、`frontend/architecture.js:595` | `options.openLeader` | 无 | 手动 · 1280×800 · 点架构图 Leader 行，核对进入对应对话 |
| `architecture.wire-alt-detach` | 按 Alt 点击入端点 → 将节点从当前父节点移出 | `frontend/architecture.js:549` | `.arch-pin`、`detachPart` | 无 | 手动 · 1280×800 · 按 Alt 点击入端点，核对将节点从当前父节点移出 |
| `architecture.domain-path` | 点聊天头部架构路径 → 打开画布并定位所选节点 | `frontend/app.js:1340`、`frontend/app.js:1603` | `.chat-domain-path`、`architectureFocus` | `plugin/tests/test_domains.py::test_board_round_trip_paths_in_state_and_conflicts` | 手动 · 1280×800 · 点聊天头部架构路径，核对打开画布并定位所选节点 |
| `architecture.history-open` | 点标题栏“版本历史” → 打开右侧抽屉（≤760px 全屏），列出每次结构改动的版本，最新在前 | `frontend/architecture.js:120`、`frontend/architecture.js:858` | `/api/op/domains.versions.list`、`data-arch-act=history` | 无 | 截图走查 · 1280 / 758 / 375 浅深色 · 打开版本历史，核对来源标签、说明、时间与 part / leader 数（`Temp/campaign/shots/d02`、`*-02-history`） |
| `architecture.history-diff` | 点某个版本 → 显示“现在 / 回滚后”改前改后对照、回滚会改什么、失去归属 / 已删除 / 仍归档的 leader；手机上有“版本列表”返回与对照切换 | `frontend/architecture.js:877`、`frontend/architecture.js:814` | `/api/op/domains.version.get`、`CrewRestructure.compare` | `plugin/tests/test_campaign_structure.py::test_rollback_restores_structure_keeps_layout_and_lists_leaders_it_cannot_place`、`plugin/tests/test_restructure_ui.js` | 截图走查 · 三视口浅深色 · 选版本 2，核对对照与提示 |
| `architecture.history-rollback` | 点“回滚到这个版本…” → 原地确认框 → 确认后回滚并存成新版本，提示会话未被删除或恢复；架构图在别处改过时提示并重读差异；手机只看不回滚 | `frontend/architecture.js:890`、`frontend/architecture.js:817` | `/api/op/domains.rollback` | `plugin/tests/test_campaign_structure.py::test_version_operations_through_the_registry` | 截图走查 · 1280×800 · 回滚到版本 2，核对新版本 `← #2` 与 toast（`d04`、`d05`） |
| `architecture.history-more` | 版本超过 50 个时点“加载更早的版本” → 追加下一页 | `frontend/architecture.js:813` | `/api/op/domains.versions.list`（`before`） | `plugin/tests/test_campaign_structure.py::test_listing_pages_and_notices_unrecorded_changes` | 手动 · 1280×800 · 造 51 个以上版本后点加载更早的版本 |
| `architecture.history-live` | 版本历史开着时别处改了架构图 → 经事件流自动出现新版本并刷新选中的差异 | `frontend/architecture.js:316`、`frontend/architecture.js:331` | `state.domains.version` | 无 | 截图走查 · 打开版本历史后用接口改名，核对列表出现新版本（`live-01`） |
| `architecture.leader-merge` | 右键 leader → “合并到另一位 leader…” → 打开重整预览，可改并入对象、编辑交接稿、选择是否唤醒，确认后执行 | `frontend/architecture.js:782`、`frontend/architecture.js:961` | `CrewRestructure`、`/api/op/domains.restructure.preview`、`/api/op/domains.restructure.apply` | `plugin/tests/test_restructure_ui.js::the panel previews the plan as given and executes it with the edited drafts and explicit wake choices` | 截图走查 · 1280×800 · 合并网关到数据，编辑交接稿后确认（`d06`–`d09`） |
| `architecture.leader-retire` | 右键 leader → “退役…” → 打开重整预览，可选接手人（或无），未结束任务须处理，确认后执行 | `frontend/architecture.js:783`、`frontend/architecture.js:961` | 同上 | `plugin/tests/test_campaign_structure.py::test_retiring_needs_idle_leaders_and_handled_tasks` | 截图走查 · 无接手人且有任务时核对逐条错误（`d14`） |
| `restructure.preview` | 重整预览面板（`window.CrewRestructure.create(...).open({project, changes, note})`）→ 改前 / 改后、受影响 leader、任务作废 / 改派与唤醒提示、将归档、待启动的新 leader、可编辑交接稿；移除条目或改接手人后自动重新预览；预览过期时重新预览而不执行 | `frontend/restructure-ui.js:61`、`frontend/restructure-ui.js:138` | `/api/op/domains.restructure.preview`、`/api/op/domains.restructure.apply` | `plugin/tests/test_restructure_ui.js` | 截图走查 · 三视口浅深色（`d10`–`d12`、`*-06/07/08-plan`） |

### Git 与 Git 助手

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `git.unavailable` | 在普通文件夹打开 Git 页 → 显示不可用原因并允许重新检查 | `frontend/git.js:108`、`frontend/git.js:168` | `#git-view`、`data-git-act=check` | `plugin/tests/test_git_availability_ui.js::a plain workspace Git page explains optional Git features without an enable form or background Git checks` | 手动 · 1280×800 · 在普通文件夹打开 Git 页，核对显示不可用原因并允许重新检查 |
| `git.enable-base` | 选择启用表单主干 → 作为助手的日常合入分支 | `frontend/git.js:96`、`frontend/git.js:177` | `#git-base` | `plugin/tests/test_git_runtime_ui.js::Codex-only Git setup selects Codex and submits it exactly once with the existing branch choices` | 手动 · 1280×800 · 选择启用表单主干，核对作为助手的日常合入分支 |
| `git.enable-integration` | 选择或留空本地集成分支 → 保存仅本地的集成目标 | `frontend/git.js:96`、`frontend/git.js:177` | `#git-integration` | `plugin/tests/test_git_runtime_ui.js::Codex-only Git setup selects Codex and submits it exactly once with the existing branch choices` | 手动 · 1280×800 · 选择或留空本地集成分支，核对保存仅本地的集成目标 |
| `git.enable-runtime` | 选择助手载体 → 依实际可用性更新可提交态 | `frontend/git.js:179` | `#git-runtime`、`#git-runtime-status` | `plugin/tests/test_git_runtime_ui.js::Codex-only Git setup selects Codex and submits it exactly once with the existing branch choices` | 手动 · 1280×800 · 选择助手载体，核对依实际可用性更新可提交态 |
| `git.enable` | 提交启用 → 创建助手绑定，失败显示原因 | `frontend/git.js:171` | `/api/git/enable`、`#git-enable-form` | `plugin/tests/test_git_runtime_ui.js::Codex-only Git setup selects Codex and submits it exactly once with the existing branch choices` | 手动 · 1280×800 · 提交启用，核对创建助手绑定，失败显示原因 |
| `git.settings` | 点 Git 设置 → 打开此工作区 Git 设置 | `frontend/git.js:167` | `data-git-act=settings` | 无 | 手动 · 1280×800 · 点 Git 设置，核对打开此工作区 Git 设置 |
| `git.refresh` | 点更新视图 → 重新读仓库状态 | `frontend/git.js:168` | `/api/git`、`data-git-act=refresh` | `plugin/tests/test_git_cache_ui.js::manual refresh keeps the last snapshot visible on failure and remains read only` | 手动 · 1280×800 · 点更新视图，核对重新读仓库状态 |
| `git.fetch` | 点拉取远端 → 反馈 fetch 输出和 PR 读取异常，并更新上次拉取时间 | `frontend/git.js:169` | `/api/git/fetch`、`.git-fetch-meta` | `plugin/tests/test_git_cache_ui.js::fetch survives leaving and returning without repeating the write or toast` | 手动 · 1280×800 · 点拉取远端，核对反馈 fetch 输出和 PR 读取异常，并更新上次拉取时间 |
| `git.adopt-branch` | 给未认领分支选 Leader → 更新分支所属并提示 | `frontend/git.js:181` | `/api/git/adopt`、`data-git-adopt` | 无 | 手动 · 1280×800 · 给未认领分支选 Leader，核对更新分支所属并提示 |
| `git.compact-assistant` | 点 Git 助手标题圆环 → 请求压缩该助手会话 | `frontend/git.js:124-131`、`frontend/app.js:1186` | `.git-assistant-title`、`state.git_assistant` | 无 | 手动 · 1280×800 · 点 Git 助手标题圆环，核对请求压缩该助手会话 |
| `git.settings-draft` | 关闭再开同项目设置 → 恢复未保存草稿 | `frontend/git-settings.js:76` | `project`、`draft` | 无 | 手动 · 1280×800 · 关闭再开同项目设置，核对恢复未保存草稿 |
| `git.settings-reload` | 点读取最新 → 有草稿时询问保留或放弃 | `frontend/git-settings.js:68` | `data-git-setting=reload` | 无 | 手动 · 1280×800 · 点读取最新，核对有草稿时询问保留或放弃 |
| `git.settings-discard` | 确认放弃草稿 → 读取最新设置 | `frontend/git-settings.js:69` | `data-git-setting=discard` | 无 | 手动 · 1280×800 · 确认放弃草稿，核对读取最新设置 |
| `git.settings-save` | 点保存 → 更新配置版本及提示，保存错误留在当前窗 | `frontend/git-settings.js:49`、`frontend/git-settings.js:59` | `revision`、`data-git-setting=save` | 无 | 手动 · 1280×800 · 点保存，核对更新配置版本及提示，保存错误留在当前窗 |
| `git.settings-disable` | 点停用 → 确认区提示解除绑定 | `frontend/git-settings.js:70` | `data-git-setting=disable` | 无 | 手动 · 1280×800 · 点停用，核对确认区提示解除绑定 |
| `git.settings-disable-confirm` | 确认停用 → 解除 Git 助手绑定并保留会话 | `frontend/git-settings.js:71` | `data-git-setting=confirm-disable` | 无 | 手动 · 1280×800 · 确认停用，核对解除 Git 助手绑定并保留会话 |
| `git.settings-repository` | 展开仓库与权限来源 → 显示工作目录、远程地址和凭据来源 | `frontend/git-settings.js:30` | `.gits-repository` | 无 | 手动 · 1280×800 · 展开仓库与权限来源，核对显示工作目录、远程地址和凭据来源 |
| `git.settings-defaults` | 点击默认载体等链接 → 转到 Git 助手大脑设置 | `frontend/git-settings.js:72` | `data-git-setting=defaults` | 无 | 手动 · 1280×800 · 点击默认载体等链接，核对转到 Git 助手大脑设置 |
| `git.settings-create` | 从未启用设置点启用 → 转到 Git 助手启用页 | `frontend/git-settings.js:73` | `data-git-setting=create` | 无 | 手动 · 1280×800 · 从未启用设置点启用，核对转到 Git 助手启用页 |
| `git.settings-close` | 点击关闭或按 Escape → 关闭设置并恢复焦点 | `frontend/git-settings.js:45`、`frontend/git-settings.js:75` | `dialog`、`returnTo` | 无 | 手动 · 1280×800 · 点击关闭或按 Escape，核对关闭设置并恢复焦点 |
| `git.pull-request-link` | 点分支 PR 标签 → 打开对应 PR 页面 | `frontend/git.js:43` | `.git-lite-tag.pr`、`target=_blank` | 无 | 手动 · 1280×800 · 点分支 PR 标签，核对打开对应 PR 页面 |
| `git.snapshot-retention` | 切回同工作区 Git → 复用已读快照及适用聊天状态 | `frontend/git.js:143`、`frontend/git.js:190` | `sameWorkspace`、`requestGeneration` | `plugin/tests/test_git_cache_ui.js::returning to the same workspace shows its snapshot immediately without another scan or polling` | 手动 · 1280×800 · 切回同工作区 Git，核对复用已读快照及适用聊天状态 |
| `git.settings-base` | 在已启用 Git 设置中更改主干 → 标为草稿并随保存生效 | `frontend/git-settings.js:28`、`frontend/git-settings.js:60` | `#gits-base`、`data-git-setting=save` | 无 | 手动 · 1280×800 · 在已启用 Git 设置中更改主干，核对标为草稿并随保存生效 |
| `git.settings-integration` | 更改或留空本地集成分支 → 对应草稿可保存 | `frontend/git-settings.js:28`、`frontend/git-settings.js:60` | `#gits-integration` | 无 | 手动 · 1280×800 · 更改或留空本地集成分支，核对对应草稿可保存 |
| `git.settings-rules` | 编辑合并规则 → 草稿保留并可由保存写入项目级规则 | `frontend/git-settings.js:29`、`frontend/git-settings.js:60` | `#gits-policy`、`name=branch_rules` | 无 | 手动 · 1280×800 · 编辑合并规则，核对草稿保留并可由保存写入项目级规则 |
| `git.settings-keep` | 读取最新提示出现时点保留草稿 → 返回当前草稿 | `frontend/git-settings.js:33`、`frontend/git-settings.js:70` | `data-git-setting=cancel-disable` | 无 | 手动 · 1280×800 · 读取最新提示出现时点保留草稿，核对返回当前草稿 |

### 会话库

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `library.entry` | 从 Leader 菜单点收编 → 打开会话库并记录待接入 Leader | `frontend/app.js:1538` | `adoptLeaderId` | 无 | 手动 · 1280×800 · 从 Leader 菜单点收编，核对打开会话库并记录待接入 Leader |
| `library.query` | 输入标题、名字、目录或 ID → 筛选匹配会话 | `frontend/session-library.js:77` | `data-sl-field=query` | `plugin/tests/test_session_library_ui.js::search combines words across title, workspace, path and native id without requiring remembered exact names` | 手动 · 1280×800 · 输入标题、名字、目录或 ID，核对筛选匹配会话 |
| `library.scope` | 切换当前或全部登记工作区 → 按选中范围加载 | `frontend/session-library.js:82` | `data-sl-field=scope` | `plugin/tests/test_session_library.py::test_scope_is_explicit_and_nested_workspace_wins` | 手动 · 1280×800 · 切换当前或全部登记工作区，核对按选中范围加载 |
| `library.runtime` | 切换载体筛选 → 显示相应 CC 或 Codex 会话 | `frontend/session-library.js:82` | `data-sl-field=runtime` | `plugin/tests/test_session_library_ui.js::native carrier, observed running state and grouping are independent filters` | 手动 · 1280×800 · 切换载体筛选，核对显示相应 CC 或 Codex 会话 |
| `library.status` | 切换运行中或未分组等状态 → 过滤结果 | `frontend/session-library.js:82` | `data-sl-field=status` | `plugin/tests/test_session_library_ui.js::native carrier, observed running state and grouping are independent filters` | 手动 · 1280×800 · 切换运行中或未分组等状态，核对过滤结果 |
| `library.category` | 点分类与计数 → 查看该分类会话 | `frontend/session-library.js:88` | `data-sl=category` | `plugin/tests/test_session_library_ui.js::default view groups fresh and named ungrouped conversations without archives or background jobs` | 手动 · 1280×800 · 点分类与计数，核对查看该分类会话 |
| `library.clear` | 点重置筛选 → 清除查询及过滤项 | `frontend/session-library.js:97` | `data-sl=clear` | 无 | 手动 · 1280×800 · 点重置筛选，核对清除查询及过滤项 |
| `library.refresh` | 点刷新 → 重读本机索引并显示状态 | `frontend/session-library.js:88` | `data-sl=refresh` | 无 | 手动 · 1280×800 · 点刷新，核对重读本机索引并显示状态 |
| `library.more` | 点再显示 → 分批展示剩余结果 | `frontend/session-library.js:88` | `data-sl=more` | 无 | 手动 · 1280×800 · 点再显示，核对分批展示剩余结果 |
| `library.preview` | 点会话或预览 → 查看载体、会话 ID、目录和只读正文 | `frontend/session-library.js:54`、`frontend/session-library.js:88` | `.sl-preview`、`data-sl=preview` | `plugin/tests/test_session_library.py::test_preview_contains_only_visible_text_not_reasoning_or_tool_results` | 手动 · 1280×800 · 点会话或预览，核对查看载体、会话 ID、目录和只读正文 |
| `library.close` | 关闭预览或按 Escape → 返回列表并聚焦原会话行 | `frontend/session-library.js:95`、`frontend/session-library.js:102` | `data-sl=close` | 无 | 手动 · 1280×800 · 关闭预览或按 Escape，核对返回列表并聚焦原会话行 |
| `library.open-team` | 在团队会话预览点进入对话 → 切至该会话 | `frontend/session-library.js:54`、`frontend/session-library.js:88` | `data-sl=open` | 无 | 手动 · 1280×800 · 在团队会话预览点进入对话，核对切至该会话 |
| `library.adopt` | 填写名字并选择 Leader 后确认接入 → 原生会话接入对应团队或显示错误 | `frontend/session-library.js:105` | `.sl-adopt` | 无 | 手动 · 1280×800 · 填写名字并选择 Leader 后确认接入，核对原生会话接入对应团队或显示错误 |

### 想法墙

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `notes.capture` | 填写想法后提交 → 显示新增卡片、保留标签归属，失败时保留输入 | `frontend/notes.js:634` | `#nw-capture`、`/api/personal/notes` | `plugin/tests/test_notes_ui.js::quick capture extracts #tags, completes the token under the caret and keeps titles from the first line`；`plugin/tests/test_notes_wall.py::test_capture_with_title_and_partial_update_keep_original_and_check_versions` | 手动 · 1280×800 · 填写想法后提交，核对显示新增卡片、保留标签归属，失败时保留输入 |
| `notes.capture-key` | 按 Enter 记下或 Shift+Enter 换行 → 提交想法或留在编辑 | `frontend/notes.js:624` | `#nw-capture` | 无 | 手动 · 1280×800 · 按 Enter 记下或 Shift+Enter 换行，核对提交想法或留在编辑 |
| `notes.capture-draft` | 输入想法后刷新 → 恢复输入草稿 | `frontend/notes.js:588`、`frontend/notes.js:621` | `DRAFT_KEY`、`localStorage` | 无 | 手动 · 1280×800 · 输入想法后刷新，核对恢复输入草稿 |
| `notes.capture-global` | 点记为全局 → 清除当前工作区归属并回焦输入 | `frontend/notes.js:696` | `captureProject` | 无 | 手动 · 1280×800 · 点记为全局，核对清除当前工作区归属并回焦输入 |
| `notes.tag-suggest` | 输入标签前缀 → 显示建议，可用上下键、Enter、Tab 或鼠标选择 | `frontend/notes.js:599`、`frontend/notes.js:624`、`frontend/notes.js:633` | `#nw-suggest`、`aria-activedescendant` | `plugin/tests/test_notes_ui.js::quick capture extracts #tags, completes the token under the caret and keeps titles from the first line` | 手动 · 1280×800 · 输入标签前缀，核对显示建议，可用上下键、Enter、Tab 或鼠标选择 |
| `notes.tag-suggest-close` | 按 Escape 或移出焦点 → 关闭标签建议 | `frontend/notes.js:623`、`frontend/notes.js:629` | `closeSuggest` | 无 | 手动 · 1280×800 · 按 Escape 或移出焦点，核对关闭标签建议 |
| `notes.filter-tag` | 点击标签 → 增删当前筛选标签 | `frontend/notes.js:686` | `data-act=filter-tag` | `plugin/tests/test_notes_ui.js::tag filters default to all-of, switch to any-of, and untagged/workspace/view narrow independently` | 手动 · 1280×800 · 点击标签，核对增删当前筛选标签 |
| `notes.filter-untagged` | 点无标签 → 切换无标签过滤并清除标签条件 | `frontend/notes.js:687` | `data-act=untagged` | `plugin/tests/test_notes_ui.js::tag filters default to all-of, switch to any-of, and untagged/workspace/view narrow independently` | 手动 · 1280×800 · 点无标签，核对切换无标签过滤并清除标签条件 |
| `notes.filter-mode` | 切换匹配方式 → 按全部或任一标签显示结果 | `frontend/notes.js:688` | `data-act=mode` | `plugin/tests/test_notes_ui.js::tag filters default to all-of, switch to any-of, and untagged/workspace/view narrow independently` | 手动 · 1280×800 · 切换匹配方式，核对按全部或任一标签显示结果 |
| `notes.group` | 切换分组方式 → 重新分组展示卡片 | `frontend/notes.js:688` | `data-act=group` | `plugin/tests/test_notes_ui.js::time grouping puts pinned, then needs-review, then today / this week / earlier; stage grouping follows the three stages` | 手动 · 1280×800 · 切换分组方式，核对重新分组展示卡片 |
| `notes.view` | 切换想法状态视图 → 显示对应卡片集合 | `frontend/notes.js:689`、`frontend/notes.js:721` | `data-act=view` | `plugin/tests/test_notes_ui.js::tag filters default to all-of, switch to any-of, and untagged/workspace/view narrow independently` | 手动 · 1280×800 · 切换想法状态视图，核对显示对应卡片集合 |
| `notes.view-open` | 点返回想法墙 → 进入开放想法视图 | `frontend/notes.js:690` | `data-act=view-open` | 无 | 手动 · 1280×800 · 点返回想法墙，核对进入开放想法视图 |
| `notes.filter-reset` | 点清除筛选 → 清除范围和标签限制 | `frontend/notes.js:691` | `data-act=clear-filters` | 无 | 手动 · 1280×800 · 点清除筛选，核对清除范围和标签限制 |
| `notes.filter-scope` | 切换全部、全局或特定工作区 → 加载对应想法范围 | `frontend/notes.js:719` | `data-act=scope` | `plugin/tests/test_notes_ui.js::tag filters default to all-of, switch to any-of, and untagged/workspace/view narrow independently` | 手动 · 1280×800 · 切换全部、全局或特定工作区，核对加载对应想法范围 |
| `notes.sort` | 切换排序 → 按所选排序重排卡片 | `frontend/notes.js:720` | `data-act=sort` | 无 | 手动 · 1280×800 · 切换排序，核对按所选排序重排卡片 |
| `notes.filter-route` | 更改筛选或使用浏览器前进后退 → URL 与当前过滤状态同步 | `frontend/notes.js:89`、`frontend/notes.js:100` | `M.parseHash`、`hashchange` | `plugin/tests/test_notes_ui.js::filter state round-trips through the URL hash and ignores unknown values` | 手动 · 1280×800 · 更改筛选或使用浏览器前进后退，核对URL 与当前过滤状态同步 |
| `notes.card-expand` | 点展开正文 → 显示或收起长内容 | `frontend/notes.js:697` | `data-act=expand` | 无 | 手动 · 1280×800 · 点展开正文，核对显示或收起长内容 |
| `notes.card-menu` | 点更多操作 → 弹出适应视口边缘的菜单并聚焦首项 | `frontend/notes.js:480`、`frontend/notes.js:685` | `.nw-menu`、`data-act=menu` | 无 | 手动 · 1280×800 · 点更多操作，核对弹出适应视口边缘的菜单并聚焦首项 |
| `notes.menu-nav` | 菜单内用上下键、Escape 或 Tab → 移动选项或关闭菜单 | `frontend/notes.js:492` | `.nw-menu` | 无 | 手动 · 1280×800 · 菜单内用上下键、Escape 或 Tab，核对移动选项或关闭菜单 |
| `notes.menu-dismiss` | 点击外部、滚动墙面或缩放窗口 → 自动收起菜单 | `frontend/notes.js:517` | `closeMenu`、`#nw-main` | 无 | 手动 · 1280×800 · 点击外部、滚动墙面或缩放窗口，核对自动收起菜单 |
| `notes.edit` | 从菜单编辑 → 修改想法并保存 | `frontend/notes.js:243`、`frontend/notes.js:507` | `editDialog`、`expected_version` | `plugin/tests/test_notes_wall.py::test_capture_with_title_and_partial_update_keep_original_and_check_versions` | 手动 · 1280×800 · 从菜单编辑，核对修改想法并保存 |
| `notes.pin` | 点置顶或取消置顶 → 更新卡片顺序及置顶标记 | `frontend/notes.js:513` | `data-menu=pin` | `plugin/tests/test_notes_wall.py::test_pin_snooze_keep_archive_restore_check_versions_and_replay` | 手动 · 1280×800 · 点置顶或取消置顶，核对更新卡片顺序及置顶标记 |
| `notes.snooze` | 进入暂缓子菜单选择天数 → 到期前将想法暂缓 | `frontend/notes.js:474`、`frontend/notes.js:504`、`frontend/notes.js:524` | `data-menu=snooze`、`snoozed_until` | `plugin/tests/test_notes_wall.py::test_pin_snooze_keep_archive_restore_check_versions_and_replay` | 手动 · 1280×800 · 进入暂缓子菜单选择天数，核对到期前将想法暂缓 |
| `notes.snooze-clear` | 选取消暂缓 → 恢复参与回顾 | `frontend/notes.js:515` | `data-menu=snooze:clear` | 无 | 手动 · 1280×800 · 选取消暂缓，核对恢复参与回顾 |
| `notes.snooze-back` | 点暂缓菜单返回 → 回到卡片主菜单 | `frontend/notes.js:505` | `data-menu=back` | 无 | 手动 · 1280×800 · 点暂缓菜单返回，核对回到卡片主菜单 |
| `notes.card-tags` | 点打标签 → 搜索或选择标签并更新该想法 | `frontend/notes.js:353`、`frontend/notes.js:508` | `tagPanel` | 无 | 手动 · 1280×800 · 点打标签，核对搜索或选择标签并更新该想法 |
| `notes.filter-tag-panel` | 点标签选择器 → 打开针对过滤的标签面板 | `frontend/notes.js:692` | `tagPanel`、`kind=filter` | 无 | 手动 · 1280×800 · 点标签选择器，核对打开针对过滤的标签面板 |
| `notes.tag-keys` | 标签面板用键盘导航与切换 → 更新当前高亮与所选标签 | `frontend/notes.js:387` | `tagPanel`、`data-index` | 无 | 手动 · 1280×800 · 标签面板用键盘导航与切换，核对更新当前高亮与所选标签 |
| `notes.manage-tags` | 点管理标签 → 进入标签维护对话框 | `frontend/notes.js:399`、`frontend/notes.js:693` | `manageTags` | 无 | 手动 · 1280×800 · 点管理标签，核对进入标签维护对话框 |
| `notes.merge` | 点合并 → 选择合并对象与保留项并确认 | `frontend/notes.js:318`、`frontend/notes.js:509` | `mergeDialog` | `plugin/tests/test_notes_wall.py::test_merge_appends_sources_unions_tags_and_unmerge_restores_only_the_child` | 手动 · 1280×800 · 点合并，核对选择合并对象与保留项并确认 |
| `notes.merge-duplicates` | 点重复想法入口 → 用候选重复条目预填合并 | `frontend/notes.js:705` | `data-act=merge-dup`、`dupes` | 无 | 手动 · 1280×800 · 点重复想法入口，核对用候选重复条目预填合并 |
| `notes.unmerge` | 点撤销合并 → 想法重新显示在墙上 | `frontend/notes.js:703` | `data-act=unmerge` | `plugin/tests/test_notes_wall.py::test_merge_appends_sources_unions_tags_and_unmerge_restores_only_the_child` | 手动 · 1280×800 · 点撤销合并，核对想法重新显示在墙上 |
| `notes.archive` | 点归档 → 从开放想法移到归档 | `frontend/notes.js:514`、`frontend/notes.js:701` | `data-act=archive` | `plugin/tests/test_notes_wall.py::test_pin_snooze_keep_archive_restore_check_versions_and_replay` | 手动 · 1280×800 · 点归档，核对从开放想法移到归档 |
| `notes.restore` | 归档页点恢复 → 想法回到墙上 | `frontend/notes.js:702` | `data-act=restore` | `plugin/tests/test_notes_wall.py::test_pin_snooze_keep_archive_restore_check_versions_and_replay` | 手动 · 1280×800 · 归档页点恢复，核对想法回到墙上 |
| `notes.keep` | 点保留 → 重置该想法的回顾时间 | `frontend/notes.js:700` | `data-act=keep` | `plugin/tests/test_notes_wall.py::test_pin_snooze_keep_archive_restore_check_versions_and_replay` | 手动 · 1280×800 · 点保留，核对重置该想法的回顾时间 |
| `notes.promote` | 点转任务 → 选择工作区与负责人并提交目标 | `frontend/notes.js:260`、`frontend/notes.js:698` | `promoteDialog` | `plugin/tests/test_notes_wall.py::test_start_note_and_recovered_link_promote_and_archive` | 手动 · 1280×800 · 点转任务，核对选择工作区与负责人并提交目标 |
| `notes.recover` | 点转出想法的恢复入口 → 按当前任务状态显示恢复操作 | `frontend/notes.js:280`、`frontend/notes.js:699` | `recoverDialog` | `plugin/tests/test_notes_wall.py::test_start_note_and_recovered_link_promote_and_archive` | 手动 · 1280×800 · 点转出想法的恢复入口，核对按当前任务状态显示恢复操作 |
| `notes.original` | 点最初记录 → 显示原始想法正文 | `frontend/notes.js:300`、`frontend/notes.js:511` | `originalDialog` | `plugin/tests/test_notes_wall.py::test_capture_with_title_and_partial_update_keep_original_and_check_versions` | 手动 · 1280×800 · 点最初记录，核对显示原始想法正文 |
| `notes.summaries` | 点讨论结论 → 查看该想法已有结论内容 | `frontend/notes.js:304`、`frontend/notes.js:704` | `summariesDialog` | 无 | 手动 · 1280×800 · 点讨论结论，核对查看该想法已有结论内容 |
| `notes.chat` | 点去聊聊 → 跳往对应会话并带入想法 | `frontend/notes.js:124`、`frontend/notes.js:682` | `chatUrl`、`note` | 无 | 手动 · 1280×800 · 点去聊聊，核对跳往对应会话并带入想法 |
| `notes.dialog-close` | 关闭想法对话框或按 Escape → 收起当前对话框 | `frontend/notes.js:218` | `dialog`、`data-close` | 无 | 手动 · 1280×800 · 关闭想法对话框或按 Escape，核对收起当前对话框 |
| `notes.multi-mode` | 切换批量选择 → 显示或退出卡片选择状态 | `frontend/notes.js:695` | `selectMode` | 无 | 手动 · 1280×800 · 切换批量选择，核对显示或退出卡片选择状态 |
| `notes.multi-pick` | 勾选想法 → 更新选中集合及批量栏 | `frontend/notes.js:683` | `.nw-check`、`selected` | 无 | 手动 · 1280×800 · 勾选想法，核对更新选中集合及批量栏 |
| `notes.bulk-tags` | 点批量标签 → 对所选想法一起打标签 | `frontend/notes.js:706` | `data-act=bulk-tags` | `plugin/tests/test_notes_wall.py::test_bulk_tags_project_and_archive` | 手动 · 1280×800 · 点批量标签，核对对所选想法一起打标签 |
| `notes.bulk-project` | 点批量归属 → 选择工作区并更新所选想法 | `frontend/notes.js:456`、`frontend/notes.js:707` | `bulkProjectDialog` | `plugin/tests/test_notes_wall.py::test_bulk_tags_project_and_archive` | 手动 · 1280×800 · 点批量归属，核对选择工作区并更新所选想法 |
| `notes.bulk-merge` | 点批量合并 → 用当前所选项打开合并表单 | `frontend/notes.js:708` | `data-act=bulk-merge` | 无 | 手动 · 1280×800 · 点批量合并，核对用当前所选项打开合并表单 |
| `notes.bulk-archive` | 点批量归档并确认 → 归档选中项、清空选择并显示数量 | `frontend/notes.js:709` | `data-act=bulk-archive` | `plugin/tests/test_notes_wall.py::test_bulk_tags_project_and_archive` | 手动 · 1280×800 · 点批量归档并确认，核对归档选中项、清空选择并显示数量 |
| `notes.bulk-clear` | 点清除选择或按 Escape → 清空并退出批量模式 | `frontend/notes.js:675`、`frontend/notes.js:714` | `selected`、`selectMode` | 无 | 手动 · 1280×800 · 点清除选择或按 Escape，核对清空并退出批量模式 |
| `notes.review-open` | 点回顾或按 R → 打开逐张处理及进度条 | `frontend/notes.js:530`、`frontend/notes.js:672`、`frontend/notes.js:694` | `.nw-review-progress` | 无 | 手动 · 1280×800 · 点回顾或按 R，核对打开逐张处理及进度条 |
| `notes.review-nav` | 点前后箭头或按左右键 → 在回顾队列中切卡 | `frontend/notes.js:572` | `data-key=ArrowLeft`、`data-key=ArrowRight` | 无 | 手动 · 1280×800 · 点前后箭头或按左右键，核对在回顾队列中切卡 |
| `notes.review-keep` | 按 K 或点保留 → 重置回顾时间并跳到下一张 | `frontend/notes.js:579` | `data-key=K` | 无 | 手动 · 1280×800 · 按 K 或点保留，核对重置回顾时间并跳到下一张 |
| `notes.review-archive` | 按 A 或点归档 → 归档这张并跳下一张 | `frontend/notes.js:579` | `data-key=A` | 无 | 手动 · 1280×800 · 按 A 或点归档，核对归档这张并跳下一张 |
| `notes.review-merge` | 按 M 或点合并 → 打开该张的合并流程 | `frontend/notes.js:575` | `data-key=M` | 无 | 手动 · 1280×800 · 按 M 或点合并，核对打开该张的合并流程 |
| `notes.review-snooze` | 按 H 或点暂缓 → 暂缓七天并前进 | `frontend/notes.js:580` | `data-key=H` | 无 | 手动 · 1280×800 · 按 H 或点暂缓，核对暂缓七天并前进 |
| `notes.review-chat` | 按 C 或点去聊聊 → 打开附带想法的会话入口 | `frontend/notes.js:574` | `data-key=C` | 无 | 手动 · 1280×800 · 按 C 或点去聊聊，核对打开附带想法的会话入口 |
| `notes.review-promote` | 按 P 或点转任务 → 打开当前想法转任务表单 | `frontend/notes.js:576` | `data-key=P` | 无 | 手动 · 1280×800 · 按 P 或点转任务，核对打开当前想法转任务表单 |
| `notes.review-finish` | 处理完回顾队列或按 Esc → 显示完成页或关闭回顾 | `frontend/notes.js:546`、`frontend/notes.js:564` | `reviewKey`、`data-close` | 无 | 手动 · 1280×800 · 处理完回顾队列或按 Esc，核对显示完成页或关闭回顾 |
| `notes.tags-shortcut` | 按 T → 对选中、聚焦的想法或当前筛选打开标签面板 | `frontend/notes.js:673` | `tagPanel`、`target` | 无 | 手动 · 1280×800 · 按 T，核对对选中、聚焦的想法或当前筛选打开标签面板 |
| `notes.pinned-shortcut` | 按 1–9 → 切换相应置顶标签，缺失时给出提示 | `frontend/notes.js:658`、`frontend/notes.js:674` | `pinnedTag` | 无 | 手动 · 1280×800 · 按 1–9，核对切换相应置顶标签，缺失时给出提示 |
| `notes.visibility-refresh` | 切回想法墙标签 → 重读其他入口的改动 | `frontend/notes.js:725` | `crew-tab-visibility`、`visibilitychange` | 无 | 手动 · 1280×800 · 切回想法墙标签，核对重读其他入口的改动 |
| `notes.tag-create` | 输入标签名并提交 → 新标签出现在管理列表 | `frontend/notes.js:411` | `.nw-tag-new` | `plugin/tests/test_notes_wall.py::test_create_recolor_pin_reorder_check_versions_and_palette` | 手动 · 1280×800 · 输入标签名并提交，核对新标签出现在管理列表 |
| `notes.tag-color` | 展开色板并选颜色 → 刷新标签颜色和选中态 | `frontend/notes.js:409`、`frontend/notes.js:419` | `.nw-palette`、`data-color` | `plugin/tests/test_notes_wall.py::test_create_recolor_pin_reorder_check_versions_and_palette` | 手动 · 1280×800 · 展开色板并选颜色，核对刷新标签颜色和选中态 |
| `notes.tag-pin` | 置顶或取消置顶标签 → 更新快捷标签集合 | `frontend/notes.js:420` | `data-tag-act=pin` | `plugin/tests/test_notes_wall.py::test_create_recolor_pin_reorder_check_versions_and_palette` | 手动 · 1280×800 · 置顶或取消置顶标签，核对更新快捷标签集合 |
| `notes.tag-up` | 点击上移 → 标签上移，焦点保留在该操作 | `frontend/notes.js:421-425` | `data-tag-act=up` | `plugin/tests/test_notes_wall.py::test_create_recolor_pin_reorder_check_versions_and_palette` | 手动 · 1280×800 · 点击上移，核对标签上移，焦点保留在该操作 |
| `notes.tag-down` | 点击下移 → 标签下移，焦点保留在该操作 | `frontend/notes.js:421-425` | `data-tag-act=down` | `plugin/tests/test_notes_wall.py::test_create_recolor_pin_reorder_check_versions_and_palette` | 手动 · 1280×800 · 点击下移，核对标签下移，焦点保留在该操作 |
| `notes.tag-remove` | 点删除标签并确认 → 从关联想法与当前筛选去掉该标签 | `frontend/notes.js:426-429` | `data-tag-act=remove` | 无 | 手动 · 1280×800 · 点删除标签并确认，核对从关联想法与当前筛选去掉该标签 |
| `notes.tag-rename` | 在标签名字段按 Enter → 更新关联项与当前筛选 | `frontend/notes.js:433-449` | `.nw-rename` | `plugin/tests/test_notes_wall.py::test_rename_syncs_every_note_and_existing_name_requires_merge` | 手动 · 1280×800 · 在标签名字段按 Enter，核对更新关联项与当前筛选 |
| `notes.tag-merge` | 将标签改为已存在名称 → 询问是否合并，确认后转换当前筛选 | `frontend/notes.js:441-445` | `merge_required`、`error.body.target` | `plugin/tests/test_notes_wall.py::test_rename_syncs_every_note_and_existing_name_requires_merge` | 手动 · 1280×800 · 将标签改为已存在名称，核对询问是否合并，确认后转换当前筛选 |

### 全局助手与随手记

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `assistant.open` | 打开全局助手 → 装载独立会话及工作区概览 | `frontend/assistant.js:38`、`frontend/assistant.js:44` | `#assistant-chat`、`/api/assistant/open`、`/api/assistant/state` | `plugin/tests/test_assistant_http.py::test_open_and_state_share_one_global_session_and_never_launch_ai` | 手动 · 1280×800 · 打开全局助手，核对装载独立会话及工作区概览 |
| `assistant.retry` | 加载助手失败后点重试 → 重新读取全局助手 | `frontend/assistant.js:17`、`frontend/assistant.js:38` | `retry`、`refresh` | 无 | 手动 · 1280×800 · 加载助手失败后点重试，核对重新读取全局助手 |
| `assistant.todo-link` | 点助手侧边的待办 → 跳到该工作区相应负责人 | `frontend/assistant.js:24`、`frontend/assistant.js:33` | `#assistant-overview` | 无 | 手动 · 1280×800 · 点助手侧边的待办，核对跳到该工作区相应负责人 |
| `assistant.task-link` | 点进行中的任务 → 打开对应工作区任务路由 | `frontend/assistant.js:25`、`frontend/assistant.js:34` | `leader`、`goal` | 无 | 手动 · 1280×800 · 点进行中的任务，核对打开对应工作区任务路由 |
| `assistant.workspace-link` | 点工作区链接 → 转到相应工作区 | `frontend/assistant.js:24`、`frontend/assistant.js:35` | `project` | 无 | 手动 · 1280×800 · 点工作区链接，核对转到相应工作区 |
| `assistant.note-attach` | 从想法墙去聊聊 → 查找想法并挂到助手输入框，删除关联时从 URL 清除 | `frontend/assistant.js:52-63` | `#note`、`/api/personal/notes` | `plugin/tests/test_notes_ui.js::assistant page attaches the idea from #note, prefills without sending, and clears the hash once the idea is used`；`desktop/test/tabs.test.cjs::“去聊聊” hands its note hash to the assistant, reusing an open assistant without reloading its draft` | 手动 · 1280×800 · 从想法墙去聊聊，核对查找想法并挂到助手输入框，删除关联时从 URL 清除 |
| `assistant.note-missing` | 通过无效想法链接进入 → 显示缺失或读取错误 | `frontend/assistant.js:58-60` | `dropNote`、`toast` | 无 | 手动 · 1280×800 · 通过无效想法链接进入，核对显示缺失或读取错误 |
| `assistant.opening` | 经 `#opening=<工作区>` 进入或已开着时 hash 变化 → 开局意图写入空输入框（有草稿则提示不覆盖），从不自动发送，工作区不存在时提示，hash 用后清除 | `frontend/assistant.js`（`readOpening`、`attachOpening`）、`frontend/chat.js`（`prefill`） | `CrewChat.prefill` | `plugin/tests/test_assistant_opening.js`；`plugin/tests/test_chat_connection.js::prefill writes only into an empty composer and reports whether it did, without sending` | 手动 · 隔离桌面 · 从空白工作区点让助手开局 |
| `assistant.empty-prompts` | 助手对话还没有任何记录时 → 显示“从哪里聊起？”和四个示例（汇总各工作区近况、记一个想法、让助手开局、给 leader 建任务）；点击只把示例写入空输入框并聚焦，有草稿则提示不覆盖，从不自动发送；历史读取中、有更早记录或已有消息时不显示 | `frontend/assistant.js`（`EMPTY_GUIDE`）、`frontend/chat.js`（`emptyGuideHTML`、`render`） | `.chat-empty-guide`、`data-chat-prompt`、`CrewChat.prefill` | `plugin/tests/test_chat_connection.js::an empty conversation shows its starting points only once history is known empty, and picking one only fills the composer`；`plugin/tests/test_assistant_opening.js::the assistant hands its chat four starting points that are only written into the composer` | 截图走查 · 1280×800、758×900、375×812 浅深色 · 新助手点示例，核对只填入输入框 |
| `assistant.prompts` | 点助手行为配置 → 打开会话级配置弹窗并能关闭 | `frontend/assistant.js:64` | `#assistant-prompts`、`/api/behavior-prompts` | 无 | 手动 · 1280×800 · 点助手行为配置，核对打开会话级配置弹窗并能关闭 |
| `assistant.jot` | 点随手记 → 打开笔记表单并显示最近三条 | `frontend/assistant.js:65`、`frontend/personal-notes-ui.js:31` | `#assistant-notes`、`.jot-dialog` | 无 | 手动 · 1280×800 · 点随手记，核对打开笔记表单并显示最近三条 |
| `assistant.jot-submit` | 点记下来 → 成功清空、刷新最近记录，失败保留文字 | `frontend/personal-notes-ui.js:23`、`frontend/personal-notes-ui.js:38` | `.jot-dialog form` | `plugin/tests/test_assistant_http.py::test_quick_note_capture_edit_and_start_keep_original_and_never_duplicate_leader` | 手动 · 1280×800 · 点记下来，核对成功清空、刷新最近记录，失败保留文字 |
| `assistant.jot-key` | 按 Enter 或 Shift+Enter → 记录或插入换行，组合输入时保留 | `frontend/personal-notes-ui.js:40` | `requestSubmit` | 无 | 手动 · 1280×800 · 按 Enter 或 Shift+Enter，核对记录或插入换行，组合输入时保留 |
| `assistant.jot-draft` | 关闭随手记后再次打开 → 恢复本机草稿 | `frontend/personal-notes-ui.js:30`、`frontend/personal-notes-ui.js:36`、`frontend/personal-notes-ui.js:39` | `localStorage`、`draftKey` | 无 | 手动 · 1280×800 · 关闭随手记后再次打开，核对恢复本机草稿 |
| `assistant.jot-wall` | 点打开想法墙 → 进入想法墙管理页面 | `frontend/personal-notes-ui.js:34` | `href=/notes.html` | 无 | 手动 · 1280×800 · 点打开想法墙，核对转到壁面管理 |
| `assistant.jot-close` | 点击关闭或按 Escape → 收起随手记 | `frontend/personal-notes-ui.js:41` | `data-note-close` | 无 | 手动 · 1280×800 · 点击关闭或按 Escape，核对收起随手记 |

### 用量与额度

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `usage.period` | 切换统计时间段 → 清除旧统计并重新加载当前时间段 | `frontend/app.js:1655` | `data-act=usage-period` | 无 | 手动 · 1280×800 · 逐个切换可见的时间段 |
| `usage.tab` | 切换用量页分类 → 显示对应统计视图 | `frontend/app.js:1656` | `data-act=usage-tab` | `plugin/tests/test_usage_ui.js::weekly quota tab lists recorded and estimated weeks without guessing missing values` | 手动 · 1280×800 · 点用量页各标签 |
| `usage.fold` | 展开或收起用量分组 → 按分组保留折叠状态 | `frontend/app.js:1657` | `usageCollapsed` | `plugin/tests/test_usage_ui.js::usage shows member Claude and native Codex totals, including leader self and collapsed team totals` | 手动 · 1280×800 · 打开分组后切分类再返回 |
| `usage.budget-edit` | 点击预算编辑 → 显示 Claude API 预算表单 | `frontend/app.js:1271`、`frontend/app.js:1624` | `#budget-form` | 无 | 手动 · 1280×800 · 打开后用键盘检查输入和取消 |
| `usage.budget-save` | 提交预算 → 用量区更新预算信息或给出错误 | `frontend/app.js:1278`、`frontend/app.js:1695` | `#budget-form`、`saveBudget` | 无 | 手动 · 1280×800 · 保存合法值与非法值各一次 |
| `usage.budget-clear` | 点击清除预算 → 恢复无预算状态 | `frontend/app.js:1625` | `data-act=budget-clear` | 无 | 手动 · 1280×800 · 有预算时清除并刷新 |
| `usage.quota-unknown` | 查看未采集或已重置额度 → 保留真实状态，已有数值时才显示进度 | `frontend/app.js:252`、`frontend/app.js:867` | `quotaText`、`used_percent` | `plugin/tests/test_runtime_ui.js::uncollected quota has only text and a collected zero has an empty determinate bar` | 手动 · 1280×800 · 查看未采集或已重置额度，核对保留真实状态，已有数值时才显示进度 |
| `usage.quota-estimate` | 悬停 Crew 用量占比 → 查看估算依据及范围 | `frontend/app.js:254` | `.quota-crew`、`title` | 无 | 手动 · 1280×800 · 悬停 Crew 用量占比，核对查看估算依据及范围 |
| `usage.budget-cycle` | 选择月度或累计预算周期 → 预算按设置显示重置或累计起点 | `frontend/app.js:1271` | `#budget-form`、`budgetCycle` | 无 | 手动 · 1280×800 · 选择月度或累计预算周期，核对预算按设置显示重置或累计起点 |

### 设置、默认大脑与权限

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `settings.category` | 点设置分类 → 显示对应设置内容与选中状态 | `frontend/settings-ui.js:179` | `data-settings-category`、`aria-current` | `plugin/tests/test_settings_ui.js::opening message channels does not wait for or start AI model discovery` | 手动 · 1280×800 · 点设置分类，核对显示对应设置内容与选中状态 |
| `settings.close` | 点击关闭、返回或按 Escape → 返回工作区原视图 | `frontend/settings-ui.js:181`、`frontend/app.js:1120`、`frontend/app.js:1732` | `data-settings-action=close`、`settingsOpen` | 无 | 手动 · 1280×800 · 点击关闭、返回或按 Escape，核对返回工作区原视图 |
| `settings.retry` | 点击重新检测或重试 → 重读配置与载体可用性；已编辑值保留 | `frontend/settings-ui.js:182` | `data-settings-action=reload`、`snapshot.runtimes` | 无 | 手动 · 1280×800 · 点击重新检测或重试，核对重读配置与载体可用性；已编辑值保留 |
| `settings.reset` | 点撤销更改 → 恢复已保存 AI 及权限配置 | `frontend/settings-ui.js:183` | `data-settings-action=reset` | 无 | 手动 · 1280×800 · 点撤销更改，核对恢复已保存 AI 及权限配置 |
| `settings.save` | 点保存设置 → 校验当前选项并显示结果 | `frontend/settings-ui.js:163`、`frontend/settings-ui.js:184` | `data-settings-action=save` | 无 | 手动 · 1280×800 · 点保存设置，核对校验当前选项并显示结果 |
| `settings.permissions-cc` | 修改 Claude Code 权限 → 保存后用于下一次载体启动 | `frontend/settings-ui.js:189` | `data-execution-runtime=claude` | 无 | 手动 · 1280×800 · 修改 Claude Code 权限，核对保存后用于下一次载体启动 |
| `settings.permissions-codex` | 修改 Codex 权限 → 保存后用于下一次载体启动 | `frontend/settings-ui.js:189` | `data-execution-runtime=codex` | `plugin/tests/test_settings_ui.js::execution permissions start safely and retain an explicitly saved full-access choice` | 手动 · 1280×800 · 修改 Codex 权限，核对保存后用于下一次载体启动 |
| `settings.inherit` | 按用途切换是否继承 → 显示默认或独立 AI 选项 | `frontend/settings-ui.js:190` | `data-ai-inherit` | 无 | 手动 · 1280×800 · 按用途切换是否继承，核对显示默认或独立 AI 选项 |
| `settings.runtime` | 更换载体 → 清除原模型、强度、速度并恢复字段焦点 | `frontend/settings-ui.js:193` | `data-ai-field=runtime` | 无 | 手动 · 1280×800 · 更换载体，核对清除原模型、强度、速度并恢复字段焦点 |
| `settings.model` | 更换模型 → 更新可选强度与速度列表，保留显式设置供校验 | `frontend/settings-ui.js:200` | `data-ai-field=model` | `plugin/tests/test_settings_ui.js::changing a model under automatic sole runtime refreshes effort options and saves the user's explicit compatible choice` | 手动 · 1280×800 · 更换模型，核对更新可选强度与速度列表，保留显式设置供校验 |
| `settings.effort` | 选择强度 → 更新对应默认或用途配置草稿 | `frontend/settings-ui.js:186` | `data-ai-field=effort` | `plugin/tests/test_settings_ui.js::changing a model under automatic sole runtime refreshes effort options and saves the user's explicit compatible choice` | 手动 · 1280×800 · 选择强度，核对更新对应默认或用途配置草稿 |
| `settings.speed` | 切换 Codex 速度 → 更新档位说明 | `frontend/settings-ui.js:209` | `data-ai-field=speed`、`data-ai-speed-note` | `plugin/tests/test_settings_ui.js::Codex speed settings offer inheritance and supported standard and fast speeds, preserving a saved tier` | 手动 · 1280×800 · 切换 Codex 速度，核对更新档位说明 |
| `settings.custom-model` | 点模型自定义输入或返回列表 → 替换控件并回焦 | `frontend/settings-ui.js:173` | `data-ai-custom`、`data-ai-field=model` | `plugin/tests/test_settings_ui.js::model and effort suggestions come from catalogs and custom values are escaped instead of replaced` | 手动 · 1280×800 · 点模型自定义输入或返回列表，核对替换控件并回焦 |
| `settings.custom-effort` | 点强度自定义输入或返回列表 → 替换控件并回焦 | `frontend/settings-ui.js:173` | `data-ai-custom`、`data-ai-field=effort` | `plugin/tests/test_settings_ui.js::model and effort suggestions come from catalogs and custom values are escaped instead of replaced` | 手动 · 1280×800 · 点强度自定义输入或返回列表，核对替换控件并回焦 |

### 外观、字号与配色

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `appearance.theme-cycle` | 点日夜开关 → 在系统、浅色、深色间循环 | `frontend/app.js:1595`、`frontend/app.js:264` | `data-act=theme`、`crew.theme` | 无 | 手动 · 1280×800 · 点日夜开关，核对在系统、浅色、深色间循环 |
| `appearance.theme-select` | 点外观主题选项 → 切换浅色、深色或跟随系统 | `frontend/app.js:1596` | `data-act=theme-select`、`CrewTheme` | 无 | 手动 · 1280×800 · 点外观主题选项，核对切换浅色、深色或跟随系统 |
| `appearance.theme-system` | 系统颜色模式改变 → 跟随系统的 Crew 同步 | `frontend/app.js:1477` | `matchMedia`、`applyTheme` | 无 | 手动 · 1280×800 · 系统颜色模式改变，核对跟随系统的 Crew 同步 |
| `appearance.project-color` | 点一款当前工作区配色 → 立即应用并保存到工作区 | `frontend/app.js:1597` | `/api/project/theme`、`data-theme-id` | 无 | 手动 · 1280×800 · 逐个选择六款并切换工作区验证作用域 |
| `appearance.font-slider` | 拖字号滑杆 → 实时显示 90%–140% 及内容大小，拖动中保留控件 | `frontend/app.js:1686` | `data-setting=font`、`--fs`、`crew.fontScale` | 无 | 手动 · 1280×800 · 分别拖至两端，刷新检查 |
| `appearance.font-reset` | 点字号重置 → 恢复 100% | `frontend/app.js:1599` | `data-act=font-reset` | 无 | 手动 · 1280×800 · 点字号重置，核对恢复 100% |
| `appearance.font-desktop` | 桌面内拖字号滑杆 → 拖动中只预览本页，松手写入桌面共用字号并同步所有工作区、助手、想法墙页面；首次读取时迁移旧的 `crew.fontScale` | `frontend/app.js`（`setFontScale`、`adoptDesktopFont`）、`frontend/global-rail.js`、`desktop/src/appearance.cjs` | `crewDesktopTheme.setFontScale`、`desktop:font-scale-set` | 无 | 手动 · 隔离桌面 · 在工作区设置拖动后打开助手页核对字号 |

### 切换执行工具

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `runtime.open` | 点更换载体 → 看当前会话、候选载体及阻塞原因 | `frontend/app.js:1598` | `runtimeSwitch.open` | `plugin/tests/test_session_config_ui.js::switching blocks active and external writers before consulting stale allowed state` | 手动 · 1280×800 · 点更换载体，核对看当前会话、候选载体及阻塞原因 |
| `runtime.runtime` | 选择另一载体 → 清除不适用的模型、强度与速度并作废旧预览 | `frontend/runtime-switch-ui.js:58` | `#runtime-switch-runtime` | `plugin/tests/test_session_config_ui.js::runtime switch does not preselect an unavailable alternative on a Codex-only installation` | 手动 · 1280×800 · 选择另一载体，核对清除不适用的模型、强度与速度并作废旧预览 |
| `runtime.model` | 选择模型 → 更新强度和可用速度并作废预览 | `frontend/runtime-switch-ui.js:59` | `#runtime-switch-model` | `plugin/tests/test_session_config_ui.js::switch previews reject models and efforts outside the chosen runtime catalog` | 手动 · 1280×800 · 选择模型，核对更新强度和可用速度并作废预览 |
| `runtime.effort` | 选择强度 → 更新设置并作废预览 | `frontend/runtime-switch-ui.js:60` | `#runtime-switch-effort` | `plugin/tests/test_session_config_ui.js::switch previews reject models and efforts outside the chosen runtime catalog` | 手动 · 1280×800 · 选择强度，核对更新设置并作废预览 |
| `runtime.speed` | 选择速度 → 更新设置并作废预览 | `frontend/runtime-switch-ui.js:61` | `#runtime-switch-speed` | `plugin/tests/test_session_config_ui.js::a Codex target speed must be a tier the target model offers and never reaches Claude` | 手动 · 1280×800 · 选择速度，核对更新设置并作废预览 |
| `runtime.catalog` | 点刷新载体选项 → 重读发现结果并清除失效配置 | `frontend/runtime-switch-ui.js:74` | `data-rs-act=catalog` | 无 | 手动 · 1280×800 · 点刷新载体选项，核对重读发现结果并清除失效配置 |
| `runtime.prepare` | 点生成预览 → 显示交接稿与有效期或说明不能切换 | `frontend/runtime-switch-ui.js:82` | `data-rs-act=prepare`、`/api/session/runtime/prepare`、`preview_id` | `plugin/tests/test_runtime_switch.py::test_prepare_and_switch_have_no_native_process_or_model_turn_and_preserve_crew` | 手动 · 1280×800 · 点生成预览，核对显示交接稿与有效期或说明不能切换 |
| `runtime.handoff-edit` | 编辑交接文本 → 禁用确认直至重新生成预览 | `frontend/runtime-switch-ui.js:63` | `#runtime-switch-handoff` | `plugin/tests/test_session_config_ui.js::the edited handoff is included exactly for a fresh bound preview` | 手动 · 1280×800 · 编辑交接文本，核对禁用确认直至重新生成预览 |
| `runtime.confirm` | 用有效预览确认 → 保存配置，提示下次输入或唤醒时续上 | `frontend/runtime-switch-ui.js:88` | `data-rs-act=confirm`、`/api/session/runtime/switch`、`preview_id` | `plugin/tests/test_runtime_switch.py::test_prepare_and_switch_have_no_native_process_or_model_turn_and_preserve_crew`；`plugin/tests/test_runtime_switch.py::test_first_codex_to_claude_creates_then_switch_back_resumes_original_histories` | 手动 · 1280×800 · 用有效预览确认，核对保存配置，提示下次输入或唤醒时续上 |
| `runtime.conflict` | 用过期或冲突预览提交 → 显示错误并要求重新预览 | `frontend/runtime-switch-ui.js:95` | `status=409`、`expired` | `plugin/tests/test_runtime_switch.py::test_confirmation_expiration_staleness_and_idempotent_success` | 手动 · 1280×800 · 用过期或冲突预览提交，核对显示错误并要求重新预览 |
| `runtime.close` | 点遮罩、关闭或按 Escape → 退出切换弹窗 | `frontend/runtime-switch-ui.js:67`、`frontend/runtime-switch-ui.js:98` | `data-rs-act=close` | 无 | 手动 · 1280×800 · 点遮罩、关闭或按 Escape，核对退出切换弹窗 |
| `runtime.focus` | 在切换弹窗首尾按 Tab → 在可见控件内循环焦点 | `frontend/runtime-switch-ui.js:101` | `overlay` | 无 | 手动 · 1280×800 · 在切换弹窗首尾按 Tab，核对在可见控件内循环焦点 |

### 组员默认大脑

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `member-settings.open` | 点本组执行默认大脑 → 打开该 Leader 的组员默认设置 | `frontend/app.js:1529` | `memberUI.openDefaults` | 无 | 手动 · 1280×800 · 点本组执行默认大脑，核对打开该 Leader 的组员默认设置 |
| `member-settings.inherit` | 开关继承 → 切换跟随 Leader 或手动选择，并恢复焦点 | `frontend/member-ui.js:48` | `name=inherit` | `plugin/tests/test_member_defaults.py::test_leader_override_wins_over_global_and_clear_restores_global` | 手动 · 1280×800 · 开关继承，核对切换跟随 Leader 或手动选择，并恢复焦点 |
| `member-settings.runtime` | 切换载体 → 清除旧模型、强度、速度并重画选项 | `frontend/member-ui.js:51` | `data-ai-field=runtime` | 无 | 手动 · 1280×800 · 切换载体，核对清除旧模型、强度、速度并重画选项 |
| `member-settings.model` | 选择模型 → 刷新可选项并保持字段焦点 | `frontend/member-ui.js:52` | `data-ai-field=model` | 无 | 手动 · 1280×800 · 选择模型，核对刷新可选项并保持字段焦点 |
| `member-settings.effort` | 选择或输入思考强度 → 更新该组的大脑选项草稿 | `frontend/member-ui.js:49` | `data-ai-field=effort` | 无 | 手动 · 1280×800 · 选择或输入思考强度，核对更新该组的大脑选项草稿 |
| `member-settings.speed` | 切换速度 → 刷新说明与该项焦点 | `frontend/member-ui.js:52` | `data-ai-field=speed` | 无 | 手动 · 1280×800 · 切换速度，核对刷新说明与该项焦点 |
| `member-settings.custom` | 点自定义输入或返回列表 → 切换编辑控件并回焦 | `frontend/member-ui.js:61` | `data-ai-custom` | 无 | 手动 · 1280×800 · 点自定义输入或返回列表，核对切换编辑控件并回焦 |
| `member-settings.refresh` | 点重新检测 → 重新读取可用载体与模型或显示错误 | `frontend/member-ui.js:66` | `data-member-action=refresh` | 无 | 手动 · 1280×800 · 点重新检测，核对重新读取可用载体与模型或显示错误 |
| `member-settings.save` | 保存或恢复继承 → 校验选项并保存本组默认设置 | `frontend/member-ui.js:72` | `/api/member-defaults` | `plugin/tests/test_member_defaults.py::test_leader_override_wins_over_global_and_clear_restores_global`；`plugin/tests/test_member_defaults.py::test_member_and_invalid_settings_cannot_be_saved_and_legacy_json_stays_readable` | 手动 · 1280×800 · 保存或恢复继承，核对校验选项并保存本组默认设置 |
| `member-settings.close` | 点击关闭、遮罩或按 Escape → 收起设置弹窗 | `frontend/member-ui.js:57`、`frontend/member-ui.js:85` | `data-member-action=close` | 无 | 手动 · 1280×800 · 点击关闭、遮罩或按 Escape，核对收起设置弹窗 |
| `member-settings.focus` | 在组员设置首尾按 Tab → 循环焦点 | `frontend/member-ui.js:87` | `overlay` | 无 | 手动 · 1280×800 · 在组员设置首尾按 Tab，核对循环焦点 |

### 行为配置与使用记录

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `prompts.scope` | 切换全局、工作区或会话范围 → 刷新相应配置并保留旧范围草稿 | `frontend/behavior-prompts-ui.js:38` | `data-bp-scope`、`drafts` | `plugin/tests/test_behavior_prompts.py::test_session_and_workspace_overrides_are_inherited_and_reset_without_affecting_others` | 手动 · 1280×800 · 切换全局、工作区或会话范围，核对刷新相应配置并保留旧范围草稿 |
| `prompts.item` | 在左侧选择提示词项 → 进入该项编辑器并保存前项草稿 | `frontend/behavior-prompts-ui.js:28` | `data-bp-key` | 无 | 手动 · 1280×800 · 在左侧选择提示词项，核对进入该项编辑器并保存前项草稿 |
| `prompts.edit` | 编辑提示词 → 保留本项草稿，版本变化时提示核对 | `frontend/behavior-prompts-ui.js:38` | `draftKey`、`expected_version` | `plugin/tests/test_behavior_prompts.py::test_stale_ai_edit_rejected_preserving_user_content_and_history` | 手动 · 1280×800 · 编辑提示词，核对保留本项草稿，版本变化时提示核对 |
| `prompts.discard` | 点放弃草稿 → 恢复当前配置内容 | `frontend/behavior-prompts-ui.js:30` | `data-bp-action=discard` | 无 | 手动 · 1280×800 · 点放弃草稿，核对恢复当前配置内容 |
| `prompts.save` | 保存覆盖 → 更新选定范围并提示下次正常输入生效 | `frontend/behavior-prompts-ui.js:34` | `action=save`、`expected_version` | `plugin/tests/test_assistant_http.py::test_prompt_settings_are_versioned_and_save_without_starting_execution`；`plugin/tests/test_behavior_prompts.py::test_each_kind_can_be_edited_versioned_and_restored` | 手动 · 1280×800 · 保存覆盖，核对更新选定范围并提示下次正常输入生效 |
| `prompts.reset` | 点恢复继承 → 删除本范围覆盖并显示继承结果 | `frontend/behavior-prompts-ui.js:34` | `data-bp-action=reset` | `plugin/tests/test_behavior_prompts.py::test_session_and_workspace_overrides_are_inherited_and_reset_without_affecting_others` | 手动 · 1280×800 · 点恢复继承，核对删除本范围覆盖并显示继承结果 |
| `prompts.history` | 点历史 → 列出可展开的版本内容及保存或恢复记录 | `frontend/behavior-prompts-ui.js:33` | `data-bp-history`、`action=history` | `plugin/tests/test_behavior_prompts.py::test_each_kind_can_be_edited_versioned_and_restored` | 手动 · 1280×800 · 点历史，核对列出可展开的版本内容及保存或恢复记录 |
| `prompts.applied` | 点查看生效证据或打开会话设置 → 读取已应用的提示词证据 | `frontend/behavior-prompts-ui.js:27`、`frontend/behavior-prompts-ui.js:39` | `data-bp-proof`、`action=applied` | `plugin/tests/test_behavior_prompts.py::test_confirmed_loading_requires_evidence_and_rejects_cross_task_identity_or_corrupt_manifest` | 手动 · 1280×800 · 点查看生效证据或打开会话设置，核对读取已应用的提示词证据 |

### 提示音

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `sound.retry` | 加载音效失败时点重试 → 重读设置与预设 | `frontend/sound-settings-ui.js:65` | `data-sound-retry` | 无 | 手动 · 1280×800 · 加载音效失败时点重试，核对重读设置与预设 |
| `sound.mute` | 切换音效开关 → 改变全局静音草稿 | `frontend/sound-settings-ui.js:110` | `data-sound-enabled` | 无 | 手动 · 1280×800 · 切换音效开关，核对改变全局静音草稿 |
| `sound.volume` | 拖动音量 → 读数即时变化 | `frontend/sound-settings-ui.js:111` | `data-sound-volume` | 无 | 手动 · 1280×800 · 拖动音量，核对读数即时变化 |
| `sound.presets` | 为单类事件更换音效 → 更新该事件草稿 | `frontend/sound-settings-ui.js:114` | `data-sound-kind` | `plugin/tests/test_event_sounds.py::test_http_and_desktop_settings_share_saved_preferences_without_starting_models` | 手动 · 1280×800 · 为单类事件更换音效，核对更新该事件草稿 |
| `sound.preview` | 点事件试听 → 播放当前选定的音效或显示错误 | `frontend/sound-settings-ui.js:92` | `data-sound-preview` | 无 | 手动 · 1280×800 · 点事件试听，核对播放当前选定的音效或显示错误 |
| `sound.custom-preview` | 点自定义音效试听 → 播放指定自定义预设 | `frontend/sound-settings-ui.js:89` | `data-sound-custom-preview` | 无 | 手动 · 1280×800 · 点自定义音效试听，核对播放指定自定义预设 |
| `sound.custom-upload` | 选择音频与名称并上传 → 校验 WAV／MP3／OGG 及 1 MB 限制后加入预设 | `frontend/sound-settings-ui.js:66` | `/api/sounds/custom`、`data-sound-upload` | `plugin/tests/test_event_sounds.py::test_custom_sound_upload_plays_selects_and_lists_in_every_catalog`；`plugin/tests/test_event_sounds.py::test_custom_sound_rejects_bad_files_and_limits_with_reasons` | 手动 · 1280×800 · 选择音频与名称并上传，核对校验 WAV／MP3／OGG 及 1 MB 限制后加入预设 |
| `sound.custom-delete` | 点删除自定义音效 → 从预设列表移除或显示实际错误 | `frontend/sound-settings-ui.js:80` | `/api/sounds/custom/delete` | `plugin/tests/test_event_sounds.py::test_deleting_a_used_custom_sound_names_its_events_and_a_lost_file_is_reported_missing` | 手动 · 1280×800 · 点删除自定义音效，核对从预设列表移除或显示实际错误 |
| `sound.reset` | 点撤销更改 → 音效表单恢复已保存值 | `frontend/sound-settings-ui.js:95` | `data-sound-reset` | 无 | 手动 · 1280×800 · 点撤销更改，核对音效表单恢复已保存值 |
| `sound.save` | 保存音效 → 工作区与助手使用新设置 | `frontend/sound-settings-ui.js:96` | `data-sound-save`、`CrewSound.configure` | `plugin/tests/test_event_sounds.py::test_http_and_desktop_settings_share_saved_preferences_without_starting_models` | 手动 · 1280×800 · 保存音效，核对工作区与助手使用新设置 |
| `sound.play-events` | 状态刷新带来新的拍板、错误或已结束成员回复 → 按设置播放对应提示音 | `frontend/app.js:1346` | `CrewSound`、`notifySounds` | 无 | 手动 · 1280×800 · 逐一模拟拍板、渠道错误和成员回复完成，比较开关前后 |
| `sound.unlock` | 首次点击或按键 → 尝试启用浏览器音频，试听失败时可再点 | `frontend/sounds.js:174-176` | `unlockAudio`、`preview` | 无 | 手动 · 1280×800 · 首次点击或按键，核对尝试启用浏览器音频，试听失败时可再点 |
| `sound.dedupe` | 同一有标识的事件在多个标签到达 → 以共享事件锁和存储记录减少重复响铃 | `frontend/sounds.js:119-159` | `navigator.locks`、`localStorage`、`pending` | 无 | 手动 · 1280×800 · 同一浏览器开两标签并回放同一事件 ID |
| `sound.baseline` | 首次打开页面或刷新时已有旧事件 → 建立基线后只播放符合条件的新事件 | `frontend/sounds.js:152-159` | `baseline`、`observed` | 无 | 手动 · 1280×800 · 首次打开页面或刷新时已有旧事件，核对建立基线后只播放符合条件的新事件 |
| `sound.tie-priority` | 多个事件同时到达 → 合批后播放优先级最高且已配置的提示音 | `frontend/sounds.js:122-134` | `catalog.kinds`、`pending` | 无 | 手动 · 1280×800 · 同时发出两类事件并核对实际播放 |

### 消息连接与通知

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `connections.channel` | 切换渠道标签 → 显示 QQ 或飞书连接状态及该渠道凭证 | `frontend/connections-ui.js:139` | `data-channel-select` | 无 | 手动 · 1280×800 · 切换渠道标签，核对显示 QQ 或飞书连接状态及该渠道凭证 |
| `connections.refresh` | 点刷新 → 更新连接与绑定状态 | `frontend/connections-ui.js:141` | `data-connection-action=refresh` | 无 | 手动 · 1280×800 · 点刷新，核对更新连接与绑定状态 |
| `connections.app-id` | 输入 App ID 或凭证 → 将字段留在渠道草稿 | `frontend/connections-ui.js:107` | `data-connection-field` | 无 | 手动 · 1280×800 · 输入 App ID 或凭证，核对将字段留在渠道草稿 |
| `connections.secret-toggle` | 点显示或隐藏 → 切换 Secret 明文与遮蔽 | `frontend/connections-ui.js:142` | `data-connection-action=toggle-secret` | 无 | 手动 · 1280×800 · 点显示或隐藏，核对切换 Secret 明文与遮蔽 |
| `connections.domain` | 切换飞书域 → 安装配置指引随域更新 | `frontend/connections-ui.js:112` | `data-connection-field=domain` | 无 | 手动 · 1280×800 · 切换飞书域，核对安装配置指引随域更新 |
| `connections.configure` | 提交凭证 → 验证后保存并连接，成功滚到绑定步骤 | `frontend/connections-ui.js:114` | `data-connection-form` | `plugin/tests/test_connections.py::test_new_user_can_save_bind_and_test_without_secret_in_snapshot` | 手动 · 1280×800 · 提交凭证，核对验证后保存并连接，成功滚到绑定步骤 |
| `connections.copy-binding` | 点复制绑定命令 → 成功显示提示，剪贴板失败时选中供手动复制 | `frontend/connections-ui.js:123` | `data-binding-command` | 无 | 手动 · 1280×800 · 点复制绑定命令，核对成功显示提示，剪贴板失败时选中供手动复制 |
| `connections.reset-binding` | 点重置绑定或取消 → 打开或关闭确认区 | `frontend/connections-ui.js:143` | `data-connection-action=reset-binding` | 无 | 手动 · 1280×800 · 点重置绑定或取消，核对打开或关闭确认区 |
| `connections.reset-binding-confirm` | 确认重置 → 生成新的绑定命令 | `frontend/connections-ui.js:144` | `data-connection-action=confirm-binding` | `plugin/tests/test_connections.py::test_disconnect_reconnect_reset_binding_and_away_survive_reload` | 手动 · 1280×800 · 确认重置，核对生成新的绑定命令 |
| `connections.route` | 选择并保存主动通知渠道 → 路由设置即时生效 | `frontend/connections-ui.js:108`、`frontend/connections-ui.js:145` | `data-notification-target`、`save-preferences` | `plugin/tests/test_connections.py::test_push_preference_selects_channels_persists_and_keeps_private_commands_available` | 手动 · 1280×800 · 选择并保存主动通知渠道，核对路由设置即时生效 |
| `connections.reconnect` | 点重新连接 → 发起该渠道连接并反馈 | `frontend/connections-ui.js:147` | `data-connection-action=reconnect` | `plugin/tests/test_connections.py::test_disconnect_reconnect_reset_binding_and_away_survive_reload` | 手动 · 1280×800 · 点重新连接，核对发起该渠道连接并反馈 |
| `connections.disconnect` | 点断开 → 断开连接并保留凭证 | `frontend/connections-ui.js:147` | `data-connection-action=disconnect` | `plugin/tests/test_connections.py::test_disconnect_reconnect_reset_binding_and_away_survive_reload` | 手动 · 1280×800 · 点断开，核对断开连接并保留凭证 |
| `connections.test` | 点发送测试 → 展示平台接受或报错 | `frontend/connections-ui.js:147` | `data-connection-action=test` | `plugin/tests/test_connections.py::test_new_user_can_save_bind_and_test_without_secret_in_snapshot`；`plugin/tests/test_connections.py::test_test_failure_reports_transport_error_without_success` | 手动 · 1280×800 · 点发送测试，核对展示平台接受或报错 |
| `connections.away` | 在渠道页切换离开状态 → 刷新主动通知配置 | `frontend/connections-ui.js:148` | `data-away` | `plugin/tests/test_connections.py::test_disconnect_reconnect_reset_binding_and_away_survive_reload` | 手动 · 1280×800 · 在渠道页切换离开状态，核对刷新主动通知配置 |

### 后台与运维

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `operations.tabs` | 点击后台与运维子标签 → 显示对应子面板 | `frontend/operations-ui.js:196` | `data-operations-tab` | 无 | 手动 · 1280×800 · 点击后台与运维子标签，核对显示对应子面板 |
| `operations.tab-keys` | 子标签按左右键、Home、End → 切换面板并聚焦对应标签 | `frontend/operations-ui.js:215` | `data-operations-tab` | 无 | 手动 · 1280×800 · 子标签按左右键、Home、End，核对切换面板并聚焦对应标签 |
| `operations.example` | 点示例 → 将内容填入运维输入框并聚焦 | `frontend/operations-ui.js:197` | `data-operations-example` | 无 | 手动 · 1280×800 · 点示例，核对将内容填入运维输入框并聚焦 |
| `operations.brain` | 点配置运维大脑 → 跳到设置中运维用途卡 | `frontend/operations-ui.js:199` | `data-operations-action=brain` | 无 | 手动 · 1280×800 · 点配置运维大脑，核对跳到设置中运维用途卡 |
| `operations.launch` | 点连接与目录 → 切到启动连接设置面板 | `frontend/operations-ui.js:200` | `data-operations-action=launch-settings` | `plugin/tests/test_operations_ui.js::a home-page shortcut opens the connection tab and hands its intent to the launch settings once` | 手动 · 1280×800 · 点连接与目录，核对切到启动连接设置面板 |
| `operations.chat-retry` | 点重试运维对话 → 重读运维聊天 | `frontend/operations-ui.js:201` | `data-operations-action=retry-chat` | 无 | 手动 · 1280×800 · 点重试运维对话，核对重读运维聊天 |
| `operations.chat-stop` | 点运维停止 → 请求中断并反映停止状态 | `frontend/operations-ui.js:202` | `data-operations-action=interrupt` | `plugin/tests/test_operations_chat.py::test_interrupt_calls_owned_runner_stop_and_preserves_partial_reply` | 手动 · 1280×800 · 点运维停止，核对请求中断并反映停止状态 |
| `operations.chat-draft` | 输入运维消息后切页 → 当前草稿保留 | `frontend/operations-ui.js:207` | `.co-composer`、`chatDraft` | 无 | 手动 · 1280×800 · 输入运维消息后切页，核对当前草稿保留 |
| `operations.chat-send` | 点发送或桌面 Enter → 提交运维消息，Shift+Enter 可换行 | `frontend/operations-ui.js:214` | `.co-composer`、`CrewBrowser.chatSendKey` | `plugin/tests/test_operations_chat.py::test_send_starts_only_a_stub_child_and_duplicate_id_does_not_start_twice` | 手动 · 1280×800 · 点发送或桌面 Enter，核对提交运维消息，Shift+Enter 可换行 |
| `operations.chat-scroll` | 运维消息继续到达 → 底部跟随，向上读时恢复原位置 | `frontend/operations-ui.js:163` | `chatScroll`、`chatPinned` | 无 | 手动 · 1280×800 · 运维消息继续到达，核对底部跟随，向上读时恢复原位置 |
| `operations.chat-visible` | 隐藏或返回运维聊天 → 暂停或恢复轮询和计时 | `frontend/operations-ui.js:222` | `visibilitychange`、`readChat` | 无 | 手动 · 1280×800 · 隐藏或返回运维聊天，核对暂停或恢复轮询和计时 |
| `operations.config-retry` | 配置读取失败点重试 → 重读运维策略 | `frontend/operations-ui.js:203` | `data-operations-action=retry-config` | 无 | 手动 · 1280×800 · 配置读取失败点重试，核对重读运维策略 |
| `operations.config-edit` | 编辑运维策略字段 → 更新未保存状态 | `frontend/operations-ui.js:208` | `data-operations-field` | `plugin/tests/test_operations_ui.js::operations form preserves exact argument boundaries and quoted Windows paths` | 手动 · 1280×800 · 编辑运维策略字段，核对更新未保存状态 |
| `operations.config-reset` | 点撤销运维策略 → 还原已保存配置 | `frontend/operations-ui.js:204` | `data-operations-action=reset-config` | 无 | 手动 · 1280×800 · 点撤销运维策略，核对还原已保存配置 |
| `operations.config-save` | 提交运维策略 → 显示保存结果 | `frontend/operations-ui.js:214` | `.co-config` | 无 | 手动 · 1280×800 · 提交运维策略，核对显示保存结果 |

### 启动、连接与目录

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `launch.reload` | 点重新读取 → 载入桌面启动配置 | `frontend/launch-settings-ui.js:160` | `data-launch-action=reload` | 无 | 手动 · 隔离桌面 · 点重新读取，核对载入桌面启动配置 |
| `launch.mode` | 切换连接模式 → 显示本机或远程配置相应字段 | `frontend/launch-settings-ui.js:173` | `data-launch-field=mode` | 无 | 手动 · 隔离桌面 · 切换连接模式，核对显示本机或远程配置相应字段 |
| `launch.source` | 切换后台程序来源 → 显示对应来源字段并作废预览 | `frontend/launch-settings-ui.js:174` | `data-launch-field=source` | 无 | 手动 · 隔离桌面 · 切换后台程序来源，核对显示对应来源字段并作废预览 |
| `launch.fields` | 编辑端口、路径等启动字段 → 更新草稿并清除校验结果 | `frontend/launch-settings-ui.js:174` | `data-launch-field` | `plugin/tests/test_launch_settings_ui.js::launch form preserves all startup settings and Windows path boundaries`；`plugin/tests/test_launch_settings_ui.js::launch form validates port syntax while preserving the automatic-port choice` | 手动 · 隔离桌面 · 编辑端口、路径等启动字段，核对更新草稿并清除校验结果 |
| `launch.advanced` | 展开高级选项 → 显示扩展字段并保留展开态 | `frontend/launch-settings-ui.js:153` | `.cl-advanced` | 无 | 手动 · 隔离桌面 · 展开高级选项，核对显示扩展字段并保留展开态 |
| `launch.pick` | 点目录浏览 → 原生选择成功后回填并作废预览 | `frontend/launch-settings-ui.js:88`、`frontend/launch-settings-ui.js:158` | `data-launch-pick` | 无 | 手动 · 隔离桌面 · 点目录浏览，核对原生选择成功后回填并作废预览 |
| `launch.select-remote` | 选择已配对主机 → 更新启动草稿并保持对应选项焦点 | `frontend/launch-settings-ui.js:175` | `data-launch-remote` | `plugin/tests/test_launch_settings_ui.js::a LAN host choice keeps its saved device and this computer's own launch settings unchanged` | 手动 · 隔离桌面 · 选择已配对主机，核对更新启动草稿并保持对应选项焦点 |
| `launch.pair-open` | 点新增配对 → 打开表单并聚焦地址或配对码 | `frontend/launch-settings-ui.js:98`、`frontend/launch-settings-ui.js:166` | `data-launch-action=pair-open` | 无 | 手动 · 隔离桌面 · 点新增配对，核对打开表单并聚焦地址或配对码 |
| `launch.pair-paste` | 粘贴带配对码的主机链接 → 拆填主机地址和配对码 | `frontend/launch-settings-ui.js:176` | `data-pair-field=url`、`pairingLink` | `plugin/tests/test_launch_settings_ui.js::a pasted connection link fills the host address and the one-time code` | 手动 · 隔离桌面 · 粘贴带配对码的主机链接，核对拆填主机地址和配对码 |
| `launch.pair-submit` | 提交主机配对 → 新主机加入列表，并自动检查切换预览 | `frontend/launch-settings-ui.js:102`、`frontend/launch-settings-ui.js:169` | `.cl-pair`、`api.pair` | 无 | 手动 · 隔离桌面 · 提交主机配对，核对新主机加入列表，并自动检查切换预览 |
| `launch.pair-cancel` | 点取消配对 → 收起表单并聚焦入口 | `frontend/launch-settings-ui.js:167` | `data-launch-action=pair-cancel` | 无 | 手动 · 隔离桌面 · 点取消配对，核对收起表单并聚焦入口 |
| `launch.home-action` | 选择 HOME 处理方式 → 保存草稿并失效此前预览 | `frontend/launch-settings-ui.js:183` | `data-launch-home-action` | 无 | 手动 · 隔离桌面 · 选择 HOME 处理方式，核对保存草稿并失效此前预览 |
| `launch.check` | 点检查 → 显示连接、目录迁移预览或异常，并聚焦确认区 | `frontend/launch-settings-ui.js:57`、`frontend/launch-settings-ui.js:161` | `.cl-confirm`、`api.validate` | `desktop/test/launch-settings.test.cjs::launch preview checks source and Python and counts sessions without stopping or saving` | 手动 · 隔离桌面 · 点检查，核对显示连接、目录迁移预览或异常，并聚焦确认区 |
| `launch.edit` | 预览中点继续编辑 → 清空预览并聚焦检查按钮 | `frontend/launch-settings-ui.js:164` | `data-launch-action=edit` | 无 | 手动 · 隔离桌面 · 预览中点继续编辑，核对清空预览并聚焦检查按钮 |
| `launch.reset` | 点撤销更改 → 恢复原配置并取消配对草稿 | `frontend/launch-settings-ui.js:165` | `data-launch-action=reset` | 无 | 手动 · 隔离桌面 · 点撤销更改，核对恢复原配置并取消配对草稿 |
| `launch.apply` | 确认应用 → 显示重开进度、配置无变化或失败 | `frontend/launch-settings-ui.js:65`、`frontend/launch-settings-ui.js:162` | `api.apply`、`home_action` | `desktop/test/launch-settings.test.cjs::owned HOME copy pauses and drains before copy, saves atomically, then requests relaunch` | 手动 · 隔离桌面 · 确认应用，核对显示重开进度、配置无变化或失败 |
| `launch.recover` | 点按当前配置重新打开 → 用已保存的配置恢复连接 | `frontend/launch-settings-ui.js:77`、`frontend/launch-settings-ui.js:163` | `data-launch-action=recover` | `desktop/test/launch-settings.test.cjs::after a failed copy, applying the original settings explicitly reconnects the stopped backend` | 手动 · 隔离桌面 · 点按当前配置重新打开，核对用已保存的配置恢复连接 |

### 工作区设置

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `workspace-settings.open` | 点击工作区设置 → 打开该工作区配置 | `frontend/app.js:1511`、`frontend/workspace-settings-ui.js:115` | `data-act=workspace-settings`、`CrewWorkspaceSettings` | 无 | 手动 · 1280×800 · 点击工作区设置，核对打开该工作区配置 |
| `workspace-settings.name` | 编辑显示名称 → 更新草稿名称 | `frontend/workspace-settings-ui.js:92` | `name=display_name` | `plugin/tests/test_workspace_settings_ui.js::editing only the display name saves without stop and reopening reads the saved state` | 手动 · 1280×800 · 编辑显示名称，核对更新草稿名称 |
| `workspace-settings.path` | 编辑根目录 → 更新草稿路径 | `frontend/workspace-settings-ui.js:92` | `name=root` | `plugin/tests/test_workspace_settings_ui.js::directory migration needs an explicit confirmation, preserves counts, and sends once while pending` | 手动 · 1280×800 · 编辑根目录，核对更新草稿路径 |
| `workspace-settings.browse` | 点浏览 → 选择目录后更新输入并回焦 | `frontend/workspace-settings-ui.js:108` | `data-workspace-action=browse` | `plugin/tests/test_workspace_settings_ui.js::native browsing only edits the path draft; cancellation and errors do not register or save` | 手动 · 1280×800 · 点浏览，核对选择目录后更新输入并回焦 |
| `workspace-settings.save` | 点保存 → 更新工作区，目录变化时进入确认 | `frontend/workspace-settings-ui.js:96`、`frontend/workspace-settings-ui.js:107` | `data-workspace-action=confirm` | `plugin/tests/test_workspace_settings_ui.js::editing only the display name saves without stop and reopening reads the saved state` | 手动 · 1280×800 · 点保存，核对更新工作区，目录变化时进入确认 |
| `workspace-settings.remove` | 点移除并确认 → 移除登记并展示结果 | `frontend/workspace-settings-ui.js:105`、`frontend/workspace-settings-ui.js:107` | `data-workspace-action=remove` | `plugin/tests/test_workspace_settings_ui.js::remove requires confirmation and success before closing, with retry after a real failure` | 手动 · 1280×800 · 在隔离工作区分别取消和确认 |
| `workspace-settings.back` | 从确认界面返回 → 回编辑并聚焦路径 | `frontend/workspace-settings-ui.js:104` | `data-workspace-action=back` | `plugin/tests/test_workspace_settings_ui.js::a failed save and returning to edit retain drafts, while closing discards them` | 手动 · 1280×800 · 从确认界面返回，核对回编辑并聚焦路径 |
| `workspace-settings.reload` | 点重新读取 → 重载当前工作区设置 | `frontend/workspace-settings-ui.js:103` | `data-workspace-action=reload` | `plugin/tests/test_workspace_settings_ui.js::a failed initial read exposes its real error and only retries on the explicit action` | 手动 · 1280×800 · 点重新读取，核对重载当前工作区设置 |
| `workspace-settings.close` | 关闭或 Escape → 退出并恢复启动入口焦点 | `frontend/workspace-settings-ui.js:97`、`frontend/workspace-settings-ui.js:102` | `dialog`、`returnTo` | `plugin/tests/test_workspace_settings_ui.js::Escape cancellation discards unsaved edits and reopening reads the stored values` | 手动 · 1280×800 · 关闭或 Escape，核对退出并恢复启动入口焦点 |

### 部门压缩与工作区清理

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `maintenance.department` | 选择压缩整个部门 → 显示可处理会话及预览 | `frontend/app.js:1644`、`frontend/maintenance-ui.js:236` | `/api/maintenance/department/preview` | `plugin/tests/test_runtime_ui.js::only a leader menu offers whole-department compaction, separate from compacting one session` | 手动 · 1280×800 · 选择压缩整个部门，核对显示可处理会话及预览 |
| `maintenance.department-select` | 勾选部门会话 → 更新待压缩目标及按钮状态 | `frontend/maintenance-ui.js:293` | `selected` | 无 | 手动 · 1280×800 · 勾选部门会话，核对更新待压缩目标及按钮状态 |
| `maintenance.department-confirm` | 确认仅压缩所选会话 → 启动并查看部门压缩任务 | `frontend/maintenance-ui.js:256` | `/api/maintenance/department/compact` | 无 | 手动 · 1280×800 · 确认仅压缩所选会话，核对启动并查看部门压缩任务 |
| `maintenance.cleanup` | 在工作区设置点清理 → 显示会话和历史归档预览 | `frontend/workspace-settings-ui.js:106`、`frontend/maintenance-ui.js:236` | `/api/maintenance/cleanup/preview` | `plugin/tests/test_workspace_settings_ui.js::current-workspace cleanup is offered from settings and hands the stable key and label to the cleanup dialog` | 手动 · 1280×800 · 在工作区设置点清理，核对显示会话和历史归档预览 |
| `maintenance.cleanup-confirm` | 确认清理工作区 → 提交该预览并显示进度 | `frontend/maintenance-ui.js:256` | `/api/maintenance/cleanup` | 无 | 手动 · 1280×800 · 确认清理工作区，核对提交该预览并显示进度 |
| `maintenance.job-status` | 维护任务进行中 → 持续刷新进度与各目标结果 | `frontend/maintenance-ui.js:209`、`frontend/maintenance-ui.js:226` | `/api/maintenance/jobs` | 无 | 手动 · 1280×800 · 维护任务进行中，核对持续刷新进度与各目标结果 |
| `maintenance.job-reload` | 点重试或刷新 → 重读预览与最近任务 | `frontend/maintenance-ui.js:304` | `data-action=retry`、`data-action=refresh` | 无 | 手动 · 1280×800 · 点重试或刷新，核对重读预览与最近任务 |
| `maintenance.job-history` | 点历史任务 → 查看该任务结果 | `frontend/maintenance-ui.js:307` | `show-job` | 无 | 手动 · 1280×800 · 点历史任务，核对查看该任务结果 |
| `maintenance.archive-channel` | 点已归档交流记录 → 打开该任务归档记录 | `frontend/maintenance-ui.js:311` | `archive-channel`、`/api/maintenance/archive` | 无 | 手动 · 1280×800 · 点已归档交流记录，核对打开该任务归档记录 |
| `maintenance.archive-handoff` | 点已归档交接记录 → 打开交接归档 | `frontend/maintenance-ui.js:311` | `archive-handoffs` | 无 | 手动 · 1280×800 · 点已归档交接记录，核对打开交接归档 |
| `maintenance.archive-more` | 点加载更多归档 → 追加更早归档记录 | `frontend/maintenance-ui.js:312` | `archive-more` | 无 | 手动 · 1280×800 · 点加载更多归档，核对追加更早归档记录 |
| `maintenance.archive-back` | 点返回 → 回到前一任务或预览并聚焦标题 | `frontend/maintenance-ui.js:306` | `#maintenance-title` | 无 | 手动 · 1280×800 · 点返回，核对回到前一任务或预览并聚焦标题 |
| `maintenance.dismiss` | 点关闭或按 Escape → 关闭维护对话框并清理前端轮询 | `frontend/maintenance-ui.js:285`、`frontend/maintenance-ui.js:292` | `dialog`、`stopPolling` | 无 | 手动 · 1280×800 · 点关闭或按 Escape，核对关闭维护对话框并清理前端轮询 |

### 存储与回收站

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `storage.refresh` | 点刷新 → 重扫分类占用及回收站 | `frontend/storage-ui.js:190` | `data-storage-action=refresh` | `plugin/tests/test_storage_ui.js::read errors can be retried; refresh and failed saves retain a policy draft` | 手动 · 1280×800 · 点刷新，核对重扫分类占用及回收站 |
| `storage.category` | 勾选清理分类 → 更新选中数量及是否可预览 | `frontend/storage-ui.js:183` | `data-storage-category` | `plugin/tests/test_storage_ui.js::storage uses backend safe defaults, makes two byte outcomes explicit, and cancelling preserves the selection and focus` | 手动 · 1280×800 · 勾选清理分类，核对更新选中数量及是否可预览 |
| `storage.preview` | 点预览 → 显示将立刻释放或移入回收站的内容 | `frontend/storage-ui.js:191` | `data-storage-action=preview`、`data-storage-focus` | `plugin/tests/test_storage_ui.js::storage uses backend safe defaults, makes two byte outcomes explicit, and cancelling preserves the selection and focus` | 手动 · 1280×800 · 点预览，核对显示将立刻释放或移入回收站的内容 |
| `storage.cancel-preview` | 取消预览 → 回到分类选择并聚焦预览按钮 | `frontend/storage-ui.js:192` | `data-storage-action=cancel-preview` | `plugin/tests/test_storage_ui.js::storage uses backend safe defaults, makes two byte outcomes explicit, and cancelling preserves the selection and focus` | 手动 · 1280×800 · 取消预览，核对回到分类选择并聚焦预览按钮 |
| `storage.clean` | 确认清理 → 使用现存预览；过期显示重做提示 | `frontend/storage-ui.js:193` | `data-storage-action=confirm-clean`、`preview_id` | `plugin/tests/test_storage_ui.js::empty storage and expired previews have clear, non-destructive states` | 手动 · 1280×800 · 确认清理，核对使用现存预览；过期显示重做提示 |
| `storage.results` | 查看清理结果 → 显示实际释放、回收及部分失败 | `frontend/storage-ui.js:104` | `outcome`、`resultSection` | `plugin/tests/test_storage_ui.js::a pending cleanup submits once, partial failure reports only actual results and refreshes history` | 手动 · 1280×800 · 查看清理结果，核对显示实际释放、回收及部分失败 |
| `storage.restore` | 点回收站批次恢复 → 恢复对应文件并刷新结果 | `frontend/storage-ui.js:196` | `data-storage-action=restore`、`data-batch-id` | `plugin/tests/test_storage_ui.js::restore conflicts keep their real reason; emptying trash requires a separate confirmation and is single-flight` | 手动 · 1280×800 · 点回收站批次恢复，核对恢复对应文件并刷新结果 |
| `storage.trash-confirm` | 点清空回收站 → 出现确认区并聚焦 | `frontend/storage-ui.js:197` | `data-storage-action=empty-trash` | `plugin/tests/test_storage_ui.js::restore conflicts keep their real reason; emptying trash requires a separate confirmation and is single-flight` | 手动 · 1280×800 · 点清空回收站，核对出现确认区并聚焦 |
| `storage.trash-cancel` | 点取消清空 → 返回回收站入口 | `frontend/storage-ui.js:198` | `data-storage-action=cancel-empty` | 无 | 手动 · 1280×800 · 点取消清空，核对返回回收站入口 |
| `storage.trash-empty` | 确认清空 → 显示实际删除与释放结果 | `frontend/storage-ui.js:199` | `data-storage-action=confirm-empty` | `plugin/tests/test_storage_ui.js::restore conflicts keep their real reason; emptying trash requires a separate confirmation and is single-flight` | 手动 · 1280×800 · 确认清空，核对显示实际删除与释放结果 |
| `storage.policy-enabled` | 勾选自动清理 → 更新策略草稿 | `frontend/storage-ui.js:182` | `data-storage-policy=automatic_enabled` | 无 | 手动 · 1280×800 · 勾选自动清理，核对更新策略草稿 |
| `storage.policy-days` | 选择保留天数 → 草稿接受 7、30 或 90 天 | `frontend/storage-ui.js:182`、`frontend/storage-ui.js:202` | `data-storage-policy=retention_days` | 无 | 手动 · 1280×800 · 选择保留天数，核对草稿接受 7、30 或 90 天 |
| `storage.policy-backups` | 调整备份保留份数 → 草稿接受 3、5 或 10 份 | `frontend/storage-ui.js:182`、`frontend/storage-ui.js:202` | `data-storage-policy=keep_backups` | 无 | 手动 · 1280×800 · 调整备份保留份数，核对草稿接受 3、5 或 10 份 |
| `storage.policy-save` | 保存策略 → 校验并恢复已保存的读数 | `frontend/storage-ui.js:200` | `data-storage-action=save-policy` | `plugin/tests/test_storage_ui.js::read errors can be retried; refresh and failed saves retain a policy draft`；`plugin/tests/test_storage_cleanup_http.py::test_policy_is_shared_by_desktop_http_and_a_new_service_instance` | 手动 · 1280×800 · 保存策略，核对校验并恢复已保存的读数 |

### 历史、导入导出与交接

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `history.section` | 点任务、报告或资料分类 → 显示该类历史及计数 | `frontend/storage-history.js:81` | `#sections`、`list(kind)` | 无 | 手动 · 1280×800 · 点任务、报告或资料分类，核对显示该类历史及计数 |
| `history.collection-more` | 点读取更多记录 → 追加旧记录页 | `frontend/storage-history.js:27` | `data-more`、`cursor` | 无 | 手动 · 1280×800 · 点读取更多记录，核对追加旧记录页 |
| `history.conversation` | 点会话条目 → 读历史会话 | `frontend/storage-history.js:63` | `navRow`、`conversation` | 无 | 手动 · 1280×800 · 点会话条目，核对读历史会话 |
| `history.conversation-more` | 点读取更早消息 → 向上加载记录 | `frontend/storage-history.js:59` | `data-more`、`append` | 无 | 手动 · 1280×800 · 点读取更早消息，核对向上加载记录 |
| `history.nav-more` | 点导航中的更多 → 追加同类别条目，失败可以重试 | `frontend/storage-history.js:67` | `moreNavigation` | 无 | 手动 · 1280×800 · 点导航中的更多，核对追加同类别条目，失败可以重试 |
| `history.artifact` | 点历史产物链接 → 新窗口打开指定产物 | `frontend/storage-history.js:12` | `artifactURL`、`target=_blank` | 无 | 手动 · 1280×800 · 点历史产物链接，核对新窗口打开指定产物 |
| `history.export` | 点历史页导出 → 调用平台导出工作区历史 | `frontend/storage-history.js:73` | `#export` | 无 | 手动 · 1280×800 · 点历史页导出，核对调用平台导出工作区历史 |
| `history.missing-history` | 打开有旧身份但无历史的目录 → 可明确从新记录开始或取消 | `desktop/src/main.cjs:250` | `history_missing`、`chooseHistory` | `desktop/test/storage.test.cjs::an identity marker with missing history reports recovery instead of registering empty data` | 手动 · 1280×800 · 打开有旧身份但无历史的目录，核对可明确从新记录开始或取消 |
| `history.relocate` | 打开现有身份的新目录 → 询问迁移目录并接续 | `desktop/src/main.cjs:254` | `relocate` | `desktop/test/storage.test.cjs::opening a new folder preserves the existing relocation choice without preparing an unnamed workspace` | 手动 · 1280×800 · 打开现有身份的新目录，核对询问迁移目录并接续 |
| `history.pick-history` | 同目录发现多份历史 → 显示各历史来源及计数，选择或取消 | `desktop/src/main.cjs:260` | `discovery.candidates` | 无 | 手动 · 1280×800 · 同目录发现多份历史，核对显示各历史来源及计数，选择或取消 |
| `history.upgrade-history` | 面对旧版历史选择升级并接续 → 保留历史后进入工作区 | `desktop/src/main.cjs:266` | `chooseHistory`、`action=upgrade` | `desktop/test/storage.test.cjs::a chosen old source is passed explicitly and a portable archive needs no execution setup` | 手动 · 1280×800 · 面对旧版历史选择升级并接续，核对保留历史后进入工作区 |
| `history.readonly-history` | 选择仅查看历史 → 打开只读历史标签 | `desktop/src/main.cjs:266` | `chooseHistory`、`action=view` | `desktop/test/storage.test.cjs::a chosen old source is passed explicitly and a portable archive needs no execution setup` | 手动 · 1280×800 · 选择仅查看历史，核对打开只读历史标签 |
| `history.fresh-history` | 条件允许时选择从全新记录开始 → 由现有代码目录建立新记录 | `desktop/src/main.cjs:266` | `can_start_fresh`、`action=fresh` | `desktop/test/storage.test.cjs::a user can explicitly open a V1 folder with fresh V2 records and cancel without writing` | 手动 · 1280×800 · 条件允许时选择从全新记录开始，核对由现有代码目录建立新记录 |
| `history.export-dialog` | 导出工作区 → 可选 .crewpack 保存路径，完成提示路径和保存版本 | `desktop/src/main.cjs:270` | `showSaveDialog`、`/api/workspace-data/export` | `plugin/tests/test_storage_v2_packages.py::test_export_is_scope_complete_and_contains_no_device_or_unrelated_user_data` | 手动 · 1280×800 · 导出工作区，核对可选 .crewpack 保存路径，完成提示路径和保存版本 |
| `history.export-lan` | 从局域网主机导出 → 显示下载进度并保存到本机选择的路径 | `desktop/src/main.cjs:274` | `/api/workspace-data/download`、`storageProgress` | `desktop/test/core.test.cjs::a host download streams into the chosen file and a failed download leaves no file behind` | 手动 · 1280×800 · 从局域网主机导出，核对显示下载进度并保存到本机选择的路径 |
| `history.restore-package` | 导入选择恢复历史 → 按原身份打开只读工作区 | `desktop/src/main.cjs:292-294` | `/api/workspace-data/import`、`restore_readonly` | `plugin/tests/test_storage_v2_packages.py::test_restore_readonly_keeps_business_ids_quarantined_without_execution_authority` | 手动 · 1280×800 · 导入选择恢复历史，核对按原身份打开只读工作区 |
| `history.fork-package` | 导入选择复制为新工作区 → 创建副本并询问是否选择目录接续 | `desktop/src/main.cjs:292-299` | `fork`、`activateWorkspace` | `plugin/tests/test_storage_v2_packages.py::test_fork_preserves_history_bytes_and_rewrites_composite_and_report_references` | 手动 · 1280×800 · 导入选择复制为新工作区，核对创建副本并询问是否选择目录接续 |
| `history.handoff-export` | 选择生成交接材料和保存目录 → 显示材料路径及接续方式 | `desktop/src/main.cjs:321` | `/api/workspace-data/handoff` | 无 | 手动 · 1280×800 · 选择生成交接材料和保存目录，核对显示材料路径及接续方式 |
| `history.handoff-copy` | 点复制接续提示 → 剪贴板得到阅读材料的提示 | `desktop/src/main.cjs:329` | `clipboard.writeText` | 无 | 手动 · 1280×800 · 点复制接续提示，核对剪贴板得到阅读材料的提示 |
| `history.handoff-reveal` | 点查看文件夹 → 系统文件管理器定位交接材料 | `desktop/src/main.cjs:330` | `shell.showItemInFolder` | 无 | 手动 · 1280×800 · 点查看文件夹，核对系统文件管理器定位交接材料 |

### 局域网访问与设备管理

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `remote.bind` | 选择网卡地址 → 刷新当前可用侦听选项 | `frontend/remote-access-ui.js:91` | `name=remote-bind` | `plugin/tests/test_remote_access_ui.js::selecting ZeroTier preserves a draft without starting access, then binds only the chosen address` | 手动 · 1280×800 · 选择网卡地址，核对刷新当前可用侦听选项 |
| `remote.start` | 点开启远程访问 → 在所选地址启动并展示访问链接 | `frontend/remote-access-ui.js:97`、`frontend/remote-access-ui.js:101` | `data-remote-action=start` | `plugin/tests/test_remote_access_ui.js::selecting ZeroTier preserves a draft without starting access, then binds only the chosen address` | 手动 · 1280×800 · 点开启远程访问，核对在所选地址启动并展示访问链接 |
| `remote.switch` | 切换侦听地址 → 停止旧监听并按新地址启动，失败显示详情 | `frontend/remote-access-ui.js:100` | `data-remote-action=switch` | `plugin/tests/test_remote_access_ui.js::switching makes one stop and one start and does not submit a second lifecycle action while pending` | 手动 · 1280×800 · 切换侦听地址，核对停止旧监听并按新地址启动，失败显示详情 |
| `remote.stop` | 点关闭远程访问 → 清除当前配对信息并更新远程状态 | `frontend/remote-access-ui.js:101`、`frontend/remote-access-ui.js:103` | `data-remote-action=stop` | `plugin/tests/test_remote_access_ui.js::the current all-network listener is selected and disappears after stopping` | 手动 · 1280×800 · 点关闭远程访问，核对清除当前配对信息并更新远程状态 |
| `remote.pair` | 点生成配对码 → 显示新配对码与到期时间 | `frontend/remote-access-ui.js:101`、`frontend/remote-access-ui.js:103` | `data-remote-action=pair`、`expires_at` | `plugin/tests/test_remote_access_ui.js::a pairing code comes with a copyable connection link per access address` | 手动 · 1280×800 · 点生成配对码，核对显示新配对码与到期时间 |
| `remote.copy` | 点复制链接 → 显示短暂复制反馈，失败提示手动复制 | `frontend/remote-access-ui.js:95` | `data-remote-action=copy-link` | `plugin/tests/test_remote_access_ui.js::a pairing code comes with a copyable connection link per access address` | 手动 · 1280×800 · 点复制链接，核对显示短暂复制反馈，失败提示手动复制 |
| `remote.revoke` | 点撤销指定设备 → 刷新配对设备列表 | `frontend/remote-access-ui.js:101` | `data-remote-action=revoke`、`data-device-id` | `plugin/tests/test_remote_access_ui.js::devices show type and status and use the revoke actions` | 手动 · 1280×800 · 点撤销指定设备，核对刷新配对设备列表 |
| `remote.refresh` | 手动刷新或等待轮询 → 重读监听与设备状态 | `frontend/remote-access-ui.js:94`、`frontend/remote-access-ui.js:108` | `data-remote-action=refresh`、`setInterval` | 无 | 手动 · 1280×800 · 手动刷新或等待轮询，核对重读监听与设备状态 |
| `remote.lifecycle-error` | 远程操作失败或后续读取失败 → 保留原因并刷新现态 | `frontend/remote-access-ui.js:104` | `actionError`、`refresh` | `plugin/tests/test_remote_access_ui.js::stop rejection never reaches start, and a later read cannot replace the real operation error` | 手动 · 1280×800 · 模拟动作已成功但读取失败，核对请求次数 |

### 连接鉴权与浏览器配对

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `access.unavailable` | 连接上下文缺失或异常 → 显示重读入口和错误原因 | `frontend/access-context.js:17`、`frontend/access-context.js:43` | `#access-notice`、`/api/access/context` | 无 | 手动 · 1280×800 · 连接上下文缺失或异常，核对显示重读入口和错误原因 |
| `access.expired` | 远程 API 返回 401 → 浏览器显示重新连接，桌面显示返回首页重新配对 | `frontend/access-context.js:27`、`frontend/access-context.js:37` | `crew-access-expired`、`#access-notice` | 无 | 手动 · 1280×800 · 远程 API 返回 401，核对浏览器显示重新连接，桌面显示返回首页重新配对 |
| `access.controls` | 打开远程页面 → 按客户端能力显示本机控制 | `frontend/access-context.js:11`、`frontend/style.css:66`、`frontend/style.css:998` | `data-access-local-controls`、`data-access-local-desktop` | `plugin/tests/test_remote_integration.py::test_login_protects_pages_business_events_files_and_local_only_management` | 手动 · 1280×800 · 在本地桌面、配对浏览器和局域网桌面分别核对设置、收编和文件操作 |
| `access.pair-link` | 用一次性配对链接打开网页 → 自动填码并清除地址栏片段 | `frontend/remote-login.html:9` | `#code`、`history.replaceState` | 无 | 手动 · 1280×800 · 用一次性配对链接打开网页，核对自动填码并清除地址栏片段 |
| `access.pair-submit` | 输入配对码并连接 → 提交后定位回请求路径，错误保留表单 | `frontend/remote-login.html:11` | `/remote/login`、`nextPath`、`#status` | `plugin/tests/test_remote_integration.py::test_login_protects_pages_business_events_files_and_local_only_management`；`plugin/tests/test_remote_routes.py::test_login_return_preserves_project_and_fragment_but_never_changes_origin` | 手动 · 1280×800 · 输入配对码并连接，核对提交后定位回请求路径，错误保留表单 |

### 桌面窗口、标签、预览与托盘

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `desktop.home` | 点击顶栏品牌 → 返回工作区首页 | `desktop/src/preload.cjs:74`、`desktop/src/main.cjs:188` | `.crew-chrome-brand`、`desktop:chrome-action` | 无 | 手动 · 隔离桌面 · 点击顶栏品牌，核对返回工作区首页 |
| `desktop.home-plus` | 点击标签栏加号 → 切到可打开工作区的首页 | `desktop/src/preload.cjs:61` | `.crew-tab-add` | 无 | 手动 · 隔离桌面 · 点击标签栏加号，核对切到可打开工作区的首页 |
| `desktop.back` | 点返回 → 当前视图回到上一页，无法后退时按钮禁用 | `desktop/src/preload.cjs:75`、`desktop/src/main.cjs:192` | `backButton`、`canGoBack` | `desktop/test/tabs.test.cjs::cycling, bounds, back, reload and keyboard callbacks target the active page` | 手动 · 隔离桌面 · 点返回，核对当前视图回到上一页，无法后退时按钮禁用 |
| `desktop.assistant` | 点顶栏全局助手 → 打开或选中独立助手标签 | `desktop/src/preload.cjs:77`、`desktop/src/main.cjs:189` | `.crew-chrome-assistant`、`tabs.openAssistant` | `desktop/test/tabs.test.cjs::the assistant is one global conversation across workspace tabs with no project activity polling` | 手动 · 隔离桌面 · 点顶栏全局助手，核对打开或选中独立助手标签 |
| `desktop.settings` | 点顶栏设置 → 打开桌面全局设置 | `desktop/src/preload.cjs:77`、`desktop/src/main.cjs:191` | `.crew-chrome-settings` | 无 | 手动 · 隔离桌面 · 点顶栏设置，核对打开桌面全局设置 |
| `desktop.admin` | 点系统管理员 → 打开桌面健康面板 | `desktop/src/preload.cjs:77`、`desktop/src/main.cjs:190` | `.crew-chrome-admin` | 无 | 手动 · 隔离桌面 · 点系统管理员，核对打开桌面健康面板 |
| `desktop.app-menu` | 点应用菜单 → 打开原生菜单并同步展开状态 | `desktop/src/preload.cjs:28`、`desktop/src/main.cjs:198` | `.crew-chrome-menu`、`aria-expanded` | 无 | 手动 · 隔离桌面 · 点应用菜单，核对打开原生菜单并同步展开状态 |
| `desktop.tab-select` | 点标签 → 激活其视图与页面焦点 | `desktop/src/preload.cjs:55`、`desktop/src/tabs.cjs:243` | `data-tab-id`、`aria-selected` | `desktop/test/tabs.test.cjs::projects deduplicate by stable name and switching retains each document without reload` | 手动 · 隔离桌面 · 点标签，核对激活其视图与页面焦点 |
| `desktop.tab-close` | 点工作区标签关闭 → 关闭目标并选择保留标签 | `desktop/src/preload.cjs:58`、`desktop/src/tabs.cjs:321` | `.crew-tab-close` | `desktop/test/tabs.test.cjs::closing selects a neighbor, keeps home, and owns no service shutdown` | 手动 · 隔离桌面 · 点工作区标签关闭，核对关闭目标并选择保留标签 |
| `desktop.tab-arrows` | 标签聚焦时按左右键、Home 或 End → 循环或跳首尾标签 | `desktop/src/preload.cjs:56` | `data-tab-id` | 无 | 手动 · 隔离桌面 · 标签聚焦时按左右键、Home 或 End，核对循环或跳首尾标签 |
| `desktop.tab-focus` | 标签列表更新 → 保留滚动及原焦点，切标签时滚至激活项 | `desktop/src/preload.cjs:37`、`desktop/src/preload.cjs:66` | `scrollLeft`、`lastActiveTab` | 无 | 手动 · 隔离桌面 · 标签列表更新，核对保留滚动及原焦点，切标签时滚至激活项 |
| `desktop.tab-signal` | 工作区标签状态变化 → 显示加载、错误、过期或正在工作标记 | `desktop/src/preload.cjs:45` | `.crew-tab-signal`、`data-tone` | 无 | 手动 · 隔离桌面 · 工作区标签状态变化，核对显示加载、错误、过期或正在工作标记 |
| `desktop.tab-count` | 工作区有活动计数 → 显示数字，超过 99 时显示 99+ | `desktop/src/preload.cjs:48` | `.crew-tab-badge` | 无 | 手动 · 隔离桌面 · 工作区有活动计数，核对显示数字，超过 99 时显示 99+ |
| `desktop.tab-unread` | 工作区有未读动态 → 标签出现未读点 | `desktop/src/preload.cjs:49` | `.crew-tab-unread` | 无 | 手动 · 隔离桌面 · 工作区有未读动态，核对标签出现未读点 |
| `desktop.tab-preview` | 悬停或聚焦项目标签约 280 毫秒 → 弹出该工作区活动预览 | `desktop/src/preload.cjs:50-51`、`desktop/src/activity-preview.cjs:11` | `tabPreviewTimer`、`desktop:activity-preview-ready` | 无 | 手动 · 隔离桌面 · 悬停或聚焦项目标签约 280 毫秒，核对弹出该工作区活动预览 |
| `desktop.tab-preview-dismiss` | 离开、失焦或按 Escape → 收起预览，进入预览时延迟取消 | `desktop/src/preload.cjs:52`、`desktop/src/preload.cjs:56`、`desktop/src/activity-preview.js:18` | `tab-preview-hide`、`activity-preview-hover` | 无 | 手动 · 隔离桌面 · 离开、失焦或按 Escape，核对收起预览，进入预览时延迟取消 |
| `desktop.tab-preview-open` | 在活动预览点打开 → 切至该工作区标签 | `desktop/src/activity-preview.js:15` | `crewActivity.open` | 无 | 手动 · 隔离桌面 · 在活动预览点打开，核对切至该工作区标签 |
| `desktop.tab-recovery` | 标签加载失败后点重试或返回首页 → 重载当前标签或切首页，其他标签保留 | `desktop/src/preload.cjs:63`、`desktop/src/preload.cjs:80` | `#crew-tab-recovery` | `desktop/test/tabs.test.cjs::a failed page never slides; the recovery controls appear directly` | 手动 · 隔离桌面 · 标签加载失败后点重试或返回首页，核对重载当前标签或切首页，其他标签保留 |
| `desktop.startup-recovery` | 启动错误页点重试或首页 → 重新打开首页或返回工作区选择 | `desktop/src/preload.cjs:82` | `#retry`、`#home` | 无 | 手动 · 隔离桌面 · 启动错误页点重试或首页，核对重新打开首页或返回工作区选择 |
| `desktop.shortcut-cycle` | 按 Ctrl／⌘+Tab 或加 Shift → 依次或逆序切换标签 | `desktop/src/main.cjs:337` | `before-input-event`、`tabs.cycle` | 无 | 手动 · 隔离桌面 · 按 Ctrl／⌘+Tab 或加 Shift，核对依次或逆序切换标签 |
| `desktop.shortcut-home` | 按 Ctrl／⌘+T → 回到工作区首页 | `desktop/src/main.cjs:338` | `before-input-event`、`showHome` | 无 | 手动 · 隔离桌面 · 按 Ctrl／⌘+T，核对回到工作区首页 |
| `desktop.shortcut-close` | 按 Ctrl／⌘+W → 关闭当前标签 | `desktop/src/main.cjs:339` | `tabs.close` | 无 | 手动 · 隔离桌面 · 按 Ctrl／⌘+W，核对关闭当前标签 |
| `desktop.shortcut-reload` | 按 Ctrl／⌘+R 或 F5 → 重载当前标签 | `desktop/src/main.cjs:340` | `tabs.reload` | 无 | 手动 · 隔离桌面 · 按 Ctrl／⌘+R 或 F5，核对重载当前标签 |
| `desktop.shortcut-back` | 按 Alt+左方向键或 Mac 的 ⌘+[ → 当前标签返回历史 | `desktop/src/main.cjs:341`、`desktop/src/menu.cjs:18` | `isBackShortcut` | 无 | 手动 · 隔离桌面 · 按 Alt+左键方向键或 Mac 的 ⌘+[，核对当前标签返回历史 |
| `desktop.workspace-reuse` | 重新打开已有工作区 → 激活对应工作区标签 | `desktop/src/tabs.cjs:104`、`desktop/src/tabs.cjs:243` | `_openProject`、`_select` | `desktop/test/tabs.test.cjs::projects deduplicate by stable name and switching retains each document without reload` | 手动 · 隔离桌面 · 重新打开已有工作区，核对激活对应工作区标签 |
| `desktop.tab-visibility` | 切换桌面标签 → 页面收到可见性变化，保留较早工作区状态 | `desktop/src/tabs.cjs:86` | `crew-tab-visibility`、`_syncPageVisibility` | `desktop/test/tabs.test.cjs::workspace and assistant pages receive active-tab visibility while their documents stay mounted` | 手动 · 隔离桌面 · 切换桌面标签，核对页面收到可见性变化，保留较早工作区状态 |
| `desktop.external` | 点击外部链接或请求新窗 → 策略分流到系统应用或拦截 | `desktop/src/tabs.cjs:129`、`desktop/src/tabs.cjs:148`、`desktop/src/main.cjs:201` | `will-navigate`、`setWindowOpenHandler` | `desktop/test/tabs.test.cjs::external clicks open a browser, but external redirects and subframes never do` | 手动 · 隔离桌面 · 点击外部链接或请求新窗，核对策略分流到系统应用或拦截 |
| `desktop.same-origin` | 点击 Crew 内的工作区或全局页面链接 → 在适用的标签中打开 | `desktop/src/tabs.cjs:111`、`desktop/src/tabs.cjs:129` | `_destination`、`_guard` | `desktop/test/tabs.test.cjs::cross-workspace task links retain their hash and select the existing document without reloading its draft` | 手动 · 隔离桌面 · 点击 Crew 内的工作区或全局页面链接，核对在适用的标签中打开 |
| `desktop.download` | 从 Crew 下载文件 → 显示本机保存对话框 | `desktop/src/main.cjs:144` | `will-download`、`setSaveDialogOptions` | 无 | 手动 · 隔离桌面 · 从 Crew 下载文件，核对显示本机保存对话框 |
| `desktop.about` | 选择关于 Crew → 显示版本弹窗 | `desktop/src/main.cjs:344`、`desktop/src/main.cjs:916` | `app-about` | 无 | 手动 · 隔离桌面 · 选择关于 Crew，核对显示版本弹窗 |
| `desktop.logs` | 选查看日志文件夹 → 打开日志目录或提示尚无日志 | `desktop/src/main.cjs:349`、`desktop/src/main.cjs:916` | `app-logs` | 无 | 手动 · 隔离桌面 · 选查看日志文件夹，核对打开日志目录或提示尚无日志 |
| `desktop.platform-edit-menu` | 使用原生编辑菜单 → 执行撤销、重做、剪切、复制、粘贴或全选 | `desktop/src/menu.cjs:8` | `editMenu`、`role` | `desktop/test/mac-platform.test.cjs::Mac uses the native app, edit and window menus, retaining the existing Windows structure` | 手动 · 隔离桌面 · 分别在 Windows 和 macOS 文本框逐项操作 |
| `desktop.window-hide` | 关闭主窗口 → Crew 留在后台且可由托盘重新打开 | `desktop/src/main.cjs:914` | `close`、`Tray` | `desktop/test/main-lifecycle.test.cjs::normal close keeps the shell alive and explicit quit cleans it up exactly once` | 手动 · 隔离桌面 · 在隔离桌面窗口点关闭，再从托盘呼回同一窗口 |
| `desktop.tray-open` | 双击托盘或选打开 Crew → 显示已有主窗口 | `desktop/src/main.cjs:924`、`desktop/src/main.cjs:926` | `showWindow` | 无 | 手动 · 隔离桌面 · 双击托盘或选打开 Crew，核对显示已有主窗口 |
| `desktop.tray-home` | 在托盘选择工作区首页 → 回到桌面首页 | `desktop/src/main.cjs:925` | `showHome` | 无 | 手动 · 隔离桌面 · 在托盘选择工作区首页，核对回到桌面首页 |
| `desktop.tray-create` | 在托盘选择新建工作区 → 打开创建表单 | `desktop/src/main.cjs:925` | `showHome(new)` | 无 | 手动 · 隔离桌面 · 在托盘选择新建工作区，核对打开创建表单 |
| `desktop.tray-existing` | 在托盘选择打开已有项目 → 启动文件夹选择流程 | `desktop/src/main.cjs:925` | `showHome(open)` | 无 | 手动 · 隔离桌面 · 在托盘选择打开已有项目，核对启动文件夹选择流程 |
| `desktop.tray-restart` | 在托盘选重启 → 进入重启流程并按繁忙情况提示 | `desktop/src/main.cjs:363`、`desktop/src/main.cjs:925` | `tray-restart`、`restartCrew` | 无 | 手动 · 隔离桌面 · 在托盘选重启，核对进入重启流程并按繁忙情况提示 |
| `desktop.tray-quit` | 在托盘选退出 Crew → 退出桌面应用 | `desktop/src/main.cjs:925` | `app.quit` | 无 | 手动 · 隔离桌面 · 在托盘选退出 Crew，核对退出桌面应用 |
| `desktop.workspace-home-menu` | 选工作区首页或按 Ctrl／⌘+Shift+O → 切首页 | `desktop/src/main.cjs:388` | `workspace-home` | 无 | 手动 · 隔离桌面 · 选工作区首页或按 Ctrl／⌘+Shift+O，核对切首页 |
| `desktop.new-menu` | 选新建工作区或按 Ctrl／⌘+N → 打开创建表单 | `desktop/src/main.cjs:390` | `workspace-new` | 无 | 手动 · 隔离桌面 · 选新建工作区或按 Ctrl／⌘+N，核对打开创建表单 |
| `desktop.existing-menu` | 选打开已有项目或按 Ctrl／⌘+O → 启动文件夹选择 | `desktop/src/main.cjs:391` | `workspace-open` | 无 | 手动 · 隔离桌面 · 选打开已有项目或按 Ctrl／⌘+O，核对启动文件夹选择 |
| `desktop.prefs-menu` | 选设置或按 Ctrl／⌘+逗号 → 打开桌面设置 | `desktop/src/main.cjs:402` | `app-settings` | 无 | 手动 · 隔离桌面 · 选设置或按 Ctrl／⌘+逗号，核对打开桌面设置 |
| `desktop.tools-menu` | 在本机应用菜单选择工具与更新 → 打开工具页 | `desktop/src/main.cjs:401` | `tools-updates` | 无 | 手动 · 隔离桌面 · 在本机应用菜单选择工具与更新，核对打开工具页 |
| `desktop.settings-menu` | 选当前工作区设置 → 对当前可写工作区打开设置 | `desktop/src/main.cjs:396` | `workspace-settings` | 无 | 手动 · 隔离桌面 · 选当前工作区设置，核对对当前可写工作区打开设置 |
| `desktop.quit-menu` | 选退出 Crew 或使用平台退出快捷键 → 退出应用 | `desktop/src/main.cjs:412` | `app-quit` | 无 | 手动 · 隔离桌面 · 选退出 Crew 或使用平台退出快捷键，核对退出应用 |
| `desktop.close-menu` | 选收起到托盘或关闭窗口 → 隐藏已有窗口 | `desktop/src/main.cjs:410`、`desktop/src/main.cjs:914` | `window-close` | 无 | 手动 · 隔离桌面 · 选收起到托盘或关闭窗口，核对隐藏已有窗口 |
| `desktop.activate` | 第二次启动或 Mac Dock 点击 → 重新显示现有 Crew 窗口 | `desktop/src/main.cjs:771` | `second-instance`、`activate` | `desktop/test/main-lifecycle.test.cjs::Mac launch waits for the shell PATH and Dock activation restores the hidden window` | 手动 · 隔离桌面 · 第二次启动或 Mac Dock 点击，核对重新显示现有 Crew 窗口 |
| `desktop.restart-confirm` | 本机有会话工作时选择重启 → 显示受影响数量和名称并等待用户选择 | `desktop/src/main.cjs:371` | `restartCrew`、`showMessageBox` | 无 | 手动 · 隔离桌面 · 本机有会话工作时选择重启，核对显示受影响数量和名称并等待用户选择 |
| `desktop.permission-prompt` | 桌面权限请求进入原生对话框 → 可允许或暂不允许 | `desktop/src/main.cjs:888-895` | `installPermissions`、`requestPermission` | `desktop/test/permissions.test.cjs::sensitive permissions ask on use; denial and navigation never grant access` | 手动 · 隔离桌面 · 桌面权限请求进入原生对话框，核对可允许或暂不允许 |
| `desktop.preview-hide-window` | 窗口失焦或缩放 → 关闭活动预览 | `desktop/src/main.cjs:882` | `activityPreview.hide` | 无 | 手动 · 隔离桌面 · 窗口失焦或缩放，核对关闭活动预览 |
| `desktop.tab-slide` | 在适用的项目和独立页间切换 → 过渡后定位原生焦点 | `desktop/src/tabs.cjs:243`、`desktop/src/tabs.cjs:274` | `_select`、`_frame` | `desktop/test/tabs.test.cjs::project to assistant pushes both pages left, then focuses the assistant at rest` | 手动 · 隔离桌面 · 在适用的项目和独立页间切换，核对过渡后定位原生焦点 |
| `desktop.preview-content` | 预览打开时状态变化 → 更新项目动态及过期提示 | `desktop/src/activity-preview.js:5-14` | `#summary`、`#items`、`#updated` | `desktop/test/tabs.test.cjs::activity fetch failure keeps the last facts and explicitly marks them stale` | 手动 · 隔离桌面 · 预览打开时状态变化，核对更新项目动态及过期提示 |
| `desktop.startup-fatal` | 应用初始化发生致命错误 → 显示原生重试或关闭流程 | `desktop/src/main.cjs:940-945` | `dialog.showMessageBox`、`app.relaunch` | 无 | 手动 · 隔离桌面 · 应用初始化发生致命错误，核对显示原生重试或关闭流程 |

### 桌面工作区首页

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `desktop-home.open-project` | 点工作区行 → 打开其标签 | `desktop/src/home.js:71` | `openWorkspace` | `desktop/test/tabs.test.cjs::projects deduplicate by stable name and switching retains each document without reload` | 手动 · 隔离桌面 · 点工作区行，核对打开其标签 |
| `desktop-home.project-settings` | 点行内设置 → 打开该工作区设置 | `desktop/src/home.js:75` | `.project-settings` | 无 | 手动 · 隔离桌面 · 点行内设置，核对打开该工作区设置 |
| `desktop-home.actions-reveal` | 悬停或键盘聚焦工作区行 → 显示行上的快捷操作 | `desktop/src/home.css:71` | `.project-action`、`:focus-within` | 无 | 截图走查 · 在桌面 1280 先悬停，再用 Tab 验证同一行 |
| `desktop-home.export` | 点导出 → 进入工作区包保存流程 | `desktop/src/home.js:78` | `crewDesktop.storageExport` | 无 | 手动 · 隔离桌面 · 点导出，核对进入工作区包保存流程 |
| `desktop-home.history` | 点历史 → 打开该工作区的存储历史 | `desktop/src/home.js:80` | `crewDesktop`、`.project-action` | 无 | 手动 · 隔离桌面 · 点历史，核对打开该工作区的存储历史 |
| `desktop-home.activate` | 点接续 → 选择该恢复工作区的执行目录及关联方式 | `desktop/src/home.js:81`、`desktop/src/home.js:136` | `project.can_activate`、`select` | 无 | 手动 · 隔离桌面 · 点接续，核对选择该恢复工作区的执行目录及关联方式 |
| `desktop-home.import` | 点导入工作区并选包 → 校验后选择恢复原身份、复制或取消 | `desktop/src/home.js:158`、`desktop/src/main.cjs:289` | `#import-workspace`、`/api/workspace-data/preview` | 无 | 手动 · 隔离桌面 · 点导入工作区并选包，核对校验后选择恢复原身份、复制或取消 |
| `desktop-home.new` | 点新建 → 打开表单并聚焦名称 | `desktop/src/home.js:159`、`desktop/src/home.js:192` | `#create-panel`、`#name` | 无 | 手动 · 隔离桌面 · 点新建，核对打开表单并聚焦名称 |
| `desktop-home.choose-parent` | 点选择父目录 → 本机弹原生目录框，远程弹主机目录框 | `desktop/src/home.js:195` | `#choose-parent`、`hostFolder` | 无 | 手动 · 隔离桌面 · 点选择父目录，核对本机弹原生目录框，远程弹主机目录框 |
| `desktop-home.create-submit` | 提交新工作区表单 → 创建并打开新项目 | `desktop/src/home.js:199` | `#create-form`、`crewDesktop.createWorkspace` | `desktop/test/core.test.cjs::workspace folders are checked, resolved and created by the backend for both local and LAN connections` | 手动 · 隔离桌面 · 提交新工作区表单，核对创建并打开新项目 |
| `desktop-home.create-cancel` | 点取消或按 Escape → 收起创建区并聚焦新建按钮 | `desktop/src/home.js:193`、`desktop/src/home.js:206` | `#create-panel`、`#new` | 无 | 手动 · 隔离桌面 · 点取消或按 Escape，核对收起创建区并聚焦新建按钮 |
| `desktop-home.existing` | 点打开已有项目 → 浏览文件夹并打开选中项目 | `desktop/src/home.js:191` | `#existing`、`openExisting` | `desktop/test/core.test.cjs::workspace folders are checked, resolved and created by the backend for both local and LAN connections` | 手动 · 隔离桌面 · 点打开已有项目，核对浏览文件夹并打开选中项目 |
| `desktop-home.refresh` | 点刷新或回到首页焦点 → 更新工作区列表 | `desktop/src/home.js:203`、`desktop/src/home.js:310` | `#refresh`、`focus` | 无 | 手动 · 隔离桌面 · 点刷新或回到首页焦点，核对更新工作区列表 |
| `desktop-home.switch-connection` | 点连接状态入口 → 按当前模式打开配对或切本机设置 | `desktop/src/home.js:205` | `#connection-switch`、`CrewHomeSettings.show` | `plugin/tests/test_operations_ui.js::a home-page shortcut opens the connection tab and hands its intent to the launch settings once` | 手动 · 隔离桌面 · 点连接状态入口，核对按当前模式打开配对或切本机设置 |
| `desktop-home.remote-banner` | 主机断线、授权过期或版本不同 → 显示对应提示及恢复入口 | `desktop/src/home.js:268` | `#remote-banner`、`connection` | 无 | 手动 · 隔离桌面 · 主机断线、授权过期或版本不同，核对显示对应提示及恢复入口 |
| `desktop-home.remote-repair-open` | 点重新配对 → 显示修复表单并聚焦配对码 | `desktop/src/home.js:237` | `.remote-repair` | 无 | 手动 · 隔离桌面 · 点重新配对，核对显示修复表单并聚焦配对码 |
| `desktop-home.remote-repair-cancel` | 点取消重新配对 → 收起表单并恢复横幅 | `desktop/src/home.js:252` | `repairing`、`renderBanner` | 无 | 手动 · 隔离桌面 · 点取消重新配对，核对收起表单并恢复横幅 |
| `desktop-home.remote-repair-submit` | 提交配对码或带码链接与设备名 → 保存新令牌并重新打开连接 | `desktop/src/home.js:255` | `crewDesktop.remoteBackendPair` | `desktop/test/launch-settings.test.cjs::re-pairing a saved host passes only the address, code and device name` | 手动 · 隔离桌面 · 提交配对码或带码链接与设备名，核对保存新令牌并重新打开连接 |
| `desktop-home.remote-local` | 点切回本机后台 → 应用本机配置并重开 | `desktop/src/home.js:231` | `launchSettingsApply` | `desktop/test/launch-settings.test.cjs::a LAN window switches back to this computer without probing the host as a borrowed local service` | 手动 · 隔离桌面 · 点切回本机后台，核对应用本机配置并重开 |
| `desktop-home.settings-nav` | 点首页设置 → 加载全局设置区，失败时有重试 | `desktop/src/home-settings.js:70` | `#nav-settings`、`CrewHomeSettings` | 无 | 手动 · 隔离桌面 · 点首页设置，核对加载全局设置区，失败时有重试 |
| `desktop-home.theme` | 点击桌面主题选项 → 更新原生主题 | `desktop/src/home-settings.js:22` | `data-desktop-theme` | `desktop/test/appearance.test.cjs::one desktop display mode drives Electron themeSource and survives restarts` | 手动 · 隔离桌面 · 点击桌面主题选项，核对更新原生主题 |
| `desktop-home.onboarding-auto` | 本机首次启动（无工作区、引导未完成也未跳过、无待执行菜单动作）→ 自动打开新手引导；连接局域网主机时不自动出现 | `desktop/src/home.js`（`maybeOnboard`）、`desktop/src/home-onboarding.js`（`shouldOpen`） | `/api/onboarding` | `desktop/test/home-onboarding.test.cjs::the guide opens by itself only on this computer's first start` | 手动 · 隔离桌面 · 全新 HOME 启动 |
| `desktop-home.onboarding-steps` | 点左侧步骤、下一步、上一步 → 切换四步并把焦点放到标题 | `desktop/src/home-onboarding.js` | `data-ob-act=step/next/back` | 无 | 手动 · 隔离桌面 · 1280、758、375 各走一遍 |
| `desktop-home.onboarding-runtime` | 引导第一步显示 Claude Code / Codex 的安装与登录状态，点可用卡片设为默认大脑，选模型与 Codex 速度档位 | `desktop/src/home-onboarding.js`、`plugin/crew/runtime_catalog.py`（`readiness`） | `/api/runtimes/readiness`、`CrewSettings.speedChoices` | `desktop/test/home-onboarding.test.cjs`；`plugin/tests/test_onboarding.py::ReadinessTests` | 手动 · 隔离桌面 · 核对状态与选择 |
| `desktop-home.onboarding-recheck` | 点重新检测 → 重新读取载体状态与 AI 设置，保留仍可用的选择 | `desktop/src/home-onboarding.js`（`detect`） | `data-ob-act=recheck` | 无 | 手动 · 隔离桌面 · 登录前后各点一次 |
| `desktop-home.onboarding-brain-save` | 第一步点下一步 → 默认大脑有变化时保存到 AI 设置，失败显示真实原因并停在本步 | `desktop/src/home-onboarding.js`（`saveBrain`） | `crewDesktop.settingsSave` | 无 | 手动 · 隔离桌面 · 保存后到设置 → AI 大脑核对 |
| `desktop-home.onboarding-appearance` | 引导第二步切显示模式、拖字号（松手保存）、恢复标准 → 立即全桌面生效 | `desktop/src/home-onboarding.js` | `desktop:theme-set`、`desktop:font-scale-set` | `desktop/test/appearance.test.cjs::one desktop font size is kept beside the display mode without either overwriting the other` | 手动 · 隔离桌面 · 切换后看首页与工作区 |
| `desktop-home.onboarding-workspace` | 引导第四步点打开已有文件夹或新建空白文件夹 → 记为完成、关闭引导，转入首页原有的打开流程或新建表单 | `desktop/src/home-onboarding.js`、`desktop/src/home.js`（`openOnboarding`） | `homeActions`、`/api/onboarding` | 无 | 手动 · 隔离桌面 · 两种入口各走一遍 |
| `desktop-home.onboarding-skip` | 点跳过引导、完成引导或按 Escape → 记为跳过或完成后关闭，之后不再自动出现；记录失败时显示原因并提供“先关闭，下次启动再提醒” | `desktop/src/home-onboarding.js`（`complete`） | `/api/onboarding` | `desktop/test/home-onboarding.test.cjs::a failed save keeps the real reason and offers an honest way out`；`plugin/tests/test_onboarding.py::OnboardingHttpTests` | 手动 · 隔离桌面 · 跳过后重启核对 |
| `desktop-home.onboarding-reopen` | 设置 → 外观点重新打开新手引导 → 回到工作区页并打开引导 | `desktop/src/home-settings.js` | `data-onboarding-open`、`CrewHomeOnboarding.open` | 无 | 手动 · 隔离桌面 · 点击核对 |
| `desktop-home.font` | 设置 → 外观拖字号滑杆或点恢复标准 → 松手保存桌面共用字号 | `desktop/src/home-settings.js` | `data-desktop-font`、`data-font-reset` | `desktop/test/appearance.test.cjs::one desktop font size is kept beside the display mode without either overwriting the other` | 手动 · 隔离桌面 · 拖动后看工作区字号 |

### 主机目录选择器

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `folders.single` | 单击目录 → 标记候选目录 | `desktop/src/home-folders.js:82` | `.hf-entry` | 无 | 手动 · 隔离桌面 · 单击目录，核对标记候选目录 |
| `folders.enter` | 双击目录 → 进入该目录 | `desktop/src/home-folders.js:83` | `.hf-entry`、`open` | 无 | 手动 · 隔离桌面 · 双击目录，核对进入该目录 |
| `folders.key-select` | 按方向上下键、Home 或 End → 移动目录选中行 | `desktop/src/home-folders.js:84` | `selected` | 无 | 手动 · 隔离桌面 · 按方向上下键、Home 或 End，核对移动目录选中行 |
| `folders.key-enter` | 按 Enter 或右方向键 → 进入所选目录 | `desktop/src/home-folders.js:87` | `open(selected)` | 无 | 手动 · 隔离桌面 · 按 Enter 或右键方向键，核对进入所选目录 |
| `folders.key-parent` | 按 Backspace 或左方向键 → 返回父目录 | `desktop/src/home-folders.js:87` | `listing.parent` | 无 | 手动 · 隔离桌面 · 按 Backspace 或左键方向键，核对回到父目录 |
| `folders.up` | 点击上一级 → 浏览父目录 | `desktop/src/home-folders.js:91` | `listing.parent` | 无 | 手动 · 隔离桌面 · 点击上一级，核对浏览父目录 |
| `folders.home` | 点主目录 → 从主机默认目录重新浏览 | `desktop/src/home-folders.js:92` | `load` | 无 | 手动 · 隔离桌面 · 点主目录，核对从主机默认目录重新浏览 |
| `folders.root` | 切换根位置 → 浏览对应驱动器或根目录 | `desktop/src/home-folders.js:93` | `roots` | 无 | 手动 · 隔离桌面 · 切换根位置，核对浏览对应驱动器或根目录 |
| `folders.hidden` | 切换显示隐藏目录 → 重画可见目录并清除选择 | `desktop/src/home-folders.js:94` | `hiddenBox`、`visible` | 无 | 手动 · 隔离桌面 · 切换显示隐藏目录，核对重画可见目录并清除选择 |
| `folders.confirm` | 点确定 → 返回所选路径给原表单 | `desktop/src/home-folders.js:97` | `chosen`、`finish` | 无 | 手动 · 隔离桌面 · 点确定，核对返回所选路径给原表单 |
| `folders.dismiss` | 点关闭、取消或按 Escape → 返回原表单 | `desktop/src/home-folders.js:95`、`desktop/src/home-folders.js:98` | `dialog`、`finish(null)` | 无 | 手动 · 隔离桌面 · 点关闭、取消或按 Escape，核对返回原表单 |

### 应用、源码与工具更新

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `updates.check` | 点检查 Crew 应用更新 → 展示版本可用性或失败 | `desktop/src/home-self-update.js:64` | `#crew-self-check` | `desktop/test/crew-self-update.test.cjs::a newer release downloads through GitHub redirects, verifies SHA-256 and waits for idle work before restarting` | 手动 · 隔离桌面 · 点检查 Crew 应用更新，核对展示版本可用性或失败 |
| `updates.download` | 有版本时点下载 → 显示下载状态 | `desktop/src/home-self-update.js:69` | `#crew-self-action`、`selfUpdateDownload` | `desktop/test/crew-self-update.test.cjs::a newer release downloads through GitHub redirects, verifies SHA-256 and waits for idle work before restarting` | 手动 · 隔离桌面 · 有版本时点下载，核对显示下载状态 |
| `updates.cancel` | 下载时点取消 → 停止下载并更新状态 | `desktop/src/home-self-update.js:68` | `selfUpdateCancel` | 无 | 手动 · 隔离桌面 · 下载时点取消，核对停止下载并更新状态 |
| `updates.install` | 就绪时点安装 → 进入安装应用更新流程 | `desktop/src/home-self-update.js:70` | `selfUpdateInstall` | `desktop/test/crew-self-update.test.cjs::a newer release downloads through GitHub redirects, verifies SHA-256 and waits for idle work before restarting` | 手动 · 隔离桌面 · 就绪时点安装，核对进入安装应用更新流程 |
| `updates.release` | 点发行说明 → 打开当前版本发布链接 | `desktop/src/home-self-update.js:72` | `#crew-self-release` | 无 | 手动 · 隔离桌面 · 点发行说明，核对打开当前版本发布链接 |
| `updates.auto` | 切换自动下载 → 保存自动下载偏好 | `desktop/src/home-self-update.js:75` | `#crew-self-auto`、`auto_download` | 无 | 手动 · 隔离桌面 · 切换自动下载，核对保存自动下载偏好 |
| `updates.source-check` | 点检查源码更新 → 显示源码版本动态或错误 | `desktop/src/home-crew-updates.js:55` | `#crew-update-check` | `desktop/test/crew-updates.test.cjs::online main and release are separate from the local target and a newer local build is labelled ahead` | 手动 · 隔离桌面 · 点检查源码更新，核对显示源码版本动态或错误 |
| `updates.source-online` | 点查看远端版本 → 打开对应分支网页 | `desktop/src/home-crew-updates.js:57` | `#crew-main-open` | 无 | 手动 · 隔离桌面 · 点查看远端版本，核对打开对应分支网页 |
| `updates.source-apply` | 点应用源码更新 → 启动更新或排队并显示进展 | `desktop/src/home-crew-updates.js:60` | `#crew-update-apply` | `desktop/test/crew-updates.test.cjs::owned update backs up before changing installation, keeps the port, verifies and reconnects` | 手动 · 隔离桌面 · 点应用源码更新，核对启动更新或排队并显示进展 |
| `updates.source-cancel` | 点取消等待 → 取消源码更新操作 | `desktop/src/home-crew-updates.js:61` | `#crew-update-cancel` | `desktop/test/crew-updates.test.cjs::busy updates queue without backup or stop, release the lock, and allow cancellation` | 手动 · 隔离桌面 · 点取消等待，核对取消源码更新操作 |
| `updates.tools-nav` | 点执行工具管理 → 查看工具、版本与安装状态 | `desktop/src/home-tools.js:106` | `#nav-tools` | `desktop/test/home-tools-platform.test.cjs::Mac tools page offers tool updates, the auto-update preference and the Crew self-update` | 手动 · 隔离桌面 · 点执行工具管理，核对查看工具、版本与安装状态 |
| `updates.tools-check` | 点检查工具 → 重新读取各工具更新状态 | `desktop/src/home-tools.js:107` | `#tools-check` | 无 | 手动 · 隔离桌面 · 点检查工具，核对重新读取各工具更新状态 |
| `updates.tools-apply` | 点更新工具 → 根据可用状态立即更新或进入排队 | `desktop/src/home-tools.js:78` | `data-tool-update` | `desktop/test/home-tools-platform.test.cjs::Mac tools page offers tool updates, the auto-update preference and the Crew self-update` | 手动 · 隔离桌面 · 点更新工具，核对根据可用状态立即更新或进入排队 |
| `updates.tools-cancel` | 点取消等待 → 清除指定工具排队任务 | `desktop/src/home-tools.js:81` | `data-tool-cancel` | 无 | 手动 · 隔离桌面 · 点取消等待，核对清除指定工具排队任务 |
| `updates.tools-auto` | 切换工具自动更新 → 保存并显示开关结果 | `desktop/src/home-tools.js:108` | `#tools-auto` | `desktop/test/home-tools-platform.test.cjs::Mac tools page offers tool updates, the auto-update preference and the Crew self-update` | 手动 · 隔离桌面 · 切换工具自动更新，核对保存并显示开关结果 |

### 桌面系统管理员

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `health.open` | 打开系统管理员 → 查看后台健康及检查项 | `desktop/src/home-health.js:101` | `#admin-check`、`CrewHomeHealth` | `desktop/test/home-health.test.cjs::administrator reads one global snapshot and never restarts a live backend` | 手动 · 隔离桌面 · 打开系统管理员，核对查看后台健康及检查项 |
| `health.check` | 点立即检查 → 刷新健康检查和建议 | `desktop/src/home-health.js:102` | `#admin-check` | `desktop/test/home-health.test.cjs::checks are serialized and failures remain retryable without displaying a false healthy state` | 手动 · 隔离桌面 · 点立即检查，核对刷新健康检查和建议 |
| `health.conversation` | 点管理员对话 → 进入后台与运维设置 | `desktop/src/home-health.js:103` | `#admin-conversation` | `desktop/test/home-health.test.cjs::administrator exposes the maintenance conversation and configuration entry` | 手动 · 隔离桌面 · 点管理员对话，核对进入后台与运维设置 |
| `health.restart` | 点重启后台 → 允许时提交重启并反馈结果 | `desktop/src/home-health.js:104` | `#admin-restart`、`snapshot.restart.allowed` | `desktop/test/home-health.test.cjs::safe recovery needs an explicit click and refreshes actual state after the receipt` | 手动 · 隔离桌面 · 点重启后台，核对允许时提交重启并反馈结果 |
| `health.visibility` | 隐藏与切回健康页 → 停止或恢复健康检查刷新 | `desktop/src/home-health.js:105` | `visibilitychange`、`syncVisibility` | `desktop/test/home-health.test.cjs::polling stops when the panel, native home view, or document is hidden and resumes on return` | 手动 · 隔离桌面 · 隐藏与切回健康页，核对停止或恢复健康检查刷新 |

### 手机、窄屏与无障碍显示

| 标识 | 交互 | 位置 | 依赖 | 已有自动化覆盖 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| `mobile.bottom-shell` | 进入 ≤760px 显示手机底栏 → 在一般页面可操作，进入聊天后按状态隐藏 | `frontend/mobile-shell.js:174`、`frontend/mobile.css:34`、`frontend/index.html:64` | `#m-tabbar`、`body.m-chat` | 无 | 截图走查 · 进入 ≤760px 显示手机底栏，核对在一般页面可操作，进入聊天后按状态隐藏 |
| `mobile.home-tab` | 触摸工作台 → 回到对应工作台入口 | `frontend/mobile-shell.js:46` | `data-m-tab=home` | 无 | 截图走查 · 触摸工作台，核对回到对应工作台入口 |
| `mobile.leader-tab` | 触摸 Leader → 打开列表，已展开时保持列表 | `frontend/mobile-shell.js:47`、`frontend/mobile-shell.js:142` | `data-m-open-leaders`、`sessionStorage` | 无 | 截图走查 · 触摸 Leader，核对打开列表，已展开时保持列表 |
| `mobile.notes-tab` | 触摸想法 → 打开想法墙 | `frontend/mobile-shell.js:48` | `data-m-tab=notes` | 无 | 截图走查 · 触摸想法，核对打开想法墙 |
| `mobile.assistant-tab` | 触摸助手 → 打开全局助手 | `frontend/mobile-shell.js:49` | `data-m-tab=assistant` | 无 | 截图走查 · 触摸助手，核对打开全局助手 |
| `mobile.more-open` | 触摸更多 → 打开底部选单，焦点移入 | `frontend/mobile-shell.js:140`、`frontend/mobile-shell.js:82` | `#m-sheet`、`aria-expanded` | 无 | 截图走查 · 触摸更多，核对打开底部选单，焦点移入 |
| `mobile.more-close` | 触摸遮罩、菜单项或按 Escape → 关闭选单并恢复焦点 | `frontend/mobile-shell.js:149`、`frontend/mobile-shell.js:155`、`frontend/mobile-shell.js:156` | `#m-sheet-backdrop` | 无 | 截图走查 · 触摸遮罩、菜单项或按 Escape，核对关闭选单并恢复焦点 |
| `mobile.more-channel` | 在更多选交流区 → 切至交流发言 | `frontend/mobile-shell.js:63` | `.m-sheet-item`、`data-view=channel` | 无 | 截图走查 · 在更多选交流区，核对切至交流发言 |
| `mobile.more-todos` | 在更多选待办 → 切至待办页 | `frontend/mobile-shell.js:64` | `.m-sheet-item`、`data-view=todos` | 无 | 截图走查 · 在更多选待办，核对切至待办页 |
| `mobile.more-architecture` | 在更多选架构图 → 打开架构画布 | `frontend/mobile-shell.js:65` | `.m-sheet-item`、`data-view=architecture` | 无 | 截图走查 · 在更多选架构图，核对打开架构画布 |
| `mobile.more-git` | 在更多选 Git 助手 → 打开 Git 页 | `frontend/mobile-shell.js:66` | `.m-sheet-item`、`data-view=git` | 无 | 截图走查 · 在更多选 Git 助手，核对打开 Git 页 |
| `mobile.more-usage` | 在更多选用量与额度 → 打开用量页 | `frontend/mobile-shell.js:67` | `.m-sheet-item`、`data-view=usage` | 无 | 截图走查 · 在更多选用量与额度，核对打开用量页 |
| `mobile.more-switch` | 在更多选切换工作区 → 打开工作区总览 | `frontend/mobile-shell.js:68` | `.m-sheet-item`、`href=/` | 无 | 截图走查 · 在更多选切换工作区，核对打开工作区总览 |
| `mobile.more-theme` | 点手机选单日夜 → 主题和当前模式提示同步更新 | `frontend/mobile-shell.js:71`、`frontend/mobile-shell.js:115` | `data-m-theme` | 无 | 截图走查 · 点手机选单日夜，核对主题和当前模式提示同步更新 |
| `mobile.more-presence` | 点在场与消息提醒 → 显示状态浮层 | `frontend/mobile-shell.js:72`、`frontend/mobile.css:24` | `data-act=presence-toggle` | 无 | 截图走查 · 点在场与消息提醒，核对显示状态浮层 |
| `mobile.more-settings` | 点设置 → 有控制权限才显示和进入设置 | `frontend/mobile-shell.js:73`、`frontend/mobile.css:58` | `data-local-control` | 无 | 截图走查 · 点设置，核对有控制权限才显示和进入设置 |
| `mobile.badges` | 工作台待办、Leader 未读或 Git 与交流区有更新 → 底栏和更多选单产生对应数字或点 | `frontend/mobile-shell.js:105` | `.m-badge`、`.m-dot`、`data-m-count` | 无 | 截图走查 · 工作台待办、Leader 未读或 Git 与交流区有更新，核对底栏和更多选单产生对应数字或点 |
| `mobile.active-tab` | 切页或开关 Leader 面板 → 底栏当前项跟随路由与面板 | `frontend/mobile-shell.js:95` | `aria-current`、`m-leaders` | 无 | 截图走查 · 切页或开关 Leader 面板，核对底栏当前项跟随路由与面板 |
| `mobile.chat-back` | 点窄屏聊天返回 → 回到 Leader 列表 | `frontend/mobile-chat.css:21`、`frontend/app.js:744` | `.m-back` | 无 | 截图走查 · 点窄屏聊天返回，核对回到 Leader 列表 |
| `mobile.chat-toolbar` | 打开手机对话 → 任务、待处理、菜单有触控区，压缩环在适用头部可点 | `frontend/mobile-chat.css:25`、`frontend/mobile-chat.css:52`、`frontend/mobile-chat.css:259` | `#chat-head .right`、`.context-ring` | 无 | 截图走查 · 打开手机对话，核对任务、待处理、菜单有触控区，压缩环在适用头部可点 |
| `mobile.chat-scope-scroll` | 横滑成员范围条 → 露出其他任务选项 | `frontend/mobile-chat.css:61` | `.member-scope-switch` | 无 | 截图走查 · 横滑成员范围条，核对露出其他任务选项 |
| `mobile.chat-composer` | 手机写消息 → 可用附件、标签、停止和发送触控区，粗指针 Enter 换行 | `frontend/mobile-chat.css:121-137`、`frontend/browser-utils.js:36` | `.composer-wrap`、`pointer:coarse` | 无 | 截图走查 · 手机写消息，核对可用附件、标签、停止和发送触控区，粗指针 Enter 换行 |
| `mobile.task-overlay` | 手机打开任务栏 → 独立覆盖面板可触控关闭或返回 | `frontend/mobile-chat.css:140`、`frontend/mobile-chat.css:149` | `#task-list`、`.task-list-close` | 无 | 截图走查 · 手机打开任务栏，核对独立覆盖面板可触控关闭或返回 |
| `mobile.task-review` | 手机进入验收详情 → 通过、观察、需修改和报告入口可触达 | `frontend/mobile-chat.css:177-183` | `.task-review-actions` | 无 | 截图走查 · 手机进入验收详情，核对通过、观察、需修改和报告入口可触达 |
| `mobile.responsive-leaders` | 在窄屏打开 Leader 列表并选会话 → 列表覆盖内容且选择后收起 | `frontend/style.css:1022`、`frontend/app.js:1503` | `#leader-panel`、`narrowLayout` | 无 | 截图走查 · 在窄屏打开 Leader 列表并选会话，核对列表覆盖内容且选择后收起 |
| `mobile.responsive-workbench` | 缩到窄屏并打开工作台 → 首页操作换行且任务、验收卡仍可使用 | `frontend/workspace-home.css:75`、`frontend/mobile-home.css:39-80` | `.wh-columns` | 无 | 截图走查 · 375px 与 760 滚动全页，核对新对话、验收和任务卡 |
| `mobile.responsive-settings` | 窄屏打开设置 → 分类与表单重排且保存、取消可用 | `frontend/settings-ui.css:156`、`frontend/mobile-pages.css:286` | `.cs-sidebar`、`.cs-footer` | 无 | 截图走查 · 375px 与 760 切分类并操作底部按钮 |
| `mobile.responsive-assistant` | 将助手页面缩至 940px 以下 → 隐藏概览列，聊天与头部动作仍可用 | `frontend/assistant.css:21` | `.assistant-context`、`.assistant-main` | 无 | 截图走查 · 940、760、375 检查助手头部和输入 |
| `mobile.sidecar-overlap` | 在较窄对话打开待关注或会话结构 → 浮层覆盖而不压缩聊天至不可读宽度 | `frontend/app.js:665` | `data-side-overlap`、`--chat-side-space` | 无 | 截图走查 · 760px、375 同时打开两分区并各自关闭 |
| `mobile.themes` | 在浅色、深色和跟随系统下打开窄屏 → 保留可见入口和对应主题状态 | `frontend/style.css:1079` | `data-theme`、`prefers-color-scheme` | 无 | 截图走查 · 375px 与 760 逐个主题打开对话、工作台和设置 |
| `mobile.git-panes` | 在手机 Git 页点仓库或对话 → 两区互斥展示，切换时仍保留同一输入 | `frontend/mobile-pages.css:136-145`、`frontend/git.js:125` | `#git-m-pane-repo`、`#git-m-pane-chat` | 无 | 截图走查 · 375px 写草稿后切仓库，再回对话检查 |
| `mobile.git-settings-full` | 手机打开 Git 设置 → 全屏显示可滚动设置及底部动作 | `frontend/mobile-pages.css:166` | `.git-settings-dialog` | 无 | 截图走查 · 375px 逐项选择并关闭 |
| `mobile.session-sheet` | 在手机打开会话菜单 → 从底部出现，子菜单在原处展开 | `frontend/mobile-pages.css:270-284` | `#menu`、`.sub-menu` | 无 | 截图走查 · 375px 点击聊天省略号，进入并退出两层菜单 |
| `mobile.notes-filter-scroll` | 在手机想法墙横滑筛选 → 同一行露出其他选择，至少两个标签才显示任一或全部选择 | `frontend/mobile-pages.css:33-49` | `.nw-filters`、`.nw-seg` | 无 | 截图走查 · 375px 选零、一、两标签并横滑 |
| `mobile.notes-bulk-bar` | 在手机勾选想法 → 批量栏显示在底栏上方，状态提示避让 | `frontend/mobile-pages.css:77-82` | `.nw-selecting`、`.nw-bulk` | 无 | 截图走查 · 375px 连续勾选三项并点批量标签 |
| `mobile.notes-menu-sheet` | 在手机点想法更多 → 弹出底部操作菜单与对话框 | `frontend/mobile-pages.css:84-91` | `.nw-menu`、`.nw-dialog` | 无 | 截图走查 · 375px 执行编辑并退回 |
| `mobile.notes-capture-sticky` | 在手机滚动想法墙 → 快速记录区保持可输入 | `frontend/mobile-pages.css:21` | `.nw-capture` | 无 | 截图走查 · 375px 从首页滚到后面的卡再记录 |
| `mobile.channel-target-scroll` | 在手机交流区横滑点名行 → 露出更多 Leader 并继续多选 | `frontend/mobile-home.css:225` | `.channel-targets` | 无 | 截图走查 · 375px 准备十位 Leader 后滑到末项 |
| `mobile.presence-sheet` | 手机点击在场与消息 → 状态从底部面板展示 | `frontend/mobile.css:29` | `.presence-popover` | 无 | 截图走查 · 375px 打开查看状态，再进连接设置 |
| `mobile.task-back-status` | 手机查看任务执行对话 → 返回总会话按钮可见，执行状态徽记收起 | `frontend/mobile-chat.css:252-254` | `.exec-chat-bar`、`.exec-chat-back` | 无 | 截图走查 · 375px 用执行卡进入后返回 |
| `mobile.pin-touch` | 无悬停设备查看 Leader 行 → 图钉仍可触达 | `frontend/style.css:974` | `.leader-pin`、`hover:none` | 无 | 截图走查 · 375px 触控分别置顶与撤销置顶 |
| `mobile.responsive-attention` | 在 620px 以下打开待关注 → 图标及数字保留，卡片操作重排 | `frontend/style.css:295` | `.attention-trigger`、`.attention-count` | 无 | 截图走查 · 620px 和 375 打开面板并点待办操作 |
| `mobile.responsive-architecture` | 缩窄架构画布 → 工具栏、归属区与缩放控件按触屏尺寸调整 | `frontend/architecture.css:151-176` | `.arch-shelf`、`.arch-zoom` | 无 | 截图走查 · 760px 与 375 切方向、缩放和折叠 |
| `mobile.reduced-motion` | 系统启用减弱动态后操作 → 圆环或面板跳过相应动画，桌面切页直接完成 | `frontend/style.css:245`、`frontend/style.css:1023`、`desktop/src/tabs.cjs:243` | `prefers-reduced-motion` | `desktop/test/tabs.test.cjs::the system reduced-motion setting switches pages directly` | 截图走查 · 1280px 启用系统减弱动态，切页并触发圆环忙态 |

## 未确认的交接描述

- **任务执行头部的独立压缩环。** `frontend/app.js:699` 与 `frontend/app.js:703` 的执行工具条未产生压缩按钮，`frontend/app.js:744` 只在 `record === null` 时渲染头部圆环，`frontend/app.js:1205` 的请求体只有 `session_id` 和 `after_turn`。执行实例级入口及其单独请求未确认。
- **架构图节点评论。** `frontend/architecture.js:733` 的节点菜单和 `frontend/architecture.js:761` 的命令分支未找到评论入口。当前代码可确认的节点操作已列在架构图节。
- **手机长按会话菜单。** `frontend/mobile-pages.css:270` 注释提到长按，`frontend/app.js:1478` 绑定浏览器 `contextmenu`，`frontend/app.js:1715` 支持菜单键及 Shift+F10。独立长按手势和各手机浏览器是否把长按转成该事件未确认。
- **正文绿色未读角标。** `frontend/chat.js:697` 渲染正文未读分隔线，`frontend/app.js:417` 的交流区入口渲染未读数字。`frontend/style.css:152` 使用当前主题强调色，正文内独立绿色数字角标未确认。
- **原生报告预览执行窗。** `desktop/src/report-preview.cjs:20` 将工作器设为隐藏，实际用户可见的报告阅读器和桌面活动预览分别按 `task.delivery` 与 `desktop.tab-preview` 验收。

## 汇总

| 区域 | 条数 | 无自动化覆盖 |
| --- | ---: | ---: |
| 工作台、工作区动态与状态刷新 | 22 | 13 |
| 常驻左栏与工作区切换 | 20 | 16 |
| Leader 侧栏、团队与会话治理 | 59 | 31 |
| 对话、状态和会话结构 | 41 | 17 |
| 输入框和技能 | 23 | 17 |
| 任务执行、交付与验收 | 42 | 14 |
| 边看边聊与任务文档 | 18 | 16 |
| 待关注、人工待办与拍板 | 24 | 15 |
| 原生执行权限审批 | 5 | 0 |
| Leader 交流区 | 18 | 10 |
| 架构图 | 51 | 33 |
| Git 与 Git 助手 | 26 | 18 |
| 会话库 | 13 | 7 |
| 想法墙 | 68 | 38 |
| 全局助手与随手记 | 16 | 11 |
| 用量与额度 | 9 | 6 |
| 设置、默认大脑与权限 | 14 | 7 |
| 外观、字号与配色 | 7 | 7 |
| 切换执行工具 | 12 | 3 |
| 组员默认大脑 | 11 | 9 |
| 行为配置与使用记录 | 8 | 2 |
| 提示音 | 15 | 11 |
| 消息连接与通知 | 14 | 7 |
| 后台与运维 | 15 | 11 |
| 启动、连接与目录 | 17 | 11 |
| 工作区设置 | 9 | 1 |
| 部门压缩与工作区清理 | 13 | 11 |
| 存储与回收站 | 14 | 4 |
| 历史、导入导出与交接 | 20 | 11 |
| 局域网访问与设备管理 | 9 | 1 |
| 连接鉴权与浏览器配对 | 5 | 3 |
| 桌面窗口、标签、预览与托盘 | 54 | 39 |
| 桌面工作区首页 | 31 | 19 |
| 主机目录选择器 | 11 | 11 |
| 应用、源码与工具更新 | 15 | 6 |
| 桌面系统管理员 | 5 | 0 |
| 手机、窄屏与无障碍显示 | 44 | 43 |
| **合计** | **798** | **479** |

### 拆分中最容易丢失的 15 条

| 标识 | 理由 |
| --- | --- |
| `leader.drag` | 置顶与普通分区共用排序写入，落点跨区时必须同时调整置顶集合和全量顺序。 |
| `leader.pin-reveal` | 图钉平时接近隐藏，悬停、键盘聚焦和无悬停设备分别依靠不同样式路径。 |
| `chat.context-ring.phases` | 圆环跨侧栏、标题和 Git 助手使用，刷新时仍需保留请求阶段、目标身份及键盘焦点。 |
| `chat.stop-person-confirm` | 停止一人的流程常与停止当前轮混在一起，受影响任务清单和取消路径容易消失。 |
| `chat.preserve-anchor` | 补历史和流式渲染都改动 DOM，自动贴底会把正在阅读的用户带离上下文。 |
| `composer.draft-text` | 会话与目标共用编辑区，草稿键或路由的细小变化会导致串稿。 |
| `workbench.ime-refresh` | 轮询和输入法组合同时发生时，保存文本及选区仍可能替换掉正在组词的原节点。 |
| `composer.numbered-list` | 三类编辑入口中的列表能力依赖 beforeinput，并与空项结束、代码围栏及撤销交互。 |
| `composer.skill-keys` | 技能选择优先于发送键，重新绑定快捷键时可能把选中技能变成提交消息。 |
| `task.leader-exec` | 任务详情和执行对话独立切换，按负责人聚合记录容易混入其总对话。 |
| `task.reject-submit` | 写入时需要携带用户理由、任务版本和当前报告引用，重试还需保持同一任务。 |
| `reader.quote-frame` | 报告嵌入文档的选区通过消息桥交给宿主，拆分时容易保留双栏却丢掉引用入口。 |
| `channel.private-detail` | 点击私聊详情会按发言时刻或用户原文前缀定位，普通跳页只能把用户送到会话底部。 |
| `remote.lifecycle-error` | 切换网络由停止、启动和读取结果组成，最后一步失败时容易重复上一条已经成功的写入。 |
| `desktop.workspace-reuse` | 复用原生视图才能保留草稿和历史位置，同名标签的状态重建可能重新加载视图。 |

