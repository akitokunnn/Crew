# Crew V2 任务行为与派工边界自检

日期：2026-10-29。范围：`rebuild/v2` 工作树中的领域接口、载体启动配置、CLI、HTTP 与角色守则。本文记录本轮验证结果，不代表正式服务、安装包或真实模型已验收。未提交或推送；最终集成由主控继续。

## 已落实的行为

1. **自然对话形成任务。** leader 守则要求结合完整对话区分讨论、实际交付与执行同意；明确执行请求即可建立目标并开始，不再要求额外点一次“建任务”。“我最近打算健身”只是话题，“简单给我一个打卡的健身 app 吧”构成任务请求。旧要求、引用、AI 消息和单个关键词不能代替新授权。程序没有增加关键词识别或正则自动开工。
2. **AI 工作与人工待办分清。** AI 自己推进的交付登记为目标，出现在工作区任务主页；`crew todo` 只登记必须由用户亲手处理的事项。目标保存本身不启动载体，显式 start 才开工。
3. **多个任务独立。** 总对话、各目标执行和成员执行保持独立身份与历史；启动后一个任务不停止前一个。假载体验证了两个真实用户例子并行，同时保持验收原文。
4. **启动重试不重做。** `goal_actions` 对 start 使用目标、轮次、需求版本和人员组成的稳定输入标识；assign/start 再加入成员任务内容。重试复用既有输入回执，不再次发 native input。新的轮次或需求版本仍有新的输入范围。
5. **leader 可以自主改名。** `crew rename` 与 Codex `crew_rename` 走 `session_ops.rename_self`，只允许当前 leader 改自己的显示名。原会话 ID、成员关系、目标和历史保留；相同名字重试不再写改名事件。
6. **只允许单层成员。** `spawn.authorize` 在 Manager、CLI（含 dry-run）、Codex `crew_spawn` 和 `/api/leader` 统一校验。只有本项目当前 leader 能在自己的组创建成员；已有成员不能用 join 自提权、不能发送 `kind=task` 再向别人派工。无 AI 身份的桌面用户仍可建立 leader。
7. **成员也不能创建原生子代理。** Claude 成员新建/恢复时禁用 Agent、Task、TeamCreate，并在 Hook 中复核；Codex 成员新建/恢复及目标执行时关闭原生多代理。leader 保留原生能力。仓库内双向 Windows 派工脚本在读取任务和启动载体前拒绝成员。
8. **有限结果不重复唤醒。** 目标消息原有的执行范围去重、批量投递、暂停门禁和有限重试继续保留。普通对话补上同一 `reply_to` 的同一 result/blocker/decision 去重：即使换 transport message ID，也复用原回执，不新增待投递消息。新的明确任务有新的原消息编号，同文结果仍可正常提交。ack/information 只存档。
9. **全局助手独立模型设置。** `assistant` purpose 默认跟随 default，显式值整体覆盖 runtime/model/effort，放入现有 `ai_task_settings`。旧 `ai_json` 保持原有字段集合；保存不唤醒 AI。

## 原生 Codex 配置依据

按官方当前源码，`multi_agent_version_override` 优先处理 `features.multi_agent_v2`，随后用 `agents.enabled=false` 显式选 Disabled；该覆盖优先于模型能力。工具注册对 Disabled 返回 false。因此成员同时设置 `agents.enabled=false`、`features.multi_agent_v2=false`，并关闭兼容的 `features.multi_agent`；没有添加未支持的 RPC 顶层参数。测试断言的是发给 thread/start 和 thread/resume 的实际 fake RPC 参数。

- [Codex 配置 schema](https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/config.schema.json)
- [Codex 配置选择逻辑](https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/config/mod.rs)
- [Codex 原生协作工具注册](https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/tools/spec_plan.rs)

## 实际验证

所有命令均在工作树 `plugin` 下运行，测试进程先移除继承的 `CREW_SESSION_ID`、`CLAUDE_CODE_SESSION_ID`，使用临时 HOME、假 native client/process 和 loopback HTTP。没有唤醒真实 AI、修改正式数据库或重启正式 Crew。以下测试组有重叠，不将数量相加冒充独立覆盖数。

