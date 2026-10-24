# Claude Opus 5.5 末轮定点复核原文

2026-10-24。此轮确认两个剩余代码缺陷已消除，同时指出任务书高估了一条测试覆盖。主控按原文要求补“没有服务登记但实际后台锁占用”的回归；执行结果和全套验证范围见 [0.1.1 交付记录](DESKTOP_0.1.1.md)。此意见为只读源码审查，不代替主控实际测试。

## 结论：两条剩余 P2 的代码缺陷都已消除。另有一个验收缺口：任务书说有"无 registry 但服务锁占用→拒绝"的测试，仓内没找到

| 剩余项 | 结论 |
|---|---|
| P2-a 迟到 URL 重连到旧端口 | **已修复**（supervisor 和 main.cjs 两处都改了，测试和 smoke 都覆盖到） |
| P2-b 无 web.json 时旧 service_pid 被复用后一直退出 76 | **代码已修复**，正向测试也在；但没找到"锁占用→拒绝"的测试 |

### P2-a 核对
- `supervisor.py:328`：只要有当前进程（`service_pid` 不是 None），url 就只发布 `self.url`，初始化期间是 None。旧的 {pid, url} 只放在 `backend_evidence`（`:329`）。只有在 `:567-568` 收到新进程自己公布的 URL 后，证据才更新。
- 我查了 `_publish` 在什么情况下还会回落到旧 URL：只有 `service_pid` 为 None 时，状态是 starting/failed/paused。`main.cjs:216` 只接受 healthy/degraded，所以这些状态不会触发重连。
- `main.cjs:218-227`：先 `await core.projects(recovered)`。失败时直接返回快照，追加一条 warning 事件"后台尚不能读取工作区"，`startupError` 和 `backend.url` 都不动。成功后（`:225`）再确认一次 `startupError` 还在、而且没有工作区标签，才调用 `tabs.reconnect`。`core.cjs:56,68` 在非 2xx 或列表无效时会抛错，`tabs.cjs:25` 在已有标签时也拒绝换地址。
- 测试 `test_supervisor.py:530-554`：把 Y 占住（只 bind 不 listen），fake 后端在拿到服务锁之后（`:34`）才 `startup_delay`（`:48`），`startup_timeout=.15`。断言依次是：新 PID 的状态为 degraded，`url is None`；证据仍是旧的 {pid, Y}；之后变 healthy，url 不等于 Y。
- smoke `smoke-home-health-delayed.cjs:16-23` gate 的是 `crew_desktop.serve`。`crew_desktop.py:18-23` 先 `HomeServiceLock.acquire()`，再按模块名调用 `serve`，所以拦截点确实在"拿到锁之后、真实初始化之前"，不是 `_bootstrap`。`:65` 断言 `url===null`，PID 是新的；`:68` 单独注入一次旧 Y，要求 HTTP 被拒、带 warning、`startupError` 保留；`:70-72` 放行后断言新 URL Z 不等于 Y，而且这时才清掉 `startupError` 并重连。

### P2-b 核对
- `supervisor.py:547-552`：没有 web.json 的分支不再看旧 PID，只看 `_free_service_lock()`（`:372-377`）。锁的定义在 `crew/service_lock.py:14-34`，Windows 用 `msvcrt LK_NBLCK`，POSIX 用 `flock LOCK_EX|LOCK_NB`，都是真实的非阻塞内核锁。有 web.json 的分支（`:522-546`）没变，未知或不兼容的服务仍然拒绝。这是有意的 fail-closed，不算缺陷。
- 正向测试 `test_supervisor.py:514-528` 用真实 `crew_supervisor.py` 和真实后端：先正常 shutdown，断言 web.json 已删；再把 `service_pid` 和 `backend_evidence.service_pid` 都改成当前活着的测试进程 PID。之后 `probe_home` 的 `can_start` 为 True，第二个 supervisor 能变 healthy，而且新 PID 不是测试进程的 PID。

### 验收缺口（不是行为缺陷，只是缺测试）
- 任务书说有"无 registry 但锁占用→拒绝"的边界测试，我没找到。`test_supervisor.py` 里的 `HomeServiceLock` 只在 `:19` import 过，测试主体没用；`test_service_lock.py` 和 `test_desktop_lock.py` 也都没调用 `probe_home` 或 supervisor。
- 会出什么问题：如果以后有人把 `:550-551` 改坏，比如退回到看 PID，或者改成锁占用也放行，现在没有任何测试能发现。可能的后果就是 P1 重复后端回归。
- 最小补法（只加一个测试，不动源码）：在 `SupervisorTests` 里新增一条，测试进程自己持有 `HomeServiceLock(self.home)`，并确认 web.json 不存在。然后断言：
  - `probe_home(self.home, timeout=.12)` 返回 `can_start is False`，reason 里包含"后端锁仍被占用"；
  - `self.launch()` 的退出码是 76，`self.launches() == []`；
  - 最后在 finally 里 `release()`。

### 只读边界
- 实际模型是 Opus 5.5（claude-opus-5-5）。全程只用了 Read、Grep、Glob。没有跑任何命令或测试，没有写文件，没有读正式 home 或密钥，没有用 MCP，没有派活，也没有启动服务。上面提到的测试和 smoke 都只读了源码，我没有运行过它们。
- 读的范围只有 `supervisor.py` 里 `_publish`/`_inspect`/`_launch` 及它们直接调用的函数、`main.cjs` 的 admin-status、`core.cjs` 的 `projects`、`tabs.cjs` 的 `reconnect`、`crew_desktop.py`、`service_lock.py`，以及上面两个测试文件。没有重新审整个仓库。
