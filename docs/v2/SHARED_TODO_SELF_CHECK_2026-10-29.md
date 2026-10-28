# 人工 TODO 去重与多任务接续自检

本次基于 `rebuild/v2` 当前代码的 `todo`、`goal_links` 实施。查阅过归档 `rebuild/test` 的 `personal.create_todo/add_native_todo/todo_changed`，沿用其“明确实际操作标识、不按标题相似度合并”的规则；没有搬入归档的另一套任务调度器。

## 当前接口

- `todo.add_native` 是 CC CLI 和 Codex 动态工具的共同登记入口。由宿主绑定实际执行，不能通过参数冒充其他执行。
- CLI 增加 `--operation-key`，Codex `crew_todo_add` 增加 `operation_key`。相同工作区内，同一个明确标识复用原事项并关联当前任务；保留原步骤和处理状态。新事项即使未提供标识，也返回可在后续调用中复用的 `operation_key`。
- 未提供标识时，只有同提出者、所有字段完全相同、仍开放的记录视为重试。不同文本或提出者不推断等价。已完成显式事项重试不会重新打开。
- `todo.get/list` 返回 `goal_ids` 和 `goal_links`（含 `goal_title`）。首页可以显示一条 TODO 及多个任务链接。
- `todo:add` 事件只在首次创建时生成一次。外部新增提醒可用 `todo:{id}:created` 作为稳定去重标识。重复登记和关联不产生第二条新增事件。

## 投递与数据

- `goal_links` 从单项唯一改成支持多个实际执行的关联，拍板仍保留单项唯一约束。迁移保留每条旧记录与接收版本，并在事务内重查，支持并发打开同一 HOME。
- 每个关联任务独立记录 `delivered_version`，已结束的关联不能消费其他任务的通知。同任务同轮次重试不会重复增加关联。
- 待办完成只投递一次给各有效任务。目标暂停、会话停止、leader 停止均保留待处理通知，不清除停止状态；用户明确恢复后才继续。旧轮次、旧需求和已结束目标仅留档。
- 目标仍有其他开放的阻塞待办或拍板时，保持 `waiting_user`。用户撤销状态继续复用原有版本审计与通知规则。
- 关联以工作区为边界。相同标识出现在不同工作区时是两项记录，不自动跨工作区合并。

## 实际验证

所有测试清除继承的 `CREW_SESSION_ID`、`CLAUDE_CODE_SESSION_ID`，只使用临时 HOME、SQLite、CLI/动态工具和假原生发送函数，无真实 AI 调用。

- `test_shared_todo test_todo test_goal_links test_codex_tools test_goal_http test_mixed_messaging test_home_migration`：180 项运行，179 通过、1 跳过，33.65 秒。
- 后续补充工作区恢复和标识隔离验证：`test_shared_todo test_workspace_recovery test_workspace_settings`：58 项运行，57 通过、1 跳过，6.19 秒。
- 新增专项覆盖并发两任务登记、重复创建/关联、CC CLI 和 Codex 工具共用事项、已完成事项重试、逐任务回执、手动暂停/停止、其他阻塞项、旧表并发迁移、恢复时保留标识与多任务回执。
- 跳过项为现有测试对平台链接创建能力的条件跳过；未据此声称该平台能力通过。

UI 多链接展示和 QQ/Lark 新增提醒由主控整合；本报告不把它们列为本次已验收内容。未实现跨工作区事项合并，也没有修改正式 Crew 数据或唤醒真实会话。