| 验证组 | 实际结果 | 关键证据 |
| --- | --- | --- |
| `test_delegation_boundary test_codex_tools test_member_creation_http test_shell test_core` | 194 tests，38.124s，OK | 派工被拒绝前无规划/启动/收编副作用；用户与 leader 正常入口可用 |
| `test_delegation_boundary test_codex_dispatch test_claude_dispatch` | 10 tests，8.289s，OK | Windows 脚本成员拒绝、leader 保留能力；假进程起止回执 |
| `test_task_behavior_v2 test_goal_actions test_goal_messages test_goal_http test_session_ops test_codex_tools test_delegation_boundary` | 177 tests，29.538s，OK | 任务执行、消息去重、身份关系和停止/过期门禁 |
| Codex 成员 start/resume、leader 能力保留、成员目标执行三项 + `test_settings test_member_defaults` | 25 tests，1.659s，OK | 新建/恢复请求的原生能力开关与助手配置兼容 |
| `test_task_behavior_v2` + CC/Codex 两项任务启动例子 + `test_core test_runtime_integration test_operations_chat` | 135 tests，20.075s，OK | 双任务与成员 assign/start 重试不增加输入；Codex 自主改名；旧设置请求读取新 assistant 默认值 |
| `git diff --check` | 通过 | 无新增空白错误 |

具体两例通过真正 HTTP 入口保存要求，再模拟 AI 发出 create/start/assign 操作：

- 健身打卡 App：要求保存打卡记录且重启后可查看；测试验证任务原文、独立执行和重复启动回执，没有生成或声称验收一个真实健身 App。
- MIB 流式声纹：保留 A、B、重叠区段，以及“整段流式结束后 400ms”约束；测试验证任务存储和调度，没有运行音频算法或声称达到 400ms。守则明确不能拿单次推理耗时代替该端到端验收。

首次自检发现并修复了新测试缺少隔离 blacklist、改名嵌套事务，以及设置返回新增字段后旧测试完整对象期望的问题；上表列的是修正后通过的对应范围。目标负例会输出 `BLOCKED by Crew: ... pausing`，这是验证禁止复工的预期输出，测试进程退出码为 0。

## 尚未验证与集成边界

- 未运行真实 CC/Codex 对话，因此不能把提示词中的任务识别、授权语义判断和自主改名宣称为真实模型可靠性验证。fake RPC 证明宿主传参和状态机，不证明模型会每次正确选择工具。
- 本轮没有构建或启动桌面包；任务主页、跨工作区助手和原生桌面接续的最终联调由主控及对应模块验证。
- 派工校验使用 Crew 已绑定的可信身份。它是产品协作规则，不是对具有本机 shell/数据库访问权的恶意程序的安全隔离；未修改用户机器上已经复制安装的自定义派工 skill。
- 历史成员子代理记录继续显示，未删除或重写旧历史。正式启动中的旧进程未被打断；能力配置在后续新建/恢复时应用。
- 普通对话的语义相近但不同文字结果不做模糊合并；没有 `reply_to` 的独立结果不会仅因正文相同就永久丢弃。目标执行仍沿用明确执行范围去重。
- AGENTS 引用的 `docs/architecture/README.md` 在本基线不存在；本轮依据现有功能源码和相关测试定位，不把历史报告视为当前实现。

## 本轮文件归属

派工规则：`spawn.py`、`guard.py`、`chat.py` 的 start 授权段、`web.py` 的 leader 授权段、`codex_runtime.py` 的成员能力配置、`codex_tools.py` 的 crew_spawn 授权；双向 Windows 派工脚本；member 守则。

任务行为：`session_ops.py`、`cli.py` 的 rename/join 段、`goal_actions.py`、`goal_messages.py`、`messaging.py`、`goal_runtime.py` 的共同守则、leader 守则。Codex `crew_rename` spec/dispatch 由全局助手 agent 整合。

设置：`settings.py` 的 assistant purpose 和对应设置/运维/运行时回归期望。测试新增 `test_delegation_boundary.py`、`test_task_behavior_v2.py`，并扩展相关 HTTP、Codex、目标与设置测试。README 只同步了原生子代理单层边界一段。
