# Codex 接入与并行开发

目标：保留项目、leader、成员、白板、拍板和 Git 管理员这些概念；创建 leader 或成员时可选择 Claude Code 或 Codex，允许两种工具混合协作。用户已将 Deepseekharness 延后，当前优先 Codex。后续仍包括桌面应用、新建或选择 Git 工作区，以及 Lark / QQ 的多项目、多 leader 交互。

## 当前开发边界

2026-10-24，接入分支 `codex/multi-runtime` 从 `e45420a` 建立，位置 `Temp/wt/multi-runtime`。只在该 worktree 开发。用户已表示会协调隔壁 Claude，让它先完成当前工作。

`Temp/wt/chandigest` 正在修改交流区摘要，涉及 `chat.py` 的 executable helper / `_launch`，以及 CLI、schema、Web 服务、前端主文件与测试。独立 worktree 不能避免这些位置的合并冲突。当前先新增 Codex 客户端、事件转换和独立测试，不修改这些共享文件；待摘要提交后，基于最新代码接入，保留它的行为与测试。

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

## 已有模块与待接入项

- `crew/codex_client.py`：双向 JSONL 客户端。直接启动 npm 安装的原生 Codex，而非 Node 包装进程，使持有的 PID 对应实际运行端。RPC 接受与轮次完成分开；超时不隐式重启或记成功。模型列表来自原生 `model/list`。
- `crew/codex_events.py`：独立事件转换；兼容当前 contextCompaction item 和旧压缩通知，最终消息覆盖流式片段，忽略重复完成及旧轮次事件。
- `crew/codex_tools.py`：由宿主绑定 Crew 身份的白板动态工具。可读写自身任务卡、登记真实拍板题号、交接和报告。消息投递通过宿主 callback，未接路由时明确失败；消息幂等和重投由宿主负责。
- `crew/codex_guard.py`、`crew_codex_hook.py`：Codex Bash / apply_patch 到现有守卫的桥接；完整检查多文件 patch 与移动的两端。身份绑定到启动参数，原生 thread ID 不作为 Crew 权限身份。

目前这些模块独立存在，现有服务和创建页面仍按 Claude 运行。尚未接入运行时选择、Crew ID / Codex ID 持久化、跨工具投递、真实模型多轮执行、桌面入口或远程路由。

验证范围：原有 766 项测试基线通过；新增模块使用隔离白板与模拟协议子进程验证。真实本机 CLI 已验证初始化、动态模型清单、原生 PID 和正常退出；隔离 CODEX_HOME 的无模型线程创建已验证每线程 hooks 配置可被解析。以上均不代替真实模型执行与完整应用流程验收。

每次创建和恢复 Codex 线程时须重新传入宿主生成的 hooks 配置，避免恢复时丢失 Crew 绑定。已核实本机 `config.bypass_hook_trust` 接受布尔值，可仅作用于该线程的已知 Crew hook，无需更改用户全局配置。还须验证实际 hook 执行与命令引用；Codex 对 hook 超时、外部崩溃可能继续执行，且已启动 exec 的 write_stdin 不重复触发 PreToolUse。不能据此承诺所有工具路径均经过守卫。
