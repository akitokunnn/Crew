# 已交付任务内的解释与小改

记录日期：2026-10-30。分支 `rebuild/v2`；验证对象为本轮未提交源码。本记录不能代替最终提交、构建或正式实例核对。

## 用户可见行为

用户在已交付、待验收或验收未通过的任务内继续发消息，先进入任务内讨论。对话可显示“回复中”；任务卡保留原业务状态，原需求和人工验收不变。普通解释直接回答，不创建目标、工作清单或报告，不自动增加轮次。

AI 确认用户确实提出修改后，用已有 `amend` 动作登记具体修改，才进入同一任务的迭代。原工作清单继承已有状态和证据；只重开受影响项或补充新项，不把所有旧项一律标成待复核。修改完成仍须正常执行工作清单和最终交付检查。旧报告、旧验收和旧执行历史保留。

本路径不根据“第二轮”“报错”等词判断用户意图。新轮次仍由明确的用户重开动作创建；普通消息及 `amend` 不增加 `current_round`。手动暂停不因讨论或修改动作被绕过。

## 数据与接口

- `goal_discussion_executions`：`execution_id`、原输入 `message_id`、`created_at`、`promoted_at`、`promotion_json`。未提升的记录表示讨论执行；提升回执只用于同一次修改重试去重。
- `goal_continuations`：保存进入同任务迭代前的需求版本、报告引用、业务状态、真实 actor、修改原因，以及验收、finish、round 快照。它是历史记录，不是执行授权。
- `goals.get_execution()` 及 `/api/goal` 的 `executions[]` 增加 `response_mode: "discussion" | "work"`。`/api/chat/history` 同样返回该字段。前端按当前 execution ID 更新，避免同一次执行从讨论转工作后仍显示“回复中”。
- 普通输入仍为 `POST /api/chat/send?project=...`，提供 `session_id`、`goal_id`、`text`、稳定 `message_id`。消息重试复用原回执；不会重复创建执行、需求版本或轮次。
- 业务工具默认沿用 `goals.dispatch_check()` 的写门禁。讨论仅允许读操作和当前目标的 `amend`；跨目标修改、派工、清单写入、交付等必须先登记修改。
- 原生回复准入使用 `allow_discussion=True`；它不改变业务写入口的默认规则。

AI 的修改动作示例：

```json
{
  "actor_session_id": "当前 leader 的稳定 ID",
  "execution_id": "当前讨论 execution ID",
  "action": "amend",
  "data": {
    "goal_id": "当前任务 ID",
    "text": "用户要求的具体修改",
    "expected_version": 5
  }
}
```

HTTP 使用 `POST /api/goals/agent?project=...`；Codex 使用 `crew_goal` 的 `action: "amend"` 和相同 `data`，actor/execution 由宿主绑定。CC 使用 `crew goal amend <goal_id> --data <JSON>`；载体内的执行身份由已有环境绑定。不能将 AI actor 改成 `user` 来绕过边界。

`amend` 接受后，同一 execution 改为 `work`，需求版本增加一次、当前轮次保持不变。相同修改参数和版本的工具重试返回原结果。后续继续使用 `work.list`、`work.add`、`work.update` 和 `finish`，无需新建目标。

## 恢复与迁移

新增表已纳入 schema 的明确外键归属。工作区恢复保留历史并暂停未结束执行；普通读取不会唤醒模型。

可移植包保留讨论/工作模式和历史快照，重新映射执行、目标和报告引用，不重放旧输入 ID 或提升回执。补齐了原来没有导出的人工验收记录。导入后已结束任务保留已结束状态，可在用户新输入时继续讨论；未结束任务仍需明确恢复。

## 验证记录

所有自动验证使用隔离 HOME、临时目录和假载体，未操作正式 Crew。执行前仅在测试进程清空继承的 `CREW_SESSION_ID`、`CLAUDE_CODE_SESSION_ID`，并设置 `PYTHONPATH=tests`。

