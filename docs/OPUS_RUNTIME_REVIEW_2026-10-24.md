# Claude Opus 5.5 第二轮只读审查原文

下面是修复前的意见。两项 P2 均已由主控抽查、修复并通过针对性回归；完整交付状态以 [0.1.1 记录](DESKTOP_0.1.1.md) 为准。

## 结论：无 P1，有 2 个 P2，可以合并上线，建议先修这 2 个 P2

我是 Claude Opus 5.5（claude-opus-5-5），这一点来自运行上下文。我只用了 Read/Grep/Glob：没有跑命令、没有跑测试、没有写文件。下面引用的测试数和真实往返结果都是主控提供的，我没有复跑。

你要我特别找的五类问题，除下面 P2-1 那条 CLI 越权外，都没有找到：
- **跨组泄漏**：在信任上下文、报告范围、拍板归属、通知路由和 Codex 的 `crew_answer` 里都没有发现。
- **配置后静默唤醒**：没有。`trust_mode.set` 和 `runtime_switch.switch` 只写库和审计；`switch` 只发一条 journal 事件，不发通知、不发消息、不启动进程。
- **丢失交接或历史**：没有。失败时交接记录保留，下次重新附上；各运行端的 native 绑定分开保存。
- **活进程被误切换**：没有。外部的 Claude 进程和 Codex 租约都会挡住切换；确认时会再检查一次进程已退出、配置没被改过。
- **数据库死锁**：没有。锁的顺序一律是 write_lock→scan_lock、write_lock→chat.lock，没有反向；连接是 autocommit（`store.py:23 isolation_level=None`），`state()` 等 scan_lock 时不持有事务；hook 进程不会回调 web HTTP。

---

## P2-1　CLI `crew answer` 不校验拍板归属，任何绑定 AI 都能答别组的题

- `plugin/crew/cli.py:563-564`
  ```
  if cmd == "answer":
      return board.answer(conn, args.id, args.text, actor)
  ```
  `board.answer`（`board.py:787-793`）只检查题目存在且还没答复，不看是谁在答。
- **对照**：Codex 原生工具会校验，`codex_tools.py:334-335` 写着 `... or decision['leader_session_id'] != sid): raise ToolError("forbidden", ...)`；`decision_briefs._authorized`（`:134`）也按 `decision_leader` 校验。只有 CLI 这条路没校验。
- **触发**：
  - Claude leader 按守则（`skills/leader/SKILL.md:46,51`）用 `crew answer <编号>` 答题。编号写错，或者故意填别组的编号，A 组 leader 就能答掉 B 组的拍板。
  - 成员也能答。
  - Codex 会话通过 shell 调 `crew answer` 也走这条路；codex_guard 共用的黑名单里没有 answer，所以绕得过 `crew_answer` 的检查。
- **影响**：B 组没开信任、本该等用户拍板的题，可能被别组的 AI 答掉。这让新加的“历史拍板归属固定在创建时 leader、原 leader 失效后暂停授权”失效，属于跨组越权。这个缺口很可能早就存在，但它削弱的是这轮新加的契约。
- **最小修法**：在 `cmd == "answer"` 分支里，当 `actor != board.HUMAN` 时做和 codex_tools 一样的检查：actor 必须是 leader、未归档、所在组仍开着，并且 `trust_mode.decision_leader(conn, args.id) == actor`，否则抛 `BoardError`。人类答题不受影响。
- **验证**：在 `test_leader_trust_cli.py` 加 3 个用例：
  - 别组 leader `answer` 应该失败；
  - 本组成员 `answer` 应该失败；
  - 本组 leader 和人类 `answer` 应该成功。
  - 再加一个：成员换组后，旧题仍只能由原 leader 答。

## P2-2　卡片显示“可更换”，但 prepare 用数据库里的状态，会拒绝已经空闲的 Claude 成员

- `plugin/crew/runtime_switch.py:64`
  ```
  if session['status']=='working' or manager.busy(sid):return '成员正在工作，请等这一轮结束'
  ```
  `prepare`/`switch` 拿到的 session 来自 `board.session(conn, ...)`，也就是数据库里的状态（`chat.py:634,638`）。
- web 状态接口在 `web.py:411-414` 先用 journal 状态覆盖了 `session['status']`，再拿去调 `runtime_switch.state(conn,session,...)`（`:464`）。所以卡片上显示的是“可更换”。
- 后台托管的 Claude，数据库里的 working→idle 只靠 Stop hook 回写（`board.py:945`）；journal 回到 idle 不会同步到数据库。
- **触发**：Claude 托管成员这一轮因为 API 错误（比如额度用完）或被用户中断而结束，进程还活着并且空闲。按 Claude Code 的 hook 语义，这两种情况走 StopFailure 或不触发 Stop，仓库里也没有处理 StopFailure 的代码（grep 结果为 0）。这一条依据的是 Claude 的外部行为，需要实机确认。
- **影响**：恰好落在“额度用完就换 Codex”这个主场景。卡片显示能换，点了却报“正在工作”，而且会一直这样，直到进程退出、SessionEnd 写成 offline。不会丢数据，属于假阻塞。
- **最小修法**（二选一）：
  - 进程归 Crew 托管且正在运行时，`basic_reason` 以 `manager.busy()` 为准，只有在没被 Crew 托管时才看数据库状态；
  - 或者 `state()` 也用数据库状态，保证显示和后端一致。
- **验证**：在 `test_runtime_switch.py` 加用例：托管进程存活、journal 为 idle、数据库为 working 时，prepare 应该允许（或者 state 同样显示不可用）。真实环境里，用 Claude 中断一轮之后试一次切换。

