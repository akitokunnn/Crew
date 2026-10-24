# 管理员修复前的原始审查

2026-10-24，实际模型 claude-opus-5-5，只读审查，未运行测试。以下是修复前的历史意见；主控已采纳三项阻塞并修复，定点复核与最终验收见 [0.1.1 交付记录](DESKTOP_0.1.1.md)。保留原文供追溯。

## 合并判断：暂不建议合并（1 个 P1、2 个 P2）

本轮只读了文件（Read/Grep/Glob）。没有跑命令或测试，没有写文件，也没有读正式 `~/.crew`。以下结论都来自读代码，不包含主控那边的测试结果。实际运行的模型是 Opus 5.5，依据是运行上下文。

### P1：自动恢复不复查 web.json，可能和旧版 `crew web` 同时跑出两个后端
- **问题代码**
  - `plugin/crew/supervisor.py:529-552`：`_recover()` 只探测自己记录的 `self.url`，然后直接 `_launch(recovery=True)`。
  - `_launch` 在 `:376` 只检查 `_free_service_lock()`。
  - 但旧路径不拿 service.lock：`plugin/crew/web.py:1285` 的 `web.serve` → `create_server`（`crew web`、登录自启 `autostart.py:187`、`crew start` 都走 `startup.py:33-34`）。所以"service.lock 空闲"证明不了"没有后端"。
- **触发链**
  1. 自有后端崩溃，进入退避，或连续失败后断路（circuit_open）。
  2. 此时有人运行 `crew start`。它探测残留的 web.json，本机 loopback 端口有时返回 refused，于是 `autostart._existing_server` 返回 False。
  3. 自启任务在 47811 拉起旧版 `crew web`，并改写 web.json。
  4. 退避到期，或用户在管理员页点"尝试恢复"：`_restart()` 判断为允许（`:283-287` 不看登记）；`_recover` 探测旧端口 X，结果是 absent，服务锁也空闲，于是在 X 上再起一个 crew_desktop。
- **影响**：同一个 HOME 下两个后端同时跑 agents/notices/git_ci 工作线程，会重复唤醒、重复通知，web.json 也被覆盖。这违反了"绝不偷偷启动第二个 backend"的红线。旧逻辑里桌面端总是先读 web.json 再决定是否借用，所以这个漏洞是新增的。
- **最小修法**
  - 在 `_recover` 调 `_launch` 之前重新读取 web.json。如果 URL 和 `self.url` 不同，先探测它：同 HOME 且健康或降级，就 `_adopt(..., recovery=True)` 借用；无法确认就设 `_blocked`，拒绝启动。
  - `_restart()` 用同样的判断，让管理员页的"允许恢复"和实际行为一致。
  - 长期方案是让 `web.serve` 也持有 `HomeServiceLock`。这算架构变更，交主控决定。
- **有效验证**：新增 supervisor 用例。fixture 进入 circuit_open 后，在另一个端口起一个兼容的假服务并写入 web.json，然后发 recover 请求。断言 `command_factory` 没被调用，最终结果是 borrowed 或 blocked。

### P2-a：桌面启动超时后无限等待停机，后台卡住时窗口和管理员页都出不来
- **问题代码**
  - `desktop/src/core.cjs:177`：启动 30 秒超时。
  - 超时后走 `:221` 的 `await this.stop()`；受监管分支 `:229-235` 等子进程 exit，没有任何时限。
  - `main.cjs:280` 要等 `backend.start()` 返回或抛错后，`:295` 才创建窗口，`:336` 才创建托盘。
  - supervisor 的 `_shutdown`（`supervisor.py:628-630`）一直等后端真正退出；而 crew_desktop 要到 `serve()` 建好服务器（`crew_desktop.py:79`）之后才读 stdin。
