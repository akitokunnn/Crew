# 第四轮恢复边界的修复前审查

2026-10-24，实际模型 claude-opus-5-5，只读工具 Read/Grep/Glob。此轮确认重复后台的 P1 已修复，又发现迟到启动引用旧 URL 和正常退出后旧 service PID 复用两项 P2。以下保留当时原文；修复和最终验收以 [0.1.1 交付记录](DESKTOP_0.1.1.md) 为准。

## 结论：P1 已修复；P2-a、P2-b 各剩一个 P2，暂不建议合并

本轮只用了 Read、Grep、Glob 读代码。没有跑命令或测试，没有写文件，没有读正式 home 或密钥，也没有启动 Crew。下文提到的测试名是从源码里看到的，我没有运行过。按运行上下文，实际模型是 Opus 5.5。

| 原阻塞 | 结论 |
|---|---|
| P1 重复后端 | **已修复** |
| P2-a 管理员页打不开 | **原问题已修复**；新加的"迟到健康后重连"有一个 P2 |
| P2-b 证据丢失 / PID 误判 | **部分修复**；supervisor PID 和 78 维护这两条已修好，但 service_pid 误判还在，而且现在每次打开都会重现 |

### P1：已修复
- `web.py:1270-1278` 的 `serve` 在 `_serve_locked`（`:1331` 的 `store.connect`）之前先拿 `HomeServiceLock`。生产入口只有 `cli.py:786` 走这条路。`crew_desktop.py:8,18-25` 自己持锁后直接调 `create_server`，同一进程不会重复持锁。
- `_restart`、`_check`、`_recover` 都先调 `_reconcile_registry`（`supervisor.py:391-410`）：web.json 的 URL 变了就探测，兼容则借用，否则设 `_blocked`。
- `_launch` 在 `:455-459` 检查一次登记版本，`:469-474` 在 Popen 前再查一次；中间 `:460` 还要求服务锁空闲。子进程自己也会争锁，抢不到就退出 75。
- 已核对：断路期间换了登记，测试 `test_circuit_new_legacy_registry_is_borrowed_without_spawning_second_backend` 断言只借用、不 spawn。

### P2-a：管理员页能打开了，但迟到重连可能连到旧端口
已修复的部分：
- `core.cjs:226` 受监管启动失败时不再 await stop，也不 kill。
- `main.cjs:289-300` 后端没连上也会建离线壳；`home-ready`（`:224`）不依赖 bootstrap。
- 测试 `core.test.cjs:149-158` 覆盖了这条路径（不 kill，也不写 shutdown）。

**剩余 P2：重连到上一轮的旧 URL，之后再也不会改正**
1. 上次正常退出时，`web.py:1253-1254` 删掉 web.json，`supervisor.py:745-746` 发布 stopped，`backend_evidence` 里的 url 是 Y。
2. 再次打开时没有 web.json，supervisor 用端口 0 拉起后端（`core.cjs:20`），后端初始化超过 30 秒。
3. 桌面在 `core.cjs:177` 超时，进入管理员页，这一步是对的。supervisor 这边到了 startup_deadline，在 `supervisor.py:608-610` 把状态置为 degraded。`_publish` 在 `:324-325` 发现 `self.url` 是 None，就回落到旧证据 url Y，而 service_pid 却是新进程。
4. `main.cjs:216-219` 看到 degraded 加 url，就执行 `tabs.reconnect(Y)` 并清空 `startupError`。
5. 后端随后在新端口 Z 变健康。但 `startupError` 已经清空，不会再重连；工作区列表、`openProject` 一直请求 Y 并失败，只能重启桌面。
- 现有的 `smoke-home-health-delayed.cjs` 是延迟 `_bootstrap`，状态一直停在 starting，所以没覆盖到这条路径。
- **最小修法（两处都改）：**
  - `supervisor.py:324-325`：已有当前进程（`self.service_pid` 非 None）时，url 不再回落到旧证据；旧证据只留在 `backend_evidence` 字段。
  - `main.cjs:216`：先 `await core.projects(recovered)` 成功，再 reconnect 并清 `startupError`。

### P2-b：已修好 supervisor PID 和 78 维护，但 service_pid 误判在每次打开时都会重现
已修复的部分：
- `_publish`（`:321-326`）沿用旧证据，ownership 不会凭证据标成 owned。
- `_inspect` 不再看 `supervisor_pid`；`probe_home`（`:820-836`）用真实的非阻塞锁判断。
- 测试 `test_maintenance_preserves_evidence_and_ignores_unrelated_live_supervisor_pid` 覆盖了"78 → 撤掉维护标记 → 原端口恢复"。
- 借用的后端退出后只上报（`:600`、`:638-640`）；shutdown 通道和实例绑定，借用的后端不会被停掉（`:709-710`、`:733-746`）。

**剩余 P2：正常退出后旧 service_pid 被复用，从此每次打开都退出 76**
1. 正常退出后，web.json 已被删，status.json 里的 `backend_evidence` 仍是 {P, Y}（`:745-746`）。
2. 重启机器或过一段时间，PID P 被某个无关进程复用。
3. 打开 Crew：`_inspect` 走无登记分支 `supervisor.py:544-546`，`_pid_alive(P)` 返回 True，判定不能启动，`run` 在 `:768-773` 退出 76。发布状态时证据原样保留，所以下次打开还是同样结果。`--probe` 也一样返回 `can_start` 为 False。
4. 桌面拿到退出码 76，管理员页显示"系统管理员未运行"，恢复按钮不可用，只能手删 status.json 或者等 P 退出。修复前，下一轮会把证据覆盖成 None，所以能自愈；这次保留证据后反而一直卡住。
- **最小修法：** 没有 web.json 时以 `HomeServiceLock` 为准，因为现在所有生产入口都持这把锁。把 `:544-548` 改为"服务锁空闲就可以启动"，不再拿旧 PID 当拒绝理由。可选再加一层：`_shutdown` 确认自有进程退出后，把证据标记为已退出。
- **验证用例：** 先正常 shutdown，把 status 里的 `service_pid` 改成一个存活的无关 PID，web.json 不存在，再 `run()`，应当正常启动并变健康。

### 只读边界
- 只复核了这三个阻塞点涉及的范围，没有重新审整个仓库，也没有重复上轮已知的建议。
- 测试是抽查读的，`tabs.test.cjs` 和 `test_service_lock.py` 没有逐行读。
