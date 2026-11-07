# V2 初版验收缺口盘点

2026-10-29，对照 `rebuild/test:docs/CREW_PRODUCT_REDESIGN_2026-10-28.md`、本轮用户纠正与 `PRODUCT.md`。本记录是实现补齐前的快照，不是最终交付结论。没有运行真实 AI 或访问正式数据。代码路径均相对仓库根目录。

| 能力 | 核实结果 | 当前代码与验收证据 | 最小补齐路径 |
| --- | --- | --- | --- |
| Crew 提示配置编辑、恢复、历史、作用域 | 首版尚缺 | `settings.py` 只存 AI 用途/执行权限；`codex_runtime.py` 初始化字符串、`goal_runtime.py:BASE_INSTRUCTIONS`、`channel_digest.py:PROMPT`、角色 SKILL 分散；当前树没有 `behavior_prompts.py`。`rules.py` 的文件读取hash仅是角色文件读取提醒，不能代表所有注入提示的版本与加载证明 | 整合旧分支 `behavior_prompts.py` 与测试；适配当前 general/goal/global assistant 身份；HTTP提供 resolve/get/save/reset/history/applied，设置页编辑；CC/Codex每次输入载入有效版本，保存不唤醒。原生接受回执与AI真正读过分开标识 |
| 原生 Skill/MCP/项目说明“实际加载” | 尚缺完整展示 | 当前 Codex initialize/runtime没有保存 `instructionSources`；原生模型发现不等于Skill/MCP加载证明。角色 SKILL 文本由宿主直接注入可保留精确hash | 先保存宿主实际发送文本与来源；只在原生返回真实来源时展示，其他列“未回报”。不要扫描磁盘配置便标已加载 |
| 快速想法：记下来 / 开始聊 | 后端部分完成，直接入口尚缺 | `personal.py:172` notes列表、`:257` note.capture/update保留原文和标签；`test_personal.py:207`验证原文和并发版本。`workspace-home.js`仅“新对话”；`assistant.js`展示TODO/任务/工作区，未展示notes或无模型保存入口 | 将note存储抽为用户/助手共用窄接口；首页一段输入两个动作，记下来只存原文，开始聊复用现有新leader入口；附可选工作区，未分配有效；notes可继续到现有任务 |
| 人工 TODO 基础处理 | 已实现 | `todo.py` add/resolve/withdraw/undo、版本冲突、取消原因；`goal_links.py:51`按原始execution投递，手动暂停不唤醒；`test_todo.py`、`test_goal_links.py` | 保留当前链路 |
| 人工 TODO 多任务合并、标签、完成附件 | 尚缺 | `todo.py:112`每次INSERT；`schema.sql:824` goal_links主键(kind,item_id)使一项只有一个goal；todo schema无附件/标签，resolve只有note；当前“一个清单”不等于多任务共享事项 | 增加显式复用已有todo_id的关联表，按事项身份去重，不靠模糊标题合并；完成版本向所有仍有效关联目标各投递一次；复用聊天附件入口支持说明图片 |
| AI 工作清单 | 尚缺独立可体验闭环 | `goals.assign`只描述成员任务，`goal_context`有事实/实验资料，未找到逐项计划的pending/doing/done、依赖及验收操作；展示整goal不等于项目自动拆成TODO逐步完成 | 在现有goal上增加work_items，允许leader增改/排序/完成并附证据，成员只改分配给自己的条目；同一个列表进入任务主页和AI上下文；旧分支 `task_scheduler.py/task_schema.sql`可借鉴状态和去重，不复活第二套任务树 |
| 每任务决策模式 | 粒度尚不匹配 | 当前 `trust_mode.py` 按leader身份配置；一个leader的多个goal共享设置，不满足原纲/PRODUCT的每任务开关 | goal保存模式并在goal上下文读取；general继续用原设置。切载体/换成员保持goal模式，暂停门禁独立 |
| QQ/Lark 基础收发、拍板、最终报告 | 已有真实程序链路和假平台测试 | `notifications.py` Inbox共用双渠道，`finalized_goal_reports`、`tick`、`process_replies`；`test_notifications.py`、`test_goal_notifications.py`、`test_connections.py`、`test_qq.py`、`test_lark.py` | 保留连接适配器；生产渠道未实际发送验收 |
| TODO新增、有意义故障通知、有限恢复 | 需补齐/整合旧实现 | `notifications.tick:309`读取attention，`:319`读reports；`presentation.attention`无TODO，`/todo`只是主动查询。`relay_turns:354`仍按离开期间leader每轮idle转发结尾，未限定行动意义；每次AI普通报告也入通知。当前无任务失败恢复次数账本 | 旧分支 `task_notifications.py`已有双渠道独立receipt、未知结果不重发、MAX_RETRIES=2，可适配当前goals/todos；仅完成/阶段可体验/失败达上限/待处理生成事件，移除重复普通状态推送；不要为了提醒再次调用模型 |
| 最终报告、验收、原任务反馈 | 已实现主要闭环 | `goals.save_report/review_acceptance/reopen`保存版本和旧轮次；`goal_actions.finish`、`goal_api`、报告HTML框架；`test_goal_acceptance.py`、`test_report_documents.py`、`test_goal_http.py`。原生handoff不同于成果反馈 | 保留现有报告/轮次，不另建报告中心 |
| 阶段报告与持续更新、用户编辑冲突保护 | 部分缺失 | `goal_actions`可用finish提交报告，当前把保存与收尾请求连接；未见独立阶段报告action/用户编辑版本冲突入口。`board.report`是文本动态，不是持续成果文档 | 新增独立report.save/stage action调用save_report但不request_finish；报告版本有明确stage标识，用户可编辑并使用expected_version；有新成果才通知 |
| 阶段版本恢复 | 尚无完整产品闭环 | 当前Git查看/版本报告不自动构成“恢复这个成果版本且保留其他任务改动”；旧分支有 `task_artifacts.py/task_artifact_actions.py/test_task_git_restore.py` | 可整合不可变成果快照+目标路径范围和证据；普通文件夹不git init。若本轮先不做，必须作为未完成项，不能归入用户明确延期 |

