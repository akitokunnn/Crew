# 原生接回与压缩反馈自检（2026-10-30）

本次从 `rebuild/v2` 的 `a31ca95`、桌面 0.3.1 开始。正式 Crew HOME、用户对话和原生桌面未被重启或修改。真实 AI 验证由用户明确授权，仅在临时 HOME、临时项目中使用原生发现返回的 `gpt-5.6-luna`，effort `low`；没有切换到更贵模型。原生版本为 Codex CLI 0.165.0。

## 复现与修改

- 接回：正式只读错误是 `thread/resume: ... already has an active writer`。两个隔离 app-server 可稳定复现。首持有者返回 `thread/unsubscribe: unsubscribed` 后，第二进程仍无法接管；首持有者实际关闭后，同一原生 ID 可以恢复。Crew 现在把这个错误作为可处理的冲突，说明需要关闭持有该对话的原生客户端，保留原生原因、外部门禁、历史和排队消息；不会换 ID、删除锁或终止用户进程。
- 压缩：真实原生顺序为 `contextCompaction item/started → tokenUsage/updated → item/completed → turn/completed`。旧适配器只处理完成后才收到用量的顺序，遗漏 `compact` 完成事件，前端一直等待。适配器现在处理两种顺序；完成但无测量时报告未知大小，不伪造 token 数。新增两条检查在旧实现上均失败，修后通过。
- 目标范围：`Manager.compact` 现在用 execution ID 选择运行进程、聊天记录和发送通道，保留人员 ID 做身份检查。总对话正在工作不妨碍压缩另一个空闲目标。目标没有已保存原生历史时，不创建新会话来压缩。HTTP 与浏览器范围由同轮其他分工验证。
- 原生工具：本机生成的 `ThreadResumeParams` 不包含 `dynamicTools`。删除了该无效参数，以及“resume 替换工具列表”的假设。工具由 `thread/start` 持久登记；回到 Crew 后重新装载 Crew 指令并由 Crew 的实际 handler 处理。原生桌面不具备 Crew handler，不能宣称它支持 Crew 专用工具。
- Claude 助手：新助手首次启动使用 `--session-id`；原生初始化/回复确认身份后，再次启动使用同一 ID 的 `--resume`。只对 0.3.1 预填了 ID 且完全没有原生输入/输出历史的助手走首次启动；已有回复必须恢复原历史。这部分使用隔离假 CC 验证，未调用真实 Claude。
- 移除了 leader 历史 trust 配置向 Codex 初始化和每条用户输入的重复注入。任务模式由本轮任务领域处理，旧配置仍可读取。