---

## 用户点名的 4 项核对结果

1. **信任模式按 leader 生效**：通过。
   - 设置以 `leader_trust_settings` 的 leader 为主键。`context()` 关闭时也返回显式的“关闭”文本（`trust_mode.py:116,125`）。
   - Codex 每一轮都会注入（`codex_runtime.py:390-392`）；Claude 在 UserPromptSubmit 和 SessionStart(resume/compact) 时注入（`hooks.py:123-129`）。
   - 旧 project 配置的迁移由 `trust_leader_migrations` 保证只执行一次（`schema.sql:612-617`）。
   - 拍板归属在 ask 时写入 `decision_leader_scopes`；全仓只有 `board.py:738` 一个插入点。
   - `auto_answer` 有 `_leader_active` 门槛。CLI 必须指定 `--leader`，AI 只能操作自己组。
2. **成员 CC↔Codex 切换**：通过，只有 P2-2。
   - native ID 分运行端保存；Claude 的 native ID 若不等于 Crew ID 会拒绝（`chat.py:528`）。
   - 预览有 300 秒时效，并用 revision 和 configuration 两次比对。
   - 检查顺序是：先 detach，再看 `poll()`，再在事务内复核配置（`runtime_switch.py:194-204`）。
   - Claude 只在用户消息 uuid 回显完全一致时才 ACK（`chat.py:418-427`）；Codex 在 `start_turn` 返回后才消费交接。
   - journal 里只记原文（`chat.py:622`、`codex_runtime.py:383`）。
   - 已切走的 Claude hook、Codex 工具和 guard 都会拒绝旧载体的迟到写入。
3. **第一轮 P2 修复**：
   - 更新器：Python 端 `timeout=None`（`tool_updates.py:400`），Electron 端 `timeout:0`（`tool-updates.cjs:59`），并补了检测不到版本时的修复提示。已修对。
   - VS Code 扫描：按项目沿用上次结果（`web.py:144-147`），按文件沿用上次结果（`vscode_sessions.py:221-227`）。已修对。
   - 文档归属：历史引用不再改写别人的卡片（`documents.py:143-148`）。已修对。
4. **usage 扫描事务**：没有发现问题。
   - `_transaction` 在已有外层事务时只用 SAVEPOINT，不会篡夺外层事务（`usage.py:36-48`）；出错时整体回滚。
   - BEGIN IMMEDIATE 只比原来多占了“第一次写之前的读阶段”，没有引入 Python 锁和 SQLite 锁互等。

## 建议（偏好或体验问题，不算 bug）

- **最近指令会显示交接头**：`sessions.last_prompt` 和 hook 事件存的是带交接前缀的文本（`codex_runtime.py:397 text[:200]`；Claude 走 `board.py:959`）。切换后的第一轮，“最近指令”会显示“【Crew 受信交接上下文】…”。建议改存 display_text。
- **切换时长时间占用写锁**：`/api/session/runtime/switch` 在持有 write_lock 和 chat.lock 时 detach，最长可能等约 20 秒，期间其它网页写操作都会排队。可以考虑只在 chat.lock 内 detach。
- **手动登记仍可接管别人的卡片**：`documents.register` 用 manual 来源时仍会改写别人卡片的作者、分组和标题（`:157`）。如果这不是有意为之，建议加归属校验。
- **退出时刻的假冲突**：Codex 的 `reader_done` 先于进程真正退出时，`switch` 会报“旧载体进程尚未真正退出”。结果是安全的（需要重新预览），但用户可能会误解。

## 只读覆盖范围

- **重点读过**：`runtime_switch.py` 全文；`chat.py` 340-1015；`codex_runtime.py` 60-509；`trust_mode.py` 全文；`runtimes.py`；`models.py` 的 set；`schema.sql` 迁移部分；`web.py` 的锁、状态接口、切换/信任 API 和 `scan_usage`；`usage.py` 的 scan/_transaction；`hooks.py` 45-155；`cli.py` 的 trust/answer/actor；`decision_briefs` 的授权部分；`notifications` 的路由；`codex_tools` 的 answer；`tool_updates`、`tool-updates.cjs`、`vscode_sessions.SessionList`、`documents.register`；`runtime-switch-ui.js` 的关键路径；`leader-trust-ui.js`；`WORKFLOWS.md` 57-82。
- **没细读**：`app.js`、`index.html`、CSS、smoke 脚本，大部分测试正文，README、CODEX_INTEGRATION、WEB_UX，`documents` 的 history/refresh，`spawn.leader_plan` 的界面必选逻辑（只确认了 CLI 默认 claude、成员默认继承 leader），以及外部 Codex 会话检测（已知限制，见下）。
- **没读的证据**：`real-runtime-roundtrip-result.json` 和各个 `evidence.json` 我都没读，相关结论全部来自主控转述。
- **已知限制**（不算本轮 bug）：在 Crew 之外打开的 Codex 线程无法检测；`--session` 参数可以冒充身份，这是现有身份模型本身的边界。

## 必须保留的 3 个检查

1. `runtime_switch.switch` 里“detach 后 `poll()` 为 None 就拒绝”，以及事务内 `configuration()==expected` 的复核（`:194-204`）。
2. 交接只在“Claude 回显 uuid 完全一致”或“Codex `start_turn` 返回”时才消费，失败时保留待交接记录（`chat.py:418-427,609-616`）。
3. `trust_mode.context` 关闭时返回显式文本，Codex 每一轮注入；`auto_answer` 保留 `_leader_active` 门槛（`trust_mode.py:116-127,168-170`）。
