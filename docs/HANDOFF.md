# Crew 交接（2026-10-21）

接手的 AI 先读这份，再读 `README.md` 和 `docs/DESIGN.md`。做完就删掉这份文件。

## 本轮接续进度（2026-10-21）

尚未全部完成，保留交接。**主检出 main 仍是 5709d44，没有合入、没有重启旧服务。**

- 已完成的集成分支：`codex/web-leader`，worktree `C:\Tools\Crew\Temp\wt\web-leader`。包含下文任务二、任务三及前端：发现候选/收编/混选成员；网页常驻 leader、多轮流式、Markdown、工具折叠、组员状态和待拍板；网页刷新重放、服务重启后同 ID 恢复；CLI `leader start` 转到运行中的网页服务。成员继续沿用上次确定的终端启动方式。
- 已修复同 ID 多 PID 导致重复候选、busy 被 idle 盖掉、旧进程钩子状态遮住当前进程的问题；原始 agents 列表仍保留所有 PID。`spawn_id` 重新登记和 join 后保留，避免丢失恢复所需的任务文件标识。
- 集成分支已通过 139 个测试（含 `spawn_id` 回归），JS 语法检查和 diff 检查通过。真实隔离 HTTP/SSE 与浏览器验收已通过：多轮、服务重启恢复、无重复用户消息、流式文本与完整回复一致、Markdown、忙时禁用发送、刷新历史。所有探针都正常 EOF 退出，未操作用户现有会话。
- 实机证据：`Temp/leader-probe/http-20261021-201342/state.json`、`history-after-browser.json`。界面示例 `Temp/chat-preview.jpg`，实机截图 `Temp/chat-live-check.jpg`。
- 任务一底层在独立分支 `codex/open-vscode`，提交 `5368f42`，worktree `Temp/wt/open-vscode`。包含空闲/入口/PID 创建时间检查和两阶段函数，**CLI/API 尚未接线**。不能直接宣称 `crew open` 可用。该分支的 spawn_id 单行修复已先带进集成分支。
- VS Code 已查明：`code <cwd>` 返回不等窗口 ready，`code --open-url` 同时带目录会忽略目录。已问用户是否接受两步：先开对应目录，窗口就绪后再点一次打开会话；**尚未答复，不能自行按两步方案启用入口**。
- 网页 leader 的进程入口是 `sdk-cli`。未来接任务一时：先用 `chat.Manager.is_owned/busy` 判断；用户第二次确认后走 `Manager.detach(session_id)`，只关自己的 stdin 并等正常退出，再开原 ID 的 URI。不要把 SDK 进程交给 `TerminateProcess`。现有终端进程关闭仍须空闲和用户明确确认，VS Code 进程永远不关。
- 用户还未回答新组员要不要也改后台；保持原来的终端方案，别扩到后台成员。
- 已查旧看板正在监听 `127.0.0.1:47811`，当时 PID `16156`，命令为主目录 `plugin/crew_cli.py web --port 47811`。已请求允许合入并重启这一个看板服务，**尚未答复**。合入前重新核对进程身份。不要先更新 main 的前端，否则旧内存后端会配上新静态页面，接口错位。确认后先停止旧看板、再合入并启动新版；不关其他会话或终端窗口。

后台 leader 最小实验结论：本机 CLI 2.1.306 的 stream-json（不用 --bare）有跨会话收件箱，空闲时能双向接 SendMessage；--resume 同 ID 多轮可行；完整插件/守则加载且 bypassPermissions 下 Crew 守卫仍有效。Python SDK 未安装，不需新增依赖。详见 `Temp/leader-probe/REPORT.json`。

## 最重要的一条

**Crew 是一个人用的工作流工具（用户 Akito，只在他自己这台 Windows 机器上跑），不是产品。**
- 不要过度冗余，不要防御性编程：不加没人要的参数校验、兜底、重试、配置项、兼容层。出错就让它直接报出来。
- 能删就删，能复用就不新写。测试只盖核心逻辑，不为每个分支补用例。
- 现有代码里有前面 Codex 写的防御性代码，改到哪里就顺手简化哪里；不要专门做一轮大重构。

## 现状

- 仓库：`C:\Tools\Crew`（GitHub `akitokunnn/Crew`，主干 main，最新 9dd2627，121 个测试全过）。用 qianlong-ai 推送，**不要切 gh 账号**。Crew 仓库可以自己决定什么时候推。
- 插件装在用户级，**运行时直接跑 `C:\Tools\Crew\plugin` 里的代码**：主检出就是所有会话钩子正在用的代码。改代码在 `C:\Tools\Crew\Temp\wt\<名字>` 开 worktree，测完（`cd plugin` 后 `python -m unittest discover -s tests`）再合进 main。
- 已实机验收的：钩子自动打卡、守卫和黑名单、借还设备、`crew spawn` 开终端会话、网页答复待拍板、`crew leader start` 分组、挪组、看板页和用量页。
- 看板网页：`crew web --open`，地址 http://127.0.0.1:47811 。
- Windows Terminal 的 crew 窗口里还留着几个测试标签页（boss、测试员、测试员2、测试组A、测试组B），让用户自己关，不要去杀进程。

## 和用户打交道

