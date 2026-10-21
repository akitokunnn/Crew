# Crew 接续（2026-10-22）

代码已经启用；只剩 VS Code 最后一次实机续聊验收，正等用户手动允许打开链接。验收完成后删掉本文件。日常用法和设计已更新到 README.md、docs/DESIGN.md。

## 已完成

- C:\Tools\Crew 的 main 已合入并推送到 72fc81d。155 个测试全部通过，JS 语法和 diff 检查通过。
- 正式看板 http://127.0.0.1:47811/ 已启动，本轮 PID 5392。旧服务当时已退出，没有停止用户其他进程。日志 ~/.crew/web-20261022-011237.log 和同名 .error.log；已确认没有错误。
- 正式页面实查发现 10 个项目候选会话，标题、状态、收编入口正常，agents_error=null。未给用户现有会话发消息或改身份。
- 新成员与 leader 均默认后台运行，网页支持多轮、流式、Markdown、工具折叠、组员状态、待拍板，刷新重放，服务重启后同 ID 恢复。
- 候选发现/收编、leader 混选、成员/leader 守则和身份、自建 peer 自动消息、服务重启记忆、原生 PID 与 agents 对齐均通过隔离实机验收。
- CLI crew open、看板和对话页的 VS Code 入口已接通两步流程；忙时拒绝。网页进程只 EOF；旧终端核对 PID 创建时间；VS Code 进程永不关闭。
- 已收会话不会因活进程或后续普通钩子重新显示活跃，综合回归通过。

## 用户已经确定

新组员也在网页看，按需转到 VS Code；接受先开目录、确认正确窗口就绪后再续聊；允许合入并重启看板。上述实现、合入、启用已经完成，不必再次问。

## 唯一剩余：VS Code 最后实机续聊

- 临时页面 http://127.0.0.1:3754/#chat=82749404-36a6-41f7-9290-6c5d626fc324 。
- 独立测试目录 C:\Tools\Crew\Temp\leader-probe\member-handoff-prepared\project 。成员 Web-Member-Probe-010230，会话 ID 82749404-36a6-41f7-9290-6c5d626fc324。
- 用户已经手动信任了目录；现在新测试 VS Code 窗口 ID 130157194、标题 Welcome - project - Visual Studio Code，Explorer 已确认完整目录正确、没有 Restricted Mode，Claude 扩展已启用。
- 用户另有 MIB_Project 窗口 329778 和后来改为 Crew 根目录的窗口 98899080，不要关闭它们。
- 第一阶段取消已实测保留原成员 PID 23876。再次准备正确 project 窗口后，根代理已点第二步。PID 23876 正常 EOF，原生退出码 0；journal 无 error，同 ID 尚无活进程（URI 确认前）。
- 现在测试窗口弹出 Allow 'Claude Code for VS Code' extension to open this URI?；已异步请用户手动点 Open，无需勾选 Do not ask me again。computer-use 技能禁止代点安全权限，不能绕过。等待用户回复或只读确认该弹框已由用户处理。
- 之后确认正确测试窗口显示原历史；在 Claude 输入框里问“只回复最初 MEMBER_ROLE_READY 开头完整标记和 MEMBER_RESUMED 开头标记，不调用工具”，预期均含 010230。核对 agents 的会话 ID 不变、entrypoint=claude-vscode，没有双开；网页随后应显示在 VS Code 并禁用输入。
- 测试项目 .claude/settings.local.json 已禁用安装的 crew@crew，hooks 指向临时插件和临时白板，不能改正式白板。
- 验收后只清理本轮自建临时服务、进程和测试窗口；协作 agent /root/leader_probe 持有探针上下文，可唤起清理。不碰用户其他会话。最后删本文件，合入推送，再标记 goal complete。

## 位置和证据

集成 worktree C:\Tools\Crew\Temp\wt\web-leader，分支 codex/web-leader。

最新证据 Temp/leader-probe/member-handoff-prepared/state.json 与 handoff-stages.json。网页成员截图 Temp/member-chat-verified.jpg，正式新版截图 Temp/crew-board-enabled.jpg。前轮证据在 Temp/leader-probe/http-20261021-201342/、peer-http-20261021-202337/，候选摘要 Temp/live-candidate-check-result.json。

## 约定

Crew 是 Akito 的单人本机工具，用中文、大白话，不堆配置、兼容层或无用测试。主检出是钩子的实时代码，继续在 worktree 改，测完再合 main。不要关用户的进程或窗口。中文提交，无 emoji、无 Co-Authored-By。GitHub 用 qianlong-ai，不切 gh 账号；允许自行决定推送时间。飞书/QQ、leader 巡查、Claude 额度百分比留下一版。
