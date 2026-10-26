# Codex 接入与并行开发

目标：保留项目、leader、成员、白板、拍板和 Git 管理员这些概念；创建 leader 或成员时可选择 Claude Code 或 Codex，允许两种工具混合协作。用户已将 Deepseekharness 延后，当前优先 Codex。后续仍包括桌面应用、新建或选择 Git 工作区，以及 Lark / QQ 的多项目、多 leader 交互。

## 当前开发边界

2026-10-24，接入分支 `codex/multi-runtime` 从 `e45420a` 建立，位置 `Temp/wt/multi-runtime`。只在该 worktree 开发。用户已表示会协调隔壁 Claude，让它先完成当前工作。

先提交独立 Codex 模块，随后合入 Claude 已提交的交流区摘要、leader 优先级和隐藏题号。成员叫醒自己的 leader 已以 `a04ac3f` 推上 main。本轮其余功能仅在该分支开发与隔离验证，未重启正式 47811 服务、未向正式组员发迁移通知。

## 实施顺序与验收

1. 独立 app-server 客户端：核对已安装 CLI 的协议，支持初始化、持久 thread 创建/恢复、流式事件、中断、压缩、工具回调与明确失败；不把初始化成功视为模型执行成功。
2. 会话运行时接入：旧会话默认 Claude；另外保存 Crew 会话 ID 与原生 thread ID 的关联。创建时选择执行工具，模型与 effort 按工具校验，跨工具不继承无效模型。现有 Claude 会话历史不会因切换一个字段自动变成 Codex 历史。
3. 混合协作：提供 Crew 自己的寻址和投递入口，验证 CC → Codex、Codex → CC、Codex → Codex。原生 Claude SendMessage 无法直接寻址 Codex；不能只修改提示词声称已打通。
4. 生命周期和页面：状态来源合并；支持流式展示、后台叫醒、同会话恢复、中断、压缩、归档、拍板回复和 Git 管理；前端创建控件选择执行工具，模型清单随之变化。
5. 桌面入口：打开后列工作区，支持新建 Git 仓库或选择已有 Git 文件夹并进入项目；保留现有服务和数据。
6. 远程交互：明确“项目 / leader / 当前对话”路由，避免仅靠全局当前 leader 造成串项目；为 QQ 和 Lark 分别验证选目标、引用回复、拍板和排队反馈。

完整验收须包含现有 CC 回归、真实 Codex 多轮与恢复、混合消息往返、实际守卫拒绝危险操作、页面流程和桌面工作区流程；模拟协议单元测试不等于端到端通过。

## 协议和守卫