- 说中文，用大白话。聊天里不贴代码块，要指代码位置就给文件链接。
- **小问题直接在对话里问一句就行，不要进计划模式。** 大功能才给一个简短的计划。
- 提交信息用中文，写清"做了什么、为什么"，不写 emoji，**不加 Co-Authored-By 署名行**。
- 没得到用户许可，不要杀进程、不要关他的窗口。

## 用户的实际用法（前面误解了，以这里为准）

1. 他先在 **VS Code 的 Claude 聊天页**里开很多个会话，探索、调研、实现。
2. 等东西慢慢成了体系，才"召唤" leader 来管**这些已经存在的会话**。
3. 他不喜欢在终端里操作，觉得太丑。和 leader 聊天要在**网页上**，而且界面要好看。

## 任务一（小）：会话能在 VS Code 里打开接着聊

新会话照旧由 `crew spawn` 开在终端里。用户之后想单独跟某个会话聊时，要能在 VS Code 的聊天页里打开它。做法：看板卡片上加一个按钮，命令行加 `crew open <名字>`。

已经查清楚的事实（读的是本机扩展 2.1.285 的 `extension.js`）：
- 扩展有一个网址入口：`vscode://anthropic.claude-code/open?session=<会话ID>`，可以在聊天页打开指定会话。还能带 `prompt=`，但这段话只会填进输入框，不会自动发出去。
- 扩展只在**当前窗口文件夹**的会话里找（`includeWorktrees: false`），找不到就开一个空白新会话。所以要先用 `code <会话所在文件夹>` 把对的窗口叫到前面，再用 `os.startfile(网址)` 打开。worktree 里的会话会新开一个指向那个 worktree 的窗口。**还没实测**：新开的窗口里网址会不会发早了、落到别的窗口上。
- 同一个会话不能两边同时开着。终端里还开着的，要先关掉终端里的进程，launch.ps1 退出后标签页会自动关。只在用户点了确认、而且会话空闲时才关；在干活的会话按钮置灰。VS Code 里的会话永远不关。
- 进程信息有两处：`claude agents --json` 给出 pid、cwd、sessionId、name、status（idle / busy）；`~/.claude/sessions/<pid>.json` 里的 `entrypoint` 能看出在哪运行（VS Code 里是 `claude-vscode`）。
- 续上以后会话 ID 应该不变（claude 有 `--fork-session` 专门用来换新 ID），要实测确认。
- `board._register` 重新登记会话时会把 `spawn_id` 清空（`spawn_id=excluded.spawn_id`），要改成保留原值。

## 任务二（致命）：网页看不到 VS Code 里的会话，没法把它们收为组员

- 根因：白板只认触发过 Crew 钩子的会话。插件只在装好之后启动、或者执行过 `/reload-plugins` 的会话里生效；早就开着的会话要等它下一次收到消息才会自动登记。用户那些 VS Code 会话大多从来没出现在白板上。
- `claude agents --json` 能列出所有活着的会话（实测 16 个，带 cwd、name、status）。网页现在只拿它来判断"离线"和补 SendMessage 地址（`plugin/crew/web.py` 的 `Service.state`）。
- 要做的：
  - 网页把"在这个项目目录下、还没登记的活会话"列成候选成员，显示会话标题。标题沿用用量里的取法：`usage.py` 读 transcript 里的 `ai-title` 记录。
  - 一键收进组：调用 `board.register_session`，带上名字和分组；git 信息用 `hooks.git_details`。
  - 开 leader 的表单里也能选这些候选会话。
  - 没有钩子的会话，状态用 `claude agents` 里的 status。
  - leader 用 SendMessage 给这些会话发消息，地址就是 agents 里的 name。

## 任务三（大）：网页里和 leader 聊天的好看前端

- leader 不再开在终端里。网页里有聊天界面：渲染 Markdown，回复流式显示，工具调用折叠起来，旁边能看到组员状态。
- 建议做法：网页服务进程用 Claude Agent SDK（Python 的 `claude-agent-sdk`，多轮对话用 `ClaudeSDKClient`）跑 leader，或者用 `claude -p --input-format stream-json --output-format stream-json --verbose` 起子进程，再用 SSE 推到浏览器。会话 ID 记进白板，做完任务一以后也能在 VS Code 里打开 leader 接着聊。
  - 权限模式用 bypassPermissions，和其他会话保持一致，否则跨会话消息会被扣住等人批准。
  - 守则复用 `plugin/agents/leader.md` 和 `plugin/skills/leader/SKILL.md`。
- **先做一个小实验再动手**：SDK 或 `-p` 跑起来的会话，有没有跨会话收件箱？`claude agents` 里能不能看到它？组员 SendMessage 给它能不能收到？已知 `--bare` 会关掉收件箱。如果收不到，组员就改成写白板（`crew report` / `crew handoff`），leader 从白板读。实验结果先跟用户说。
- 先问用户：`crew spawn` 开的组员要不要也改成后台运行、在网页里看，不再开终端标签页？用户说过不喜欢终端；但他上一次同意的是"新会话照旧开在终端，之后能在 VS Code 里打开"。
- 网页现在是原生 JS，没用框架（`plugin/crew/web/`）。聊天界面要好看，可以引一个轻量的 Markdown 库；不要为此引入构建流程。

## 第二版（还没开始）

飞书双向通知（leader 私聊用户、用户回复进 leader），leader 巡查（先跟用户聊清楚实际场景），QQ，Claude 额度百分比。