- **影响**：后端卡在 install/Service 初始化、迟迟不打印 URL 时，桌面既没有窗口也没有托盘，看起来像假死。这正是"后台真故障时管理员页面打不开"。旧的非受监管路径最多等 9 秒，这里是退化。
- **最小修法**：受监管、且不是 75 的启动失败，不要等它停机：
  - 推荐：保留 supervisor 继续监测，保持 `owned` 以便退出时再发 shutdown，直接抛出 `startupError` 进入管理员页；
  - 或者：发出 shutdown 后最多等有限时间（不杀进程），然后继续显示界面。
- **有效验证**：在 core.test 里用假 spawn，子进程不输出 URL、也不响应 stdin。断言 `start()` 大约 30 秒内 reject，且子进程没被 kill。

### P2-b：status.json 里的旧后端证据会被提前覆盖，残留 web.json 时管理员永远起不来
- **问题代码**
  - `supervisor.py:650` 在 `_bootstrap()` 之前就 `_publish()`，`:645-646` 的维护分支同样如此。此时 `service_pid` 和 `url` 都是 None，会覆盖上一轮记录的已死 PID 和 URL。
  - 之后 `_inspect` 在 `:435-442` 需要 `previous.url == registry url` 且 PID 确认已死，这个条件再也满足不了。
- **触发链**
  1. 后端被强杀，web.json 残留（这正是当前"全停"之后可能出现的状态）。
  2. 带维护标记打开桌面：连接被 refused，跳过探测，直接 spawn supervisor，退出码 78，旧证据被覆盖。
  3. 撤掉维护标记后，每次打开都在 `:442` 退出 76。
  4. 管理员页显示"系统管理员未运行"，恢复按钮不可用，只能手工删 web.json。
- 同类问题：`:421-423` 在已经持有 supervisor.lock 时，仍按旧 `supervisor_pid` 判断"存活"。Windows 上 PID 被复用，或旧进程刚释放锁还没退出，都会误拒并清空证据。
- **最小修法**
  - bootstrap 定论之前，发布状态时沿用 `_previous_status` 里的 `service_pid` 和 `url`，或者等 bootstrap 结束后再首次发布。
  - 持锁时跳过 `supervisor_pid` 检查。
  - `--probe`（不持锁）改为非阻塞地尝试获取 supervisor.lock，代替 PID 判断。
- **有效验证**
  - 用例一：先准备已死的 P/U 加残留 web.json，带维护标记 `run()` 得到 78；撤掉标记后再 `run()`，应在 U 原端口启动。
  - 用例二：status 里的 `supervisor_pid` 设成一个存活的无关 PID，bootstrap 仍能继续。

### 已核对、未发现 P1/P2
- 活 PID 只要 HTTP 超时，就只标为 degraded，不会杀进程也不会替换。`_port_free` 要求 `_alive() is False`，并用独占 bind（不 listen）。
- 恢复使用同一端口；`_drain_output` 拒绝端口变化。
- 桌面 EOF 或 BrokenPipe 不会让管理员退出（`_print`、`_reader`）。显式 shutdown 会等后端真正退出；借用的后端不会被停止。
- 控制请求绑定 instance_id 并做幂等；维护标记会阻止启动和自动/手动恢复。
- 管理员页全部用 textContent 渲染，没有 innerHTML 注入面。
- 紧急修复抽查：`git_process.run` 用 DEVNULL 加临时文件、默认 10 秒；worktree add 在 `spawn.py:138` 是 `timeout=None`。chat.py/chat.js 本轮没有重读。

### 建议（非阻断）
1. 借用的后端退出后也会被自动重建，分不清是有意停机还是崩溃。建议借用模式只上报，不自动恢复。
2. 桌面崩溃后，留下的 supervisor 没有任何 shutdown 通道（只能手动结束进程）。可以考虑增加一个受实例绑定的 `shutdown` 控制动作。
3. 主循环（`supervisor.py:663-682`）没有兜底 try。比如 `HomeServiceLock.acquire` 在 open 阶段抛出非 EACCES 的 OSError 时，管理员会退出，后端随之因 stdin EOF 停机。
4. `--probe` 会建目录、短暂占用 service.lock 和端口，可能和并发启动撞上，后果只是一次可恢复的 75 退出。