在 `plugin` 目录执行：

```powershell
python -m unittest test_goal_http test_goal_actions test_goal_acceptance test_goal_work test_storage_v2_features test_workspace_recovery test_v2_journeys test_goals test_goal_context test_codex_tools -q
```

196 项通过。覆盖 CC/Codex 的普通解释、明确小改、同任务同轮交付、旧验收保留、工作清单继承、工具重试、跨目标拒绝、暂停保护，以及真实 SQLite 工作区恢复和 `.crewpack` 导出、导入、激活。日志：`Temp/goal-discussion-tests.log`。

交叉检查命令：

```powershell
python -m unittest test_storage_v2_features test_storage_v2_activation test_storage_v2_packages test_goal_recovery test_goal_messages test_goal_links test_goal_modes_reports test_goal_notifications test_web_contract -q
```

151 项中 150 项首次通过；唯一失败是旧合同仍期待新任务继承隐藏的 leader 信任。按本轮需求改为任务自己的默认来源 `task-default` 后，`python -m unittest test_web_contract -q` 全部 47 项通过。日志：`Temp/goal-discussion-cross-tests.log`，其中保留首次失败原文，不抹掉排查记录。

反证：在隔离测试进程将 `ensure_discussion` 临时替换为旧的“任何消息先 `continue_task`”路径，纯解释测试确实失败：`completed / requirement 1` 被改成 `queued / requirement 2`。恢复正确路径后通过。记录：`Temp/goal-discussion-mutation.log`。

浏览器使用真实 HTTP/SQLite 和假载体验证：已验收任务追问显示“回复中”，业务状态与验收不变；明确登记修改后，同一 execution 显示工作状态，轮次不增加。UI 分工最终 28 项通过；截图和旧界面反证见 `UI_REVIEW_2026-10-30.md`。

## 真实模型及权限边界

最终真实 Codex 验证由原生载体分工使用 `gpt-5.6-luna / low` 在单独隔离 HOME 完成。夹具为 `Temp/v2-feedback-2026-10-30/discussion_fixture.py`，仅生成一个 `Hello` 页面及已验收任务。纯解释后，原 goal 与人工验收完全相同，文件未修改。第二条真实小改实际经过 `amend → work.update → 读取 → apply_patch → 复读 → work.done → finish`，同一任务、同一轮次、同一 execution，需求版本 1→2，页面变为 `Hi`，新增报告且旧验收仍可查阅。

首个验证脚本在原生 idle 后过早关闭宿主，尚未等待后台正常收尾，因任务暂时仍为 running 而断言失败；原记录保留。随后在相同隔离 HOME 重开宿主，将原生启动配置为必定报错，正常后台约 2 秒内将已发布结果收尾为 completed/finalized，实际 AI 启动次数为 0，没有重跑模型覆盖失败。证据为 `Temp/v2-feedback-2026-10-30/discussion-real-http.json`、`discussion-real-settled.json`；详情见 `NATIVE_FEEDBACK_SELF_CHECK_2026-10-30.md`。临时认证已清理。

收尾回归还修正了一条旧 hook 测试：普通 CC prompt 不再自动注入 leader 的历史 trust 开关或目标，历史配置仍可读取；不排通知、不创建执行。`python -m unittest test_trust_mode -v` 25 项通过，任务模式的配置、双载体拍板绑定及独立上下文更新 3 项通过。仅在测试进程加载旧 `a31ca95` hook 后，开启和关闭两个分支均按预期失败，夹具错误为 0；记录为 `Temp/v2-feedback-2026-10-30/trust-hook-old-counterproof.log`。生产代码未因旧测试而恢复历史注入。

原生 guard 与 Crew 业务动作共同限制讨论中的显式文件写入和业务修改。现有 `danger-full-access` 环境下，不能宣称能从任意 Bash 命令中绝对识别全部副作用；本次没有增加靠命令关键词猜授权的解析器。真实 Codex 桌面 UI 的交出、接回仍须单独验证。