压缩后的 `19264` 是原生对保留上下文的估算，不是摘要请求费用，也不是精确 token 测量。与本机相同版本的官方 [`compact.rs`](https://github.com/openai/codex/blob/rust-v0.165.0/codex-rs/core/src/compact.rs) 在替换历史后先重估、再发完成事件；[`session/mod.rs`](https://github.com/openai/codex/blob/rust-v0.165.0/codex-rs/core/src/session/mod.rs) 的 `recompute_token_usage` 将历史和 base instructions 的估算写入 `last.totalTokens`，其余输入/输出分项清零后通知。录制事件与此格式一致；原生未提供这个值时仍显示未知。

交付后普通讨论允许 CC/Codex 回复与读取，保留原任务、需求和验收。Crew 业务修改必须先明确 `amend`，原生明确编辑工具（CC Edit/Write/NotebookEdit、Codex apply_patch）也遵循此门禁；晋升后的同一执行读取最新需求版本。任意 shell 的文件副作用仍取决于原生权限与现有 guard，本次没有将 full-access 载体宣称为绝对只读。

最终只读审查另发现并修复了 Codex 的批准复检遗漏：讨论中的原生命令请求可以进入单次用户批准，随后仍完整执行原 guard；原生文件编辑请求在 `amend` 前继续拒绝。新增回归同时验证读取批准、黑名单继续生效、修改前拒绝编辑、修改后同执行可批准。将旧 `_validate_approval` 方法仅装回测试进程，新检查按预期失败（1 失败、0 夹具错误），证据为 `discussion-approval-counterproof.json`。CC 使用既有 PreToolUse 路径，没有这一层 app-server 批准复检；本次没有再调用真实 AI。

这项最后修复的专项 `test_goal_discussion_native` 与 `test_native_approvals` 共 16 项通过、失败 0，包含真实无模型 JSONL 审批请求与批准后的效果检查；输出为 `discussion-approval-final.log`。

## 实际验证

在 `plugin` 目录清除测试进程继承的 `CREW_SESSION_ID`、`CLAUDE_CODE_SESSION_ID` 后执行：

```powershell
python -c "import sys,unittest; sys.path.insert(0,'tests'); unittest.main(module=None,argv=['unittest','test_codex_runtime','test_native_handoff','test_personal_native','-v'])"
```

57 项通过，失败 0。另 `test_context_runtime` 15 项与 `test_codex_events` 15 项通过；本分工独立检查合计 87 项，不重复计算。

讨论门禁追加执行 `test_goal_discussion_native`、`test_codex_guard`、`test_hook_project`、`test_hooks_git`、`test_codex_runtime`，73 项通过、失败 0（包含上段部分重叠项，不相加）。其中新增检查覆盖双载体读取/编辑门禁、同执行晋升后的需求刷新、普通回复保留验收、暂停时不接收新输入。把 `a31ca95` 的两个原生 guard 仅在测试进程中装回，双载体检查均按预期失败，夹具错误 0；记录在 `discussion-old-guard-counterproof.json`。

最后只读交叉审查发现 Codex 审批复检仍拒绝已交付讨论中的命令读取。最窄修复仅给 `commandExecution` 放行讨论状态，之后仍经过原有 `codex_guard`；`fileChange` 在 amend 前保持拒绝。`test_goal_discussion_native` 与 `test_native_approvals` 最终 16 项通过，失败 0，覆盖需要用户批准的读取、黑名单拒绝、amend 前后文件变更，以及现有真实无模型 JSONL 审批效果。旧审批方法装回后的新增行为检查失败 1 项、夹具错误 0。证据为 `discussion-approval-final.log` 和 `discussion-approval-counterproof.json`；这部分用假原生传输，不增加真实 AI 调用。CC 没有此 app-server 审批复检层，既有 PreToolUse 对等路径已覆盖。

真实隔离旅程及证据保存在项目 `Temp/v2-feedback-2026-10-30/`：

| 证据 | 实际结果 |
| --- | --- |
| `native-model-discovery.json` | 原生 model/list 确认 Luna ID 与 low effort 可用 |
| `native-protocol-events.json` | 原生压缩真实通知顺序，未记录 reasoning 内容 |
| `compact-recording-old-vs-fixed.json` | 同一真实序列回放旧 `a31ca95` 得到 0 次压缩完成，当前实现得到恰好 1 次；未修改共享源码来做反证 |
| `crew-native-real.json` | Crew 首次回复、压缩完成事件、压缩后回复、交出、占用冲突、关闭测试持有者、同 ID 接回、再次回复；新增外部消息/回复/轮次结果只导入一次 |
| `native-return-tool.json` | 同 ID 恢复后，实际调用一次 `crew_whoami` 成功，并回复 `RETURN_TOOL_OK` |
| `assistant-real-http.json` | 真实 HTTP 打开全局助手、连续两次输入都有回复、同原生 ID、页面状态名称为“助手”、没有新建工作区或任务 |
| `native-regression.log` | 上述 57 项回归输出 |
| `discussion-native-regression.log` | 讨论门禁相关 73 项回归输出 |
| `compact-native-semantics.json` | 原生 0.165.0 官方源码位置、内容 hash 及压缩后上下文估算语义 |
| `discussion-real-http.json` | Luna 真实解释保留原任务/验收/文件；第二条实际 amend、编辑 Hello→Hi、复读、发布新报告，同任务/同轮次/同执行，需求 1→2 |
| `discussion-real-settled.json` | 已发布结果经正常后台收尾变为 completed/finalized；没有启动任何原生进程 |

最后两条真实测试中，原生 Windows on-request 权限要求批准样例读取、编辑、复读。仅通过现有 HTTP 批准这三项确切操作，没有改变生产权限或向模型追加消息。首脚本在原生 idle 时过早关闭后台，尚未等到每 2 秒一次的正常收尾，因目标暂时仍为 running 而断言失败；失败原始记录保留。脚本已改为同时等待业务收尾。另在同一隔离 HOME 重开宿主，并将原生启动设为立即报错，正常后台在约 2 秒内完成已发布的交付，验证了同一任务、同一轮次、Hi 文件、新旧报告和旧验收；实际原生启动次数为 0，没有重新调用模型来覆盖这个失败。

真实验证完成后，临时 HOME 的认证副本与 provider 配置已删除，保留无秘密的验证记录；没有修改正式认证。首轮清理记录为 `native-credentials-cleanup.json`，最后两条完成后的清理记录为 `native-final-credentials-cleanup.json`。

提示长度仅统计隔离测试数据的字符数与 hash，不输出原文或凭据：原生 base instructions 17730 字符；当时 leader 动态工具 JSON 17687 字符，助手 1375 字符。完整原生内建工具目录不可由这个 metadata 确认；字符数也不等同于 token 数。详见 `native-prompt-measurements.json` 和每次实际 request 的长度记录。

## 未验证边界

- 没有操作正式 Codex 桌面来验证关闭某个标签是否释放写入连接，也没有完成真实桌面 UI 的往返验收；同线程 app-server 协议成功不能替代该结论。
- 已证明仅 `thread/unsubscribe` 不足以释放本版本的写入者。应用退出或原生拥有者关闭后的协议恢复通过；不承诺强行抢占仍在线的桌面连接。
- 此轮没有验证真实 Claude 模型、IDE 接续或完整长任务。浏览器展示、压缩入口范围及助手 UI 截图由同轮对应分工保存。