已完成的基础还包括：全Crew单助手跨工作区、顶部工作区标签、任务/讨论主页、稳定输入receipt与失败草稿、成员禁止再派工、goal/general独立执行、人工停止门禁、原生同ID接续后端和回收站基本机制；相关测试只能证明隔离假载体链路，不能声称真实模型主动识别任务、整夜运行或声纹效果达标。

用户明确允许延期的是 IDE 重点与没有可靠现成方案的 CC 原生接续；手机端不要求完整重做。跨项目助手、快速记录、AI工作清单、人工TODO合并、提示可编辑、任务反馈和有意义通知未被用户取消。Codex桌面真实往返仍是未验证项，不能用后端协议模拟替代。

建议补齐顺序：提示配置与真实载入证据 → AI工作清单与任务级模式 → 无模型快速记录/人工TODO关联 → 事件化通知与有限失败恢复 → 阶段报告和成果版本。每项以真实API + 界面操作 + 隔离载体调用验收，避免仅把功能写入prompt。

## 补齐记录（2026-10-29，源代码与隔离测试）

提示配置领域已接入：`behavior_prompts.py`、`web_api/behavior_prompts.py` 保存全局/工作区/会话覆盖、CAS版本、恢复继承和不可变历史；涵盖 common/leader/member/assistant/git/report/context/summary。CC 与 Codex 的 general/goal 启动及后续输入按变化发送有效文本，无变化不重复注入。保存不启动模型。全局助手可凭原始用户消息修改；失去用户消息凭据则拒绝。`test_behavior_prompts.py` 8项、`test_behavior_prompt_runtime.py` 6项通过。

载入记录明确区分 prepared、sent、confirmed。CC 写入 stdin 只记 sent，匹配原生 user UUID 回执才确认；Codex start/resume/turn 请求成功确认宿主内容已被原生接受。模型是否理解、原生私有系统提示或未回报的 Skills/MCP 没有被声称已验证。默认角色来自实际 SKILL 文件，覆盖后不再提醒读取旧内置文件。摘要保留固定输出协议，行为正文由可编辑模板替换。真实双载体模型与 Codex 桌面往返仍未执行。

Storage V2 的数据库 schema 已加到3，工作区标记格式仍为2；新扩展表 `workspace_feature_records` 使用工作区内的类型化 agent/goal/execution/todo/report/父记录/blob 外键。升级仅接受原有可信schema2元数据，先备份后事务升级；旧schema2 crewpack仍可验证并导入。新增数据的完整导出、fork到新身份、隔离HOME激活与再次刷新已在 `test_storage_v2_features.py` 验证：Prompt覆盖/历史/原载入状态、AI清单及历史、共享TODO每目标独立送达版本、每任务模式、阶段报告、项目想法及既有leader关系、成果版本的交付与基线真实字节。

原生ID/HOME/控制令牌、全局助手与未分配想法、全局Prompt、动作授权回执及本机文件恢复意图不作为工作区可执行状态迁移。HOME整备份保留它们；工作区包仅保留相关脱敏审计，导入会话保持停止，原生控制不重建连接，历史Prompt不标成本机已载入，成果字节只进入缓存不覆盖项目文件。QQ/Lark历史通知恢复为 archived，不重发原通知。

本表的“尚缺”是上方最初盘点时点。快速想法、共享TODO、AI清单、任务模式、阶段报告、通知与成果恢复由并行任务补齐，最终状态应以各功能代码、对应验收记录及交付说明为准。
