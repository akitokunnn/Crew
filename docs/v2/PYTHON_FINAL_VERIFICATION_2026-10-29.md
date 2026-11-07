# V2 Python 全量验证

实际工作树为 `C:\Users\admin\.codex\worktrees\crew-v2\Crew`，分支 `rebuild/v2`，基准提交 `a51a093`，桌面源码版本 `0.3.0`。验证对象是本轮未提交源码；不能将结果解释为已部署或已合并。正式 Crew 服务、数据库、AI 及 QQ/Lark 均未调用。

每次测试使用 PowerShell 子进程，清除 `CREW_SESSION_ID`、`CLAUDE_CODE_SESSION_ID`、`CREW_EXECUTION_ID`、`CREW_GOAL_ID`，在 `plugin` 目录设置 `PYTHONPATH=tests;.` 与 UTF-8 后执行 `python -X utf8 -m unittest discover -s tests -v`。各测试自己的临时 HOME 和假载体承担隔离。

## 第一轮：保留失败证据

开始于 2026-10-29 05:51:30 +09:00，运行 688.469 秒：**2217 项，13 failures、5 errors、7 skips**。此轮并未通过。完整日志为系统临时目录 `v2-verification/python-final.log`，启动现场保存在 `python-final-start.txt`。运行期间其他协作者仍在落盘新源码，已加载 Python 模块不会随文件更新，不能把后续修复当作本轮成功。

| 失败用例 | 实际原因与修复 | 新进程验证 |
| --- | --- | --- |
| `test_context_http.ContextHttpTests.test_offline_managed_session_resumes_then_sends_compact`；`test_context_runtime.ContextRuntimeTests.test_compact_offline_resumes_and_sends_command` | 行为配置新增 `--append-system-prompt` 后，测试仍假定 `--resume` 在命令末尾；改为核对参数对应会话 ID，保留原始输入及恢复行为断言。 | `test_context_http` 14、`test_context_runtime` 15 项通过。 |
| `test_git_management.GitHttpTests.test_offline_result_uses_existing_wake_entry` | 同样的命令尾序假设；继续保留一次唤醒、用户内容、无第二次投递断言。 | 独立复现后原失败用例通过。 |
| `test_models_http.ModelWakeTests.test_resume_uses_updated_options_and_clears_previous_applied`、`test_wake_adopted_member_creates_spec_log_and_resumes_with_identity` | 同样的命令尾序假设；仍验证模型选项、身份及旧应用状态清理。 | `test_models_http` 20 项通过。 |
| `test_shell.SpawnTests.test_spec_brief_command_and_event`；`test_shell.WebTests.test_chat_resume_after_service_restart_uses_same_id_and_refuses_external_process`、`test_member_web_send_and_resume_keep_current_group_role_and_native_identity`、`test_resume_drains_previous_reader_before_new_turn_status`；`test_wake_runtime.AutoResumeTests.test_interrupted_member_and_leader_resume_once_with_crew_message_and_current_model` | `--session-id` / `--resume` 命令尾序假设；改为精确定位参数值，没有取消身份、单次恢复或消息保留检查。 | `test_shell test_shared_todo` 72 项通过，22.56 秒；`test_wake_runtime` 13 项通过。 |
| `test_git_context.GitContextTests.test_owned_codex_input_retries_unaccepted_context_and_does_not_change_the_display_text`；`test_runtime_integration.ManagedRuntimeTests.test_codex_creation_and_controls_use_codex_driver`、`test_peer_delivery_is_queued_while_busy_then_delivered_once`；`test_runtime_integration.RuntimeFeatureHttpTests.test_implicit_leader_and_member_use_codex_when_claude_is_absent`、`test_leader_without_explicit_options_uses_saved_default_brain` | 测试假 Codex 载体缺少真实载体已有的 `thread_id` / 初始行为配置 hash，记录原生接收证据时抛异常，HTTP 路径体现为 500。补全假载体契约，不在生产代码吞掉缺失字段。 | `test_git_context` 4、`test_runtime_integration` 22 项通过。 |
| `test_shared_todo.SharedTodoTests.test_workspace_recovery_keeps_shared_links_keys_and_each_receipt`；`test_workspace_recovery.WorkspaceRecoveryTests.test_reply_links_and_deduplication_survive_recovery` | 新增 `goal_decision_modes` 等领域表不在旧恢复器已知 schema 中，恢复实际被拒绝。补齐新领域表的恢复归属与引用映射。 | 前一用例及 `test_shared_todo` 完整验证通过；`test_workspace_recovery` 15 项通过，2.85 秒。 |
| `test_web_contract.WebContractTests.test_every_entry_page_asset_is_served_by_the_real_http_handler`，`/goal-versions-ui.js` | 全量已加载的静态路由缺少运行中新增资源，实测 HTTP 404；交主控对最终入口复验。 | 主控新进程 `test_web_contract` 47 项通过，9.16 秒；日志为工作树 `Temp/v2-verification/web-contract.log`。 |

第一轮共 18 个失败结果均逐项分类，没有删除失败用例，也没有改变生产失败结果为成功。

## 第二轮

在上述已知修复和 notes 恢复落盘后，于 2026-10-29 06:05:11 +09:00 以新进程重新执行完整发现，运行 **690.556 秒，2235 项，OK（7 skipped）**，退出码 0。即 2228 项通过，没有失败或错误。相比第一轮增加的用例来自协作者落盘的新功能回归。

7 项跳过包括 6 项需要 Windows 符号链接权限的检查，以及 1 项需显式开启的零模型原生 Codex 沙箱检查；不能把它们写成通过。

两轮日志和启动现场均已保存至工作树 `Temp/v2-verification/`：`python-final.log`、`python-final-start.txt`、`python-final-green.log`、`python-final-green-start.txt`，系统临时目录保留原始副本。第二轮没有覆盖第一轮失败日志。

第二轮启动后，主控要求补齐原生接续在 releasing/returning 中断后的恢复；该新增变更不在本轮已经导入的 Python 模块中，须由独立受影响模块验证补充，不能声称本轮包含运行中新增的代码。

该补充改变在 `native_handoff.py` 中恢复进程已不拥有的交接操作，并在 `chat.Manager` 初始化时调用；活跃进程仍拥有的操作保持原状态。协作者已用新进程验证原生交接 10、聊天 32、退出生命周期 12、行为配置 14、目标相关 205、Codex 运行 35 项：**共 308 项全部通过**。