以本机 `codex-cli 0.163.0` 生成的 JSON schema 和 [官方 app-server 文档](https://developers.openai.com/codex/app-server) 为依据，采用 stdio JSONL 的 `thread/*`、`turn/*` 和 `item/*` 协议，使用本机已有登录，不另搭模型 API。

客户端工具回调可用于 Crew 消息和白板操作，但 Codex 内建 shell、文件工具不因此自动经过 Crew 的 Claude hooks。`on-request` 的审批回调也不是每条工具调用的执行前守卫。接入时必须验证可阻止的执行路径，不能只观察执行后事件便宣称黑名单、文件归属和 Git 管理已受保护。

`codex_events.Events` 将 Codex 通知转成现有网页能理解的 text / assistant / tool_result / status 等事件。activity、context 仅用于内存实时状态；不保存思考内容。只有 `turn.status == completed` 记为成功，failed、interrupted 和未知状态均不记为成功。上下文使用最近请求的 token usage，累计计费用量不当作当前上下文。

## 已接入模块

- `crew/codex_client.py`：双向 JSONL 客户端。直接启动 npm 安装的原生 Codex，而非 Node 包装进程，使持有的 PID 对应实际运行端。RPC 接受与轮次完成分开；超时不隐式重启或记成功。模型列表来自原生 `model/list`。
- `crew/codex_events.py`：独立事件转换；兼容当前 contextCompaction item 和旧压缩通知，最终消息覆盖流式片段，忽略重复完成及旧轮次事件。
- `crew/codex_tools.py`：由宿主绑定 Crew 身份的白板动态工具。可读写自身任务卡、登记真实拍板题号、交接和报告；`crew_doc_add` / `crew_doc_list` / `crew_doc_refresh` 登记、查找和整理本项目交付文档，新增文档要求标题、摘要。消息投递通过宿主 callback，未接路由时明确失败；消息幂等和重投由宿主负责。
- `crew/codex_guard.py`、`crew_codex_hook.py`：Codex Bash / apply_patch 到现有守卫的桥接；完整检查多文件 patch 与移动的两端。身份绑定到启动参数，原生 thread ID 不作为 Crew 权限身份。

- `crew/codex_runtime.py`：原生 thread 生命周期、Crew 映射、每线程守卫、角色守则、动态白板工具、模型/effort 回报、图片、中断、压缩和恢复。原生 PID 与创建时间的租约防止服务重启后重复启动仍存活的会话。
- `crew/runtimes.py`、`runtime_catalog.py`：执行工具记录与本机动态模型清单。state 轮询不启动新进程；显式刷新才访问 `model/list`。跨工具不继承不兼容的模型。
- `crew/messaging.py`、`chat.py`、`hooks.py`：持久混合工具投递，绑定可信 Crew 发信人，工作中排队；外部 Claude 下一次 hook 收取。两种托管会话均将 `CREW_HOME` 与自身 launcher 放入环境，避免隔离测试调用正式 home。
- `desktop/`：Electron 窗口、工作区选择/新建、托盘、已有服务复用和自有后端的优雅退出。源码、未压缩 exe 和 portable 均使用独立 home 做 smoke。

验证分层：原有 766 项基线通过；新增白板、权限、消息和协议使用临时数据库 / 假进程回归。真实 Codex 已在临时 Git 项目与 CODEX_HOME 完成动态工具、连续对话、停止后同 thread 恢复、真实 shell 黑名单拒绝、真实 apply_patch 文件归属拒绝且文件未变、模型 effort 回报、手动压缩、中断及中断后继续。真实模型测试不访问正式 Crew home，临时认证配置在测试结束移除。混合工具方向使用宿主集成测试，没有把它说成两家真实模型的对话验收。

每次创建和恢复线程均重新传宿主生成的 hooks 配置。`config.bypass_hook_trust` 仅信任该线程的已知 Crew hook，不更改全局配置。Windows PowerShell 可能把原生 exit 2 映射成 1，本桥使用 exit 0 加显式 JSON deny；真实原生通知已验证状态为 blocked。参考 [官方 hook 输出协议](https://learn.chatgpt.com/docs/hooks) 与 [Windows 退出码问题](https://github.com/openai/codex/issues/54072)。

## 明确边界

- 原生可见性按生命周期管理：人员总会话持久化，`thread/name/set` 明确设为“Crew 名字 · leader/组员”；启动、恢复、正常输入及网页改名同步标题，同步失败保留真实原因。摘要、运维与 `codex-dispatch` 一次性执行使用 ephemeral。独立目标仍持久化，运行期间使用人员简名；停止时归档，已结束目标待原生最终轮次空闲后关闭并归档，不打断最后输出。续跑先查询归档清单，只恢复该执行已绑定的原生 ID。归档保留历史，不能当成删除；原生协议没有单独隐藏活跃持久线程的开关。
- 临时派工用 `codex exec --ephemeral --json` 的 `turn.completed.usage` 保存用量，不依赖原生 rollout，也不再按时间窗口误配附近的普通会话。任务文件、最终输出、stdout/stderr、退出码和派工记录仍保留；无用量回执保留未知，不把失败改写为成功。原生协议依据见 [非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode) 与 [app-server](https://learn.chatgpt.com/docs/app-server)。

- 0.2.1 起，只有 Codex CLI 也能使用全部内建 AI 入口。设置页统一配置默认 AI、摘要、Git 管理员和运维的载体、模型与 effort；任务可跟随默认，空模型/effort 使用原生配置。未设置且只有一个已安装工具时自动采用它，两者都有则明确要求选择。成员仍继承所属 leader，已有会话不因全局设置改变。Codex 摘要使用独立临时线程，记录实际模型与未知费用；旧 Claude 摘要和原生会话保持原身份。

- 成员支持显式更换 CC / Codex：先本地生成可编辑交接摘要，确认后只存配置，下次发送或唤醒再启动目标运行端。Crew 身份与任务卡不变，两个运行端的原生历史分别保存；切回时恢复该端原有绑定。忙碌、外部进程、未排空输出或仍占用原生租约时拒绝更换。Claude 使用原 Crew UUID，Codex 使用独立 thread ID，不转换历史文件。
- 信任模式按 leader 独立设置，下属只继承所属组。保存不发通知、不唤醒，正常对话/恢复/压缩时带最新显式开关；关闭说明覆盖旧授权。旧载体的工具和元数据入口拒绝迟到写入，但守卫不负责终止已开始的命令，也不能替代更换前的进程退出确认。
- Codex 对 hook 超时、外部崩溃可能继续执行；Crew 观察到执行前 hook 失败会停止自己的会话，但这是故障收敛，不能承诺抢在所有副作用前。已启动 exec 的 `write_stdin` 不重复执行 PreToolUse。其他原生工具或 MCP 需要自己的守卫适配，不能声称所有路径都有同等覆盖。
- 不在运行中的 Claude 历史上直接改执行工具字段；通过上述确认流程切换。Codex 原生映射缺失则明确报错，避免悄悄新建历史。Codex 暂不支持转交 VS Code，相关按钮和服务端均阻止误开 Claude URI。
- Lark 本轮完成交互方案与接入清单，尚未配置专用机器人或验证真实收发。QQ 的新路由与旧按钮保护通过 FakeQQ 验证，未向正式账号发送测试消息。
- 分支版默认桌面入口会复用指定 home 的健康服务；如果正式旧服务仍在运行，验收新功能请使用独立 `--home`，不要把复用旧服务当成新版本已上线。
