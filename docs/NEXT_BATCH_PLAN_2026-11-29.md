# 2026-11-29 下一批规划书：作战室加固、人格、安卓、尾巴

- 状态：**评审中**。按用户要求不等逐条敲定，评审一轮后直接开工。
- 基线：`main` = `live` = `b5655c8`，桌面版本 `0.4.0`。本批不出包、不发布。
- 用户裁决全文：`C:\Tools\Crew\Temp\batch-1129\方向与裁决.md`（不在仓库内，下文第 1 节摘录要点）。
- 盘点和草案：`Temp/tasks/总盘点_作战室与尾巴_盘点任务书_last.md`、`Temp/tasks/总盘点_人格与安卓_盘点任务书_last.md`，以及四块各自的草案和盘点（`Temp/tasks/作战室_规划草案.md`、`人格_规划草案.md`、`安卓_规划草案.md`、`尾巴_规划草案.md`）。下文 `Q` 号指总盘点，`W/P/A/T` 号指各块草案。

## 1. 用户裁决（2026-11-28，不可推翻）

### 通用

1. 出书后评审一轮就定稿开工，不等用户逐条敲定；需要用户拍板的问题交给领头集中转达。
2. 多用 Codex 和 Gemini；astra、carbon 随便用，完全照规格的死板活给 sol（`-Effort high`）。
3. 收口：各块完成 → 总 leader 合并、集成验收 → 合入本机 live。推送和合 main 等用户指令；本批不出包。

### 作战室

4. **worktree 隔离**：“那就分worktree吧 要记得清理啊”。Git 工作区里参战成员各开独立 worktree 和分支，收工后走 Git 管理员的合并流程；成员收工、作战结束（含放弃、叫停）后自动清掉 worktree 和分支，没合并的先备份再清理。非 git 工作区沿用现状，并明确告诉领头“这里没有隔离”。
5. **叫醒所有参战 leader**：被 @ 到、被派到活的参战 leader，没有在跑的执行时也要能被叫醒并处理消息，领头不用写说明。按房间单独恢复会话、授予收回权限由总 leader 判断，能复用“借主会话”就从简。
6. **守则遵从稳定性**：上批偏差——领头把协调事项写进「分工」；筹备期想发拍板卡；先列目录卡在审批、没先保存草稿。能写进后端状态机的规则写进后端。真 AI 实测用 Codex `gpt-6.1-terra`（low）和 Claude Code Sonnet（low）各跑多次，记发帖量和偏差。
7. **事件触发的看门狗**，替换约 10 分钟的空闲守护，成为唯一的防停滞手段：每结束一个执行判断一次；作战中、目标没完成、领头和参战 leader 都没有在跑的执行、没有在跑的派工、没有待投递消息，全部满足才算停滞；宽限约 1 分钟仍是这样就叫醒领头一次；领头不写说明，“起来就去看为什么卡住了，解决了继续朝 goal 跑”；叫醒后仍没进展，战况栏顶部显示「需要你关注」；成员进度帖不叫醒领头（wake=0），只有完成、卡住、失败才叫醒；内部帖禁止“收到”“在等”这类空话，实测要统计。

### 人格

8. 新手引导加开关“是否想要 AI 慢慢有自己的性格？”，设置里随时能改。
9. 只影响说话方式，以及不妨碍做任务的风格；守则和后台规则高于人格；作战室内部帖子、任务执行不受人格影响。
10. AI 自己随便发挥成长，不用用户确认；每位上限 20k token。
11. 每个 leader 各一份，每个工作区助手各一份；用户可写或选起点，内容可见、可改、可重置。
12. 数据只存本机；改了提示词要真 AI 实测。

### 安卓

13. 做安卓版，借鉴 GitHub 开源，不从零做；GPL/AGPL 只看思路不拷代码。
14. 自写 Kotlin WebView 壳（参照 home-assistant/android）：原生配对页（扫码或 `NsdManager` 发现）→ 拿到凭据 → 加载电脑下发的现有网页前端；原生能力走 JS 桥；不用 Capacitor、TWA。
15. 后端始终在电脑上，复用“选后台”配对和令牌。
16. 语音用 sherpa-onnx 端侧（Silero VAD、流式中文 ASR、中文 TTS，系统 TTS 备选）；朗读时继续听可打断（`VOICE_COMMUNICATION` + `AcousticEchoCanceler`）；只把文字发给电脑；只和工作区助手对话，要派活或改动的仍需屏幕点确认。
17. 通知第一版用局域网长连接加前台服务，不用 FCM。
18. 电脑端补 mDNS 广播、通知事件流、语音文字接口；新依赖写明理由。
19. 构建环境不碰系统级设置；能用模拟器就用模拟器；产物是可安装的 debug APK 加一页安装配对说明，不上架、不签正式证书。
20. 旧语音原型 `frontend/voice.*` 和 `docs/VOICE_FEASIBILITY.md` 由总 leader 定去留，不留两套。

### 尾巴

21. 7 条审美细节；3 个红着的 `test_install_launchers`；手机上架构图能编辑；Codex 侧栏露出的 leader 总对话线程按方案 A 处理；报告出处只链仓库内文件；导图触摸板和真手机能自动测的先测，余下进手测清单。“4 条安装版检查”本批不做。

## 2. 主控裁决

### 2.1 作战室（块：war-room）

| M# | 裁决 | 理由 |
|---|---|---|
| M1 | **隔离单位是“房间 × 参战成员”**：每人一个 worktree 和一个分支，同一成员本房多条分工共用；分工改派后在新负责人的 worktree 里做。领头不开 worktree（领头不接分工，见 M9）。 | 用户裁决 4；W1。分支守卫按 goal 认领分支，一人多分工会冲突。 |
| M2 | 目录 `<工作区根>/.crew/worktrees/room-<房号>-<成员slug>`，分支 `crew/room-<房号>-<成员slug>`。第一次创建时在该仓库的 `.git/info/exclude` 追加 `/.crew/`（只在本机生效、不进提交）。 | W2：目录必须在工作区根下，用量归属、守卫和派工都按“是否在根下”判项目；`spawn` 用的 `Temp/wt` 在用户仓库里不一定被忽略。 |
| M3 | 基点：开了 Git 管理用它的 `integration`，没有就用 `base`；没开 Git 管理用主目录当前 `HEAD`。创建时记下基点提交号。 | W3：与 Git 管理员合并目标一致。 |
| M4 | **何时建**：批准计划的同一事务里，给每位有 goal 的分工负责人插 `war_room_worktrees(state='pending')`；`git worktree add` 在后台循环里、事务外执行，成功 `active`，失败 `failed` 带真实原因。`can_work` 加条件：本人 worktree 是 `active`，或工作区没有隔离（`isolation='none'`）。worktree 没就绪不送输入、不开执行。 | W4：SQLite 回滚撤不回 Git 命令，按表登记可重放、重启可续。 |
| M5 | **执行目录**：绑定房间分工的执行，`goal_runtime.target()` 把 `cwd` 换成本人 worktree，CC 和 Codex 都从这一处取（Codex 二次读取 `execution_session()` 的路径同样走这里）；常驻会话 `cwd` 不动。 | W5；Q1 指出 Codex 有二次读取。 |
| M6 | **合回**：成员收工按守则调用现有 `git.request.add`，显式带 `branch`，由 Git 管理员合并。没开 Git 管理的工作区由领头在主目录合进成员分支。后端不新增合并操作。 | 用户裁决 4；W6。 |
| M7 | **何时清理**：任一条件成立即标“收工”——本人在本房绑定 goal 的分工全部终态；被移出房间；房间关闭（完成、放弃、撤销、叫停、预算结束）。本人在本房没有正在跑的执行后，后台按序处理：(a) 有未提交或未跟踪改动（不含被忽略文件）先做快照提交（临时 index，`write-tree` + `commit-tree`，父为当前 `HEAD`，不动分支和主目录）；(b) 分支末端（有快照看快照）已是合并目标的祖先即算已合并；(c) 已合并删 worktree 和分支；(d) 未合并但有未处理的 `git_merge_requests`：只删 worktree，保留分支，记 `awaiting_merge`，申请处理后合并了删分支、被拒按 (e)；(e) 未合并也没有排队申请：写备份引用 `refs/crew-backup/room-<房号>/<slug>`，删 worktree 和分支，在房间时间线发一条“已备份”公告并附恢复命令。每步可重放，失败记 `error` 并保持当前状态，下轮重试。 | 用户裁决 4“没合并的先备份”；W7。选引用不选 bundle：对象就在原仓库，跨平台；快照能保住未提交改动。 |
| M8 | **非 Git 工作区**：不建表行，房卡 `isolation='none'`；开房和批准时给领头的通知加固定一句“这个工作区不是 Git 仓库，成员共用同一个目录，没有隔离；分工按文件不重叠来切。”战况「参战成员」区显示“没有隔离”。是否 Git 仓库用 `hooks.git_details(root)` 判断；仓库损坏或找不到 git 如实报错，不当成“没有隔离”。 | 用户裁决 4；W8。 |
| M9 | **领头不能当分工负责人**：`war_room_plan.owners` 遇到领头报 `invalid_plan`“第 N 行：分工只写参战成员的活，领头的协调不写进分工”；`set_item` 负责人是领头也拒绝，不写负责人时不再默认成发帖的 AI 自己。 | 用户裁决 6 偏差 1；Q4 指出后台反而放行领头。 |
| M10 | **筹备期不能发拍板卡**：`war_room.decide` 在 `phase='preparing'` 报 `conflict`“筹备期的问题写进计划书「等你定」，批准后再发拍板卡”；`channel.py` 里“当场发拍板卡”的动态提示只在作战中给。旧的筹备期卡片仍可答复、照常送达；`test_war_room_plan.py` 里要求筹备期发卡成功的用例改用作战中的房间。 | 用户裁决 6 偏差 2。Q4：`463e080` 去掉了阶段检查，本批按用户口径恢复。 |
| M11 | **先存草稿**：开房通知（用户开房和 AI 开房都附；AI 开房放在 `war_room.open` 的返回里）由 Crew 附上工作区概况——根目录第一层最多 40 项，Git 仓库再附当前分支和是否有改动——和三节齐全的计划书骨架，第一句写“先用 war_room.plan.update 按骨架保存 v1，再细化。”`draft_version=0` 时 `war_room.consult` 报 `conflict`“先保存一版计划书”。 | 用户裁决 6 偏差 3；W11：Crew 先给目录，模型不用调原生工具卡在审批上。 |
| M12 | **不发空话帖**：AI 发的房帖（`war_room.post`，或 `channel.post` 回复进房）去掉标点和空白后不超过 12 个字、整句落在固定词表里，报 `empty_post`“这条没有新信息，不用发”。词表：收到、好的、好、明白、了解、知道了、在等、等待中、我在等、继续等、稍等、马上、在处理、处理中、OK、ok、👌。用户的帖不拦。拦截次数记进房间事件（`war_room_events.kind='empty_post'`），供实测统计。 | 用户裁决 7；W12。 |
| M13 | **进度不叫醒人**：`war_room_plan.changed` 按事件决定通知——事项改成 `done` 或 `blocked` 给领头一条 `wake=1`；换负责人给新负责人一条 `wake=1`；批准开工照旧给每位有分工的成员发开工通知；其他变动（`doing`/`todo`、改说明、改契约、存草稿）不发通知。失败走 `task_returned`（已是 `wake=1`）。 | 用户裁决 7；Q5/W13：状态板一动就通知全房是发帖量主要来源。 |
| M14 | 守则文字只改作战室那一节（`plugin/prompts/roles/leader.md`、`workspace_assistant.md` 的作战室段落，`git.md` 的房间分支说明），写明：先存草稿；筹备期问题写进「等你定」；内部帖只写结论、证据和阻塞；收工带分支申请合并；被看门狗叫醒后直接去查卡在哪，不写说明。**人格块不改这三份角色文件。** | 后端拦着，提示词只讲原因。 |
| M15 | **统一路由**：新增 `war_room.route(conn, notice) -> 'goal' | 'main' | None`，`deliver` 和主会话认领都只调它：收件人在本房有已批准、绑定 goal、`todo/doing` 的分工 → `'goal'`；否则收件人是领头或仍在房里的成员 → `'main'`，借他的主会话；房间关闭、暂停或收件人已离开 → `None`。`'main'` 不再限于点名和拍板答复，`task_returned`、看门狗通知、M13 的唤醒通知都走这条路。一条通知写过 `war_room_delivery` 或认领过 `batch_id` 后路由固定。 | 用户裁决 5；W15。两条路互斥，避免同一条通知执行两次。 |
| M16 | 判断“有没有可投递的分工”只看 goal 和分工状态，删掉 `claim_main_notices` 里“当前轮有没有执行记录”的条件。 | Q2/W16：已结束的执行记录也会挡住主会话路。 |
| M17 | 借主会话收房内消息的成员（按 M15 一定没有可投递分工）在输入前加一句“要改文件的活，等分工批准后在本房任务里做。”这句由阶段 0 的 `war_room.main_input_note()` 钩子提供。 | Q2 风险：借主会话改文件会绕开隔离。 |
| M18 | 不做“按房间单独恢复会话、授予和收回权限”。 | 用户裁决 5 授权总 leader 判断；M15 已满足需求。 |
| M19 | **整体替换旧守护**：删 `war_room_work.watch`、`end_idle`、`war_room_idle` 表和索引、“空闲十分钟”文案和测试；迁移 `DROP TABLE war_room_idle`，还没发出的旧空闲提醒改 `wake=0`。`can_work` 保留。 | 用户裁决 7“唯一的防停滞手段”。 |
| M20 | **触发**：`ChatJournal.emit` 写完 `turn_result` 后调用 manager 回调，把房号放进内存“待复核”集合（不查库、不发通知）。房号来源：借主会话的回合取事件自带 `room_id`；任务执行用 `goal_id → war_room_items` 查。另两处调同一回调：`goal_runtime.sync` 发现执行离线或终态（补不产生回合事件的退出）；服务启动把所有作战中房间放进集合一次。 | W20；Q3：两载体共用 `turn_result`，不挂 Codex 专属 `close()`。 |
| M21 | **停滞判定** `war_room_watchdog.stalled()`，全部成立才算：① `status='open'`、`phase='active'`、没有预算暂停；② 已批准完成标准没全勾（与 `close` 同一判断）；③ 领头和在房成员的主会话、本房绑定 goal 的执行都不忙（`manager.busy`，CC 外部看 `live`，Codex 看 lease）；④ `usage.dispatch_tasks(project)` 里属于这些会话或本房 goal 的派工没有 `running`（`unknown` 不算在跑）；⑤ 本房没有 `wake=1` 的未发通知（看门狗自己发的不算）；⑥ 没有开着的本房拍板卡，也没有待处理审批（`interaction_requests`）。 | 用户裁决 7 的五条；⑥ 是主控补充：这时等的是用户，界面已显示「等你定」，叫醒领头只添噪音，且只会让停滞判定更严，不违背用户口径。 |
| M22 | **宽限和叫醒**：表 `war_room_watch`。判定停滞且当前没有停滞段就开一段（`stalled_since=now`，`check_at=now+60s`）；到 `check_at` 再判，仍停滞且本段没叫过，就给领头发一条 `wake=1` 通知（按 M15 路由），记 `woke_at`、`notice_id`。每段最多叫一次。文案固定：“作战室 #N 停下了：没人在干活，目标还没完成。去查卡在哪，解决后继续朝目标推进，不用写说明。” | 用户裁决 7。 |
| M23 | **需要你关注**：叫醒后领头那一回合结束时再复核；仍停滞且 `progress_key` 与叫醒时相同，写 `attention_since`。`progress_key` = 所有事项 (id, status, updated_at)、非看门狗帖子最大 id、本房执行个数、`approved_version`。`progress_key` 一变或不再停滞就结束本段并清掉关注。 | 用户裁决 7。 |
| M24 | **房卡字段**：`_card` 顶层加 `isolation`（`'worktree'|'none'`，筹备期就给）和 `watchdog`（`null` 或 `{state:'stalled'|'woken'|'attention', since}`，前端只在 `attention` 显示）；`get` 加 `worktrees`（用户和领头看全部，成员只看自己一行）。前端在现有「需要你关注」区追加“房里停下了：叫醒领头后还是没有进展。”，不伪造卡住事项或拍板卡。 | W24；Q3 指出 `.wr-attention` 现成。 |
| M25 | **作战室界面上的 4 条审美细节**归本块（尾巴盘点 T1 的 1、2、5、6）：目录和详情共用 `roomSubtitle(room)`，去掉目标末尾多余句号，目标为空或去句号后等于房名时只显示“领头 X”；「需要你关注」里能跳转的拍板条目尾部加 `›`，纯文案条目不加；筹备房左右两栏空状态用同一局部类，水平居中、距栏头同一垂直距离（不改 `channel.css`）。 | 这些都在 `frontend/war-room.js|css`，本块同时在改关注区，避免两块撞文件。 |

### 2.2 人格（块：persona）

| M# | 裁决 | 理由 |
|---|---|---|
| M26 | 人格主体 = `rules.skill_role(conn, session)` ∈ {`leader`, `workspace_assistant`}；Git 助手、成员、系统管理员没有人格，接口返回 `persona_not_applicable`。 | 用户裁决 11；P3、P4（Git 助手 `role='leader'` 但 `skill_role` 是 `git`）。 |
| M27 | 按 `session_id` 存一份，挂 `sessions(session_id)`；换载体、丢会话、改名迁目录不搬数据。合并或退役时人格随原 leader 归档保留，拆出的新 leader 从空白起步。永久删除人员时在 `session_ops.delete()` 同一事务里显式删人格和历史；移除工作区只隐藏、停用，恢复后沿用。 | Q4：两条删除入口都保留父记录，单靠 `ON DELETE CASCADE` 不触发。 |
| M28 | **只注入总对话**（`execution is None` 的进程）：CC 在 `chat.py` 启动时把人格块拼进 `--append-system-prompt`；Codex 在 `codex_runtime.py` 仅无 execution 时拼进 `developerInstructions`。目标执行、交付后讨论、成员、Git、频道摘要、房间顾问、运维一律不带。人格不进任何 `BUNDLES`。 | 用户裁决 9；P2：角色包按逻辑 `session_id` 解析，执行也会拿到。 |
| M29 | 运行中人格变了（成长、编辑、开关切换）：沿用守则的“下一次正常输入带新块”机制，但用独立的 `persona_hash` 记在运行实例上，不写 `behavior_prompt_applications`。开关关闭时下一次输入带 `persona_off` 一句。 | 避免把 20k 正文再存一遍（用户裁决 12）。 |
| M30 | 人格块由新守则 `persona_frame` 包裹（可在“行为提示词”里查看和改），写明：只用于直接回复用户；守则、用户当前要求、后台规则优先；作战室帖子、组内消息、任务执行、报告、提交信息、代码与文档一律用守则默认口吻；性格不改变工作方法、判断、工具使用和交付标准。 | 用户裁决 9。 |
| M31 | **每轮标记**：总对话收到的非用户输入（`source` 不是 `human/qq/lark`，或带 `room_id`）在正文前加一行“【本轮不是和用户直接对话：用守则默认口吻，不带个人性格】”。只在该会话当前已注入人格时加。由阶段 0 的 `persona.input_frame()` 钩子提供。 | M18 决定复用借主会话（不拆房内上下文），房内轮次会看到历史里的人格；用标记压住，真 AI 实测量化串味。 |
| M32 | **成长**：宿主后台一次性调用，不在总对话里自改。复用 `channel_digest` 的无工具、不留会话执行方式，模型按 `settings.resolve(home, 'digest')`，不新增用途。触发：全局开启，且自上次成长后 ≥12 条用户亲口私聊，且距上次 ≥6 小时（从未成长只看条数）；全局同时最多 1 个成长作业，攒得最多的先跑；另有“现在成长一次”手动按钮（开关开启且至少 1 条新私聊）。 | 用户裁决 10；成本有界，也供实测。 |
| M33 | **计数口径**：主体总对话收到 `source='human'`、无 `room_id`、无执行、identity 不是宿主合成前缀（清单写死在 `persona.py`）的输入，被原生接收后 `pending_turns += 1`（阶段 0 的 `persona.note_input()` 钩子）。成长读 `HOME/chat/<session_id>.jsonl` 中 `growth_cursor` 之后同口径的用户消息和该轮 AI 最终回复文本，倒序取到 24,000 字符。 | P 草案 M9；Q1：`send()` 这里同时知道来源、执行和房间。 |
| M34 | 成长输出：完整新正文 + 一句话变化摘要（固定两段格式，解析失败算失败）。一个事务里复查开关、资格、`version` 未变（期间用户编辑或重置过就丢弃候选，记“被用户修改覆盖，已放弃”）、长度 ≤ 上限才写新版本（`action='grow'`）。失败保留旧版本，记 `growth_error` 真实原因，不重试，等下次触发。 | 用户裁决 10。 |
| M35 | **20k 的计法**：本地保守估算 `est-v1` = 中日韩字符与全角标点 × 1.2 + 其余非空白字符 ÷ 3，向上取整。编辑、选起点、恢复、成长都用同一函数校验 ≤ 20,000，超限拒绝（`persona_too_long` 带当前估算），不截断。界面显示“篇幅 xx%”。成长提示词带当前篇幅：≥60% 时先合并相近特质、删过时细节压到 ≤50%；平时新正文 ≤80%。 | Q5：仓库没有 tokenizer，不引入新依赖。 |
| M36 | 起点：内置 5 个 `plugin/prompts/personas/<key>.md` + “空白” + “自己写”。选起点覆盖当前内容（`action='seed'`）并记 `seed`；重置回所选起点正文（没选过则清空）。历史每次写入追加，每人保留最近 50 版，可“恢复到此版本”。 | 用户裁决 11。 |
| M37 | 全局开关 `persona_enabled`：库里没记录 = 关（老用户升级不突然多出成本和口吻变化）；新手引导这一步默认选“要”；跳过引导不写。关闭：停注入、停成长、停计数，内容保留，界面写“已暂停，内容保留”。不做每人单独暂停。桌面本机设置（operations worker）和网页 `settings.save` 两条写入口都认这个字段。 | 用户裁决 8；P6。 |
| M38 | 只存本机：人格表不进 `storage_v2` 工作区包和导出。设置说明写一句“性格正文会作为提示词发给所选 AI”。权限：人格读写 scope = `HBD`，按 `session_id` 校验归属与资格；全局开关跟设置走 `HD`。AI 不通过 MCP 改人格（成长由宿主后台做）。 | 用户裁决 12。 |
| M39 | **人格块拥有的共享界面文件**：新手引导 `desktop/src/home-onboarding.js|css`（含尾巴盘点 T1 第 7 条：窄屏引导「开作战室」卡描述加 `text-wrap: pretty`，≤620px 按钮换到下一行，文案不变）、`frontend/settings-ui.js`、`frontend/index.html` 的新脚本登记。人格的窄屏适配写在 `frontend/persona-ui.css` 里，不改 `mobile*.css`。 | 避免与尾巴、安卓撞文件。 |

### 2.3 安卓（块：android）

| M# | 裁决 | 理由 |
|---|---|---|
| M40 | **手机用现有 `browser` 设备身份（Cookie），不新增设备类型。** 原生配对页用 OkHttp 向 `POST /remote/login` 提交 `{kind:"browser",code,device_name}`，带同源 `Origin` 和 `X-Crew: 1`；返回的 `Set-Cookie` 写进 WebView `CookieManager`（`flush()` 后再加载）。原生请求从同一 CookieManager 取 Cookie。一次配对、一个设备记录，电脑“强制下线”同时断开 WebView 和原生。 | 用户裁决 15；Q7：desktop Bearer 令牌不能当 browser Cookie，WebView 子请求也无法统一加头。 |
| M41 | **二维码内容就是现有配对链接** `http://<ip>:<port>/remote/login#code=<8位>`。电脑「局域网访问」生成配对码时每条链接旁画二维码；手机相机直接扫能打开网页登录，安卓壳扫到则自己解析完成原生配对。二维码生成用 `qrcode-generator`（MIT）内置到 `frontend/vendor/`，在 `remote-access-ui.js` 里按需加载，不改 `index.html`。 | Q7：现有链接已含全部信息。 |
| M42 | 始终用数字 IPv4 访问（`Host` 必须等于电脑本机接收连接的 `IPv4:端口`）。 | 安卓盘点 Q3。 |
| M43 | **mDNS 用 python-zeroconf 0.154.0（LGPL-2.1）+ ifaddr（MIT），未修改源码随仓库内置到 `plugin/vendor/`**，附版本、来源、许可证说明，不走 pip，不改打包。只在局域网访问开启时、只在实际绑定的非回环 IPv4 上公告 `_crew._tcp.local.`；停止或换网卡时注销。TXT 只放 `v=1`、`id=<HOME 路径哈希前 16 位>`、`name=<电脑名>`。依赖导入或启动失败时，局域网访问面板如实显示“自动发现不可用：<原因>”，扫码和手填照常。 | 用户裁决 18 已点名 zeroconf，理由：后端无第三方依赖安装链路（Q11），内置源码不改 Windows/Mac 打包；自写 mDNS 要处理冲突探测、多进程共用 5353、goodbye 包，不划算。LGPL 以未修改源码 + 许可证随包分发满足可替换。 |
| M44 | **通知用 SSE 长连接 + 前台服务**：新增手机提醒表 `device_notifications` 和 `GET /api/notifications/events?after=N`（SSE，支持 `Last-Event-ID`）。来源复用 QQ/Lark 提醒的同一套事项（待拍板、人工待办、最终交付、卡住），不受“我已离开”开关限制；按事项 ID 去重；保留 7 天或最近 500 条；无游标首连只发 `ready`，不回灌。 | 用户裁决 17 的本意是“局域网长连接，不用 FCM”。选 SSE 不选 WebSocket：单向推送足够，Crew 已有 SSE 和心跳，Python 标准库没有 WebSocket 服务端，选 WS 要么新依赖要么手写协议。已在给领头的消息里说明。 |
| M45 | **语音复用 `chat.send`**，加可选参数 `input_mode: "text"|"voice"`（默认 text）和 `heard: {reply_id, chars}`（只在 voice 时允许）。阶段 0 把 `input_mode` 传进 `DeliveryMixin.send()` 并写进该输入的 `input_delivery` 事件；安卓块在 `web_api/sessions.py` 的现有 `prefix`/`display_text` 路径里给 voice 输入加一段说明（语音可能有同音错字；回复会被朗读，先说结论、口语、短句，不念代码和链接；有 `heard` 时补“上一条回复用户只听到前 N 字”），界面只显示原话，消息旁显示“语音”标记。 | Q9；不新开语音接口，避免两套。 |
| M46 | **语音话轮里的写操作一律走确认队列**：`ops/executor.py` 的 `caller.is_ai and op.risk == 'high' and not confirmed` 改为 `caller.is_ai and not confirmed and (op.risk == 'high' or (caller.role == 'workspace_assistant' and op.access != 'read' and voice_turn(...)))`；`voice_turn` = 该会话最近一条已接收的用户输入 `input_mode == 'voice'`。走与高风险相同的 `confirmations.request`。手机语音页拉 `interaction.list` 画确认卡，只能点按钮确认；说“确认”不生效。 | 用户裁决 16 写进后端，不靠提示词。 |
| M47 | sherpa-onnx 用 GitHub 官方 AAR v2.0.3（校验 SHA-256），构建脚本下载到 `android/app/libs/`（不进 git）；只打 `arm64-v8a` 和 `x86_64`。 | 安卓调研：Maven Central 无官方包，JitPack 构建失败。 |
| M48 | **模型打包进 APK**（按 `android/assets.lock.json` 的 URL + 字节数 + SHA-256 下载到 assets，不进 git）：ASR `x-asr-160ms-streaming-zipformer-transducer-zh-en-punct-int8`（中英混说、带标点）+ `silero_vad.onnx` + TTS `vits-icefall-zh-aishell3`。debug APK 约 230–260 MB，接受。 | Crew 口语中英混说多；侧载不受商店体积限制；断网和模拟器都能测；国内直连 GitHub/HF 不稳。 |
| M49 | TTS 默认 sherpa AISHELL-3，设置里可切系统 TTS（系统没中文音色时不显示）。不用 matcha-baker、vits-zh-ll、kokoro、piper-phonemize（授权问题）。音质差就把默认改成系统 TTS，列进手测清单。 | 用户裁决 13、16 的许可证红线。 |
| M50 | 打断在端侧做，状态照 OpenLive 四态 `idle → listening → thinking → speaking`（Apache-2.0 只借鉴设计，Kotlin 重写）；朗读中确认是人声（连续 ≥250 ms，朗读期提高门限）立即停播、丢弃未播 PCM、取消后续合成，已播字数随下一句以 `heard` 上送。“停止朗读”不等于“取消助手正在做的事”，后者是单独按钮（复用现有中断操作）。 | 用户裁决 16。 |
| M51 | 录音 `VOICE_COMMUNICATION` + `AcousticEchoCanceler` + `NoiseSuppressor`（逐个 `isAvailable()`，结果写进语音页“诊断”）；只在语音页可见时录音，不做后台录音。 | 用户裁决 16；后台录音要 `microphone` 类前台服务，第一版不需要。 |
| M52 | 通知前台服务类型 `connectedDevice`，声明 `CHANGE_WIFI_MULTICAST_STATE`；常驻通道 LOW，提醒通道 HIGH；断线重连退避 5、10、15、20、30、45、60、120 秒封顶；Android 13+ 单独请求 `POST_NOTIFICATIONS`。 | `dataSync` 在 Android 15 有 6 小时配额；退避照 ntfy（Apache-2.0）。 |
| M53 | 明文 HTTP 只放给已配对主机：debug 构建 base-config 放开明文（局域网 IP 不固定、不支持 IP 段通配），应用层约束——WebView 只允许已配对 `scheme+host+port`，其他链接交系统浏览器；原生 HTTP 只发往已配对主机，禁用重定向跟随。 | 安全边界。 |
| M54 | JS 桥 `window.CrewAndroid`，只注入已配对主机的主 frame（`addWebMessageListener` 限定 origin）；能力：`available()`、`openVoice(project)`、`notificationsEnabled()`/`requestNotifications()`、`switchComputer()`。网页端工作区助手页检测到 `CrewAndroid` 时在输入栏旁显示“语音”按钮。扫码用 CameraX + ZXing core（Apache-2.0），不用依赖 Google Play 的扫描器。 | Q13：不能借 `CrewBrowser` 或伪装 `crewDesktopShell`。 |
| M55 | **旧语音原型废弃**：阶段 0 删除 `frontend/voice.html|js|css`、`web.py` 的三条静态登记、`test_assistant_http.py` 里对应三项；安卓块在 `docs/VOICE_FEASIBILITY.md` 顶部加一句“已由安卓原生语音实现，见 docs/ANDROID.md”，正文保留作选型依据。 | 用户裁决 20：不留两套；原生层才能拿到麦克风。 |
| M56 | 构建环境：复用本机已有的 JDK 21 和 Android SDK（`%LOCALAPPDATA%\Android\Sdk`，已有 android-36 平台、模拟器和 WHPX 加速），**只读使用，不往里装组件**；Gradle 用 wrapper（下载到用户级 `~/.gradle`）；专用 AVD `crew_test_api36`。确需新增 SDK 组件时装到 `C:\Tools\android-sdk` 独立目录。 | 用户裁决 19“不碰系统级设置”；安卓构建环境实测已通过（AGP 9.5.3 / Gradle 9.9.3 / Kotlin 2.4.21）。 |
| M57 | WebView 里的手机适配沿用现有 ≤760px 样式；只修安卓实测发现的问题（软键盘遮挡、返回键、安全区 inset），改动限于 `mobile.css`、`mobile-chat.css` 的相关选择器和壳；`mobile-pages.css` 的 `.sl-categories` 归尾巴块。 | Q13；避免撞文件。 |

### 2.4 尾巴（块：tails）

| M# | 裁决 | 理由 |
|---|---|---|
| M58 | **审美细节 3、4 归本块**：手机会话库分类胶囊复用想法墙 `.nw-filters` 的 `mask-image` + `data-more`，右边还有内容才渐隐（`frontend/session-library.js`、`mobile-pages.css` 的 `.sl-categories`）；空首页「推荐」标签对齐 `.opt .rec`（`--accent-text` / `--accent-soft` 浅底胶囊，不小于 11px×`--fs`），只改 `frontend/workspace-home.css` 的局部选择器。细节 1、2、5、6 见 M25（作战室块），细节 7 见 M39（人格块）。 | 按文件归属拆，避免撞车。 |
| M59 | **3 个 `test_install_launchers` 修测试，不修启动器**：两条帮助用例断言退出 0、`usage: crew`、`--home`、`doctor`，保留含空格/引号/中文的 HOME、PATH 陷阱和不起后台断言；HOME 用例改用 `crew doctor`，两套隔离安装 HOME 各一个项目名再注入一套外部 `CREW_HOME`，断言读到自己那套、都没有 `web.json`/`chat/`。找 bash 改成按 `git.exe` 所在 Git 根找 `bin/bash.exe`（PATH 首项是 `mingw64/bin/git.exe` 时现在会跳过）。 | Q8：CLI 拆分 `3a56f29` 后 `--session` 已删，启动器本身正常。 |
| M60 | **手机宽度架构图可编辑**：去掉 `editable = () => !phone.matches` 的“窄屏 = 只读”；触屏入口照思维导图范式——点选 part 出底部操作栏「加子 part · 改名 · 移动到… · 删除 · 更多」，改名弹单行输入，Leader 节点可「设负责人 / 移出 / 进对话」，连线用「连接」后点第二个节点；单指拖节点移动、空白拖平移、双指缩放（沿用 `canvas-core.js`），不做长按；撤销重做、自动排列、方向放进标题栏「更多」；手机历史抽屉开放回滚（二次确认）；触点 ≥44px，`.arch-pin` 在 760px 下恢复可点。桌面行为不变。 | 用户裁决 21；Q9。 |
| M61 | **Codex 侧栏 leader 总对话线程，补方案 A 的缺口**（主体已在 `7a7a11d` 落地）：Crew 自建的人员总会话在 `idle_reclaim` 之外，`managed_stop`（Crew 退出）和 `user_stop` 也归档；`detach`（载体切换、交接）不归档；`codex_visibility.reconcile_stopped` 加人员总会话候选（离线、不在运行、Crew 自建、`archive_confirmed=0`），复用执行补扫的租约与重查；解档后 `archive_confirmed` 复位；用户接入和来源不明的不碰。`docs/CODEX_INTEGRATION.md` 与实现对齐。只动 `codex_runtime.py` 的关闭/归档段。 | Q10；方案 A 见用户 2026-11-19 调研结论。 |
| M62 | **报告出处写法**：`docs/FEEDBACK_REPORT_2026-11-28.md` 里指向 `Temp/` 和仓库外的链接改成反引号路径文本，仓库内文件保留链接；规则写进 `AGENTS.md`“验证与交付”一句，并同步到 `plan-dispatch` 技能。产品内 `plugin/prompts/final_report.md` 不改。**`AGENTS.md` 本批只归本块改**，其他块需要补的说明写在交付里，由总 leader 合并时补。 | 用户裁决 21；Q11。 |
| M63 | **导图触摸板/触屏能自动测的部分**：`test_canvas_core.js` 补无修饰 wheel 平移、Ctrl+wheel 以指针为锚缩放、原生可滚区绕过、单指空白平移后保存相机、捏合后无幽灵 click 和误提交、失捕获回滚；另用 Electron + CDP（`Input.dispatchTouchEvent`、`synthesizePinchGesture`、带 `ctrlKey` 的 wheel）对导图和架构图各走一遍，脚本放 `Temp/`、结果进交付，不新增冒烟脚本。真机才能测的列进手测清单。 | 用户裁决 21；AGENTS.md 冒烟脚本限 4 个。 |

## 3. 能力和权限

| 能力 | 谁能做 | 校验位置 |
|---|---|---|
| 写计划书分工 | 领头，负责人不能是领头 | `war_room_plan.owners`、`war_room.set_item`（M9） |
| 发拍板卡 | 领头，仅作战中 | `war_room.decide`（M10） |
| 请顾问 | 领头，需已有草稿 | `war_room.consult`（M11） |
| 房帖 | 成员和领头；AI 空话被拒 | `channel.post` 房内分支（M12） |
| 改人格 | 用户（本机、远程桌面、已认证手机浏览器，`HBD`）；AI 只能经宿主成长 | `ops/domains/persona.py` + `persona.py` 资格判定（M26、M38） |
| 改人格总开关 | 用户（`HD`） | `settings.save` 与桌面本机 worker（M37） |
| 语音话轮写操作 | 只能进确认队列，用户点按钮执行 | `ops/executor.py`（M46） |
| 手机读通知流 | 已配对 browser 设备 | `remote_routes.TRANSPORT_GET` + 现有长流复查（M44） |

## 4. 接口约定（冻结）

### 4.1 表结构（阶段 0 提交，`schema.sql` 基础 DDL，`CREATE TABLE IF NOT EXISTS`，新旧库连接即建）

```sql
CREATE TABLE IF NOT EXISTS war_room_worktrees (
    room_id INTEGER NOT NULL REFERENCES war_rooms(id),
    session_id TEXT NOT NULL,
    path TEXT NOT NULL,
    branch TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    base_commit TEXT,
    state TEXT NOT NULL CHECK (state IN ('pending','active','releasing','awaiting_merge','removed','failed')),
    outcome TEXT CHECK (outcome IN ('merged','backed_up')),
    snapshot TEXT,
    backup_ref TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    released_at TEXT,
    cleaned_at TEXT,
    PRIMARY KEY (room_id, session_id));

CREATE TABLE IF NOT EXISTS war_room_watch (
    room_id INTEGER PRIMARY KEY REFERENCES war_rooms(id),
    stalled_since TEXT,
    check_at TEXT,
    woke_at TEXT,
    notice_id INTEGER REFERENCES leader_notices(id),
    progress_key TEXT,
    attention_since TEXT);

CREATE TABLE IF NOT EXISTS device_notifications (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('decision','todo','goal_report','session_error','codex_failed')),
    project TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS persona_settings (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS personas (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    seed TEXT,
    content TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    tokens_estimate INTEGER NOT NULL DEFAULT 0,
    pending_turns INTEGER NOT NULL DEFAULT 0,
    growth_cursor TEXT,
    last_growth_at TEXT,
    last_growth_summary TEXT,
    growth_error TEXT,
    updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS persona_history (
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    tokens_estimate INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('seed','edit','grow','reset','restore')),
    actor TEXT NOT NULL,
    summary TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, version));
```

- `war_room_idle` 的删除和旧提醒改 `wake=0` 由作战室块在 `schema.sql`/`store.py` 里做（M19），其余块不再改这两个文件；需要新列时先找总 leader 改契约。

### 4.2 阶段 0 钩子（签名冻结，阶段 0 为空实现）

| 位置 | 签名 | 阶段 0 行为 | 归属 |
|---|---|---|---|
| `chat_delivery.DeliveryMixin.send()` | 新增参数 `input_mode='text'`（`'text'|'voice'`，其他值报错） | voice 时写进 `input_delivery` submitted 事件的 `input_mode` 字段 | 安卓读取 |
| 同上，输入投递前 | `war_room.main_input_note(conn, session_id, source, room_id) -> str|None` | 返回 None | 作战室实现（M17） |
| 同上，输入投递前 | `persona.input_frame(conn, session_id, source, room_id) -> str|None` | 返回 None | 人格实现（M31） |
| 同上，原生接收后 | `persona.note_input(conn, session_id, source, room_id, identity) -> None` | 无操作 | 人格实现（M33） |
| `chat.send` 操作参数 | `input_mode`（enum `text`/`voice`）、`heard`（`{reply_id:string, chars:integer≥0}`） | `input_mode` 传进 `send()`；`heard` 只校验形状（非 voice 带 `heard` 报 `invalid_arguments`），不使用 | 安卓实现前缀 |
| `web_service/workers.py` `deliver_notices()` | `war_room_worktree.sweep(manager, conn, now)`、`war_room_watchdog.tick(manager, conn, live, now)` | 无操作 | 作战室实现 |
| `web_service/workers.py` `_poll_notices()` | `persona_growth.tick(manager, conn, now)` | 无操作 | 人格实现 |

非用户输入（`source` 不在 `human/qq/lark`，或带 `room_id`）走钩子时，`send()` 把各钩子返回的非空行按“作战室提示、人格标记”的顺序加在正文前，`display_text` 保留原话。

### 4.3 作战室

- 房卡 `_card`：新增 `isolation: 'worktree'|'none'`、`watchdog: null | {state, since}`；`get` 新增 `worktrees: [{member, path, branch, state, outcome, backup_ref, error}]`（M24）。
- 错误码：新增 `empty_post`；沿用 `invalid_plan`、`conflict`。不新增操作名。
- 内部函数（块内，列出供对齐）：`war_room.route(conn, notice) -> 'goal'|'main'|None`；`war_room_worktree.ensure / release / sweep / path_for(conn, execution) -> str|None`；`war_room_watchdog.note_turn(room_id) / tick(...)`。

### 4.4 人格

全部走 `/api/op/<name>`，注册在新文件 `plugin/crew/ops/domains/persona.py`（人格块新建，同时在 `ops/domains/__init__.py` 登记导入）。

| 操作 | 参数 | 返回 / 错误 |
|---|---|---|
| `persona.get` | `{session_id}` | `Persona` |
| `persona.save` | `{session_id, content, expected_version}` | `Persona`；`conflict`、`persona_too_long`、`persona_not_applicable` |
| `persona.seed` | `{session_id, seed, expected_version}`，`seed` ∈ 起点 key 或 `blank` | 同上 |
| `persona.reset` | `{session_id, expected_version}` | 同上 |
| `persona.restore` | `{session_id, version, expected_version}` | 同上 |
| `persona.history` | `{session_id, before?, limit?(1–50)}` | `{history:[{version, action, actor, summary, tokens_estimate, created_at, content}], next_before}` |
| `persona.grow` | `{session_id}` | `{started:true}`；`persona_disabled`、`persona_nothing_new`、`persona_busy` |
| `persona.list` | `{project?}` | `{enabled, items:[{session_id, name, project, subject, version, percent, last_growth_at, growth_error}]}` |
| `settings.save` / `settings.get` | 新增字段 `persona_enabled: bool` | 现有返回 |

`Persona` = `{session_id, subject:'leader'|'workspace_assistant', enabled, seed, content, version, tokens_estimate, limit:20000, percent, estimate:'est-v1', last_growth_at, last_growth_summary, growth_error, growing, pending_turns, presets:[{key,name,summary}]}`。写入后 `change_feed.touch(conn, 'persona', project, session_id)`。新守则 key `persona_frame`、`persona_growth`、`persona_off` 加进 `behavior_prompts.SPECS`，不进任何 `BUNDLES`。

### 4.5 安卓与电脑端

- `GET /api/access/context` 远程分支新增 `host_name`、`instance_id`（同 mDNS TXT `id`）。
- mDNS：服务类型 `_crew._tcp.local.`，实例名 `<电脑名> Crew`，SRV 端口为远程监听实际端口，A 记录为实际绑定 IPv4（`0.0.0.0` 时为每个可用非回环网卡），TXT `v=1`、`id`、`name`（≤63 字节）。`remote_access.get` 快照新增 `discovery_advertising: {active, error}`。
- 通知流 `GET /api/notifications/events?after=<seq>`：加进 `remote_routes.TRANSPORT_GET` 的 browser 和 desktop，本机也可用。帧 `id: <seq>` + `data: {"seq","type":"notification","item_id","kind","project","title","body","url","created_at"}`；无游标首连 `{"type":"ready","seq":<最大 seq>}`；`after` 小于保留最小 seq 时先发 `{"type":"gap","oldest":N}` 再补发；空闲 15 秒心跳；设备撤销或远程关闭时断流。
- `chat.send` 的 `input_mode`、`heard` 见 4.2；`voice_turn` 见 M46。
- 安卓内部数据（已配对电脑、游标、语音会话）由安卓块自定，不影响他块。

## 5. 数据迁移

- 新表都是 `CREATE TABLE IF NOT EXISTS`，旧库下次连接即建，无回填。
- `war_room_idle`：作战室块在 `store.connect()` 迁移入口里先把引用它的未发提醒 `UPDATE leader_notices SET wake=0 WHERE id IN (SELECT notice_id FROM war_room_idle WHERE notice_id IS NOT NULL) AND sent_at IS NULL`，再 `DROP TABLE`。可重复执行。
- 人格：老库没有 `persona_settings` 行 = 关；没有 `personas` 行 = 空白。
- 要验证的库形态：旧结构测试库（参照 `test_war_room_plan.py` 制造旧库并重复打开）、空库、正式库只读副本（只 `store.connect`，不起后台）。

## 6. 施工拆分

阶段 0 由总 leader 提交到集成分支 `claude/batch-1129-259f96`，内容：4.1 六张表；4.2 全部钩子和空模块（`war_room_worktree.py`、`war_room_watchdog.py`、`persona.py`、`persona_growth.py`）；`chat.send` 两个参数；M55 删除旧语音原型；`web.py` 登记 `persona-ui.js|css` 并建空文件；本规划书。各块把阶段 0 提交合进自己的分支后开工。

| 块 | M 条 | 模型 | 拥有的文件（只改这些；跨块文件见下行） |
|---|---|---|---|
| 作战室 war-room | M1–M25 | astra（路由、worktree、看门狗）、sol（守则后端）、Gemini carbon（界面） | `war_room*.py`、新 `war_room_worktree.py`/`war_room_watchdog.py` 的实现、`goal_runtime.py` 的 `target()` 和 `sync` 回调、`chat_journal.py` 的回合钩子、`channel.py` 房内分支、`chat_delivery.py` 的主会话认领段（约 `:270–330`）、`goals.py` 终态调 `release`、`web_service/workers.py` 的 `deliver_notices()`、`schema.sql`/`store.py` 删 `war_room_idle`、`prompts/roles/leader.md`/`workspace_assistant.md`/`git.md`、`frontend/war-room.js|css`、相关测试 |
| 人格 persona | M26–M39 | astra（注入、成长）、sol（存储接口）、Gemini carbon（界面） | 新 `persona.py`/`persona_growth.py` 实现、新 `ops/domains/persona.py` 和 `domains/__init__.py` 导入、`settings.py`、`web_api/admin.py`、`ops/domains/admin.py`、`operations_chat.py`、`session_ops.py`、`behavior_prompts.py` 的 SPECS、`chat.py` 启动拼接、`codex_runtime.py` 的 `developerInstructions` 段（约 `:262–290`）、`chat_delivery.py` 的 `_send` 守则重发段（约 `:81–97`）、`web_service/workers.py` 的 `_poll_notices()`、`plugin/prompts/persona_*.md` 和 `personas/`、`frontend/persona-ui.*`、`member-ui.js`、`settings-ui.js`、`index.html`、`assistant.js`/`assistant.html` 的页头入口、`desktop/src/home-onboarding.*`、相关测试 |
| 安卓 android | M40–M57 | astra（语音、确认判定复核）、sol（电脑端接口、壳、通知）、Gemini carbon（界面） | `android/` 全部、`docs/ANDROID.md`、`docs/VOICE_FEASIBILITY.md`、新 `discovery.py`/`device_notifications.py`、`plugin/vendor/`、`frontend/vendor/qrcode-generator*`、`remote_access.py`、`remote_routes.py`、`notifications.py`、`web.py` 的通知流路由、`web_api/sessions.py` 的语音前缀、`ops/executor.py`、`ops/confirmations.py`、`frontend/remote-access-ui.*`、`assistant.js` 的输入栏语音按钮、`chat.js` 的语音标记、`mobile.css`/`mobile-chat.css` 相关选择器、相关测试 |
| 尾巴 tails | M58–M63 | astra（架构图触屏、线程归档）、sol（测试、文档）、Gemini carbon（审美细节） | `frontend/session-library.js`、`mobile-pages.css` 的 `.sl-categories`、`workspace-home.css` 的推荐标签、`plugin/tests/test_install_launchers.py`、`frontend/architecture.*`、`canvas-core.js`、`codex_runtime.py` 的关闭/归档段（约 `:740–780`）、`codex_visibility.py`、`native_origins.py`、`docs/CODEX_INTEGRATION.md`、`docs/FEEDBACK_REPORT_2026-11-28.md`、`AGENTS.md`、相关测试 |

- **同一文件分段归属**：`chat_delivery.py`（作战室：主会话认领段；人格：`_send` 守则重发段；钩子已在阶段 0 落好）、`codex_runtime.py`（人格：启动参数段；尾巴：关闭归档段）、`web_service/workers.py`（作战室：`deliver_notices()`；人格：`_poll_notices()`）、`frontend/assistant.js`（人格：页头入口；安卓：输入栏语音按钮）。各块只改自己的段，合并冲突由总 leader 按意图解决。
- `plugin/tests/ci_core.txt` 各块都可追加新模块，合并时总 leader 合并两边。
- 跨块契约要变：找总 leader 改本规划书，再通知受影响的块。
- 块内测试只跑相关模块；全量测试、完整截图矩阵、作战室和人格的多轮真 AI 实测放在集成验收。块内真 AI 实测只在本块就是 AI 调用链本身时做：作战室（守则与看门狗各一轮冒烟）、人格（注入和成长各一轮）、安卓（语音链路）、尾巴（线程归档）。

## 7. 验收

| 项 | 判定 |
|---|---|
| worktree 隔离 | Git 工作区批准计划后，每位有分工的成员有独立 worktree 和分支，执行 cwd 是它；非 Git 工作区领头收到“没有隔离”，房卡 `isolation='none'`。 |
| 收工清理 | 已合并 → worktree 和分支都删；有排队申请 → 只删 worktree 记 `awaiting_merge`；未合并无申请 → 有备份引用、时间线有公告和恢复命令；有未提交改动的快照被备份引用指到；房间放弃、叫停也走同一流程；重启后能续。 |
| 叫醒参战 leader | 无分工、无执行的成员被 @ 后经主会话收到并回帖；`task_returned` 和看门狗通知也能送到无执行的领头；同一通知不会既走任务又走主会话。CC 和 Codex 各一遍。 |
| 守则后端 | 分工写领头、筹备期发卡、无草稿请顾问、空话帖各被拒并给出原因；进度改 `doing` 不发通知，`done/blocked` 只叫醒领头。 |
| 看门狗 | 五条加等用户条件全部满足 60 秒后叫醒领头一次且只一次；有派工在跑、有待投递、有人在忙、等用户时都不叫；叫醒后仍无进展显示「需要你关注」，有进展后消失；旧 `war_room_idle` 已删，旧未发提醒不再唤醒。 |
| 真 AI · 作战室 | Codex `gpt-6.1-terra` low 与 Claude Code Sonnet low 各跑至少 3 次完整流程（开房 → 写计划 → 批准 → 两成员各改一处 → 申请合并 → 关房）：统计每人发帖数、空话被拦次数、三类偏差尝试次数、看门狗叫醒次数、worktree 是否清干净、备份引用是否正确。 |
| 人格 | 引导开关和设置两条写入口都生效；leader A、B、工作区助手各一份不串；总对话启动参数含人格块，执行、顾问、成员不含（测试断言）；运行中编辑下一次输入带一次新块；超 20k 估算的各种写入被拒；成长期间用户编辑丢弃候选；历史 50 版与恢复可用；人格不进工作区包导出。 |
| 真 AI · 人格 | 同一问题两种起点风格可辨；同一小任务开关人格结果一致、测试都通过；开房后领头房内帖不带性格口吻（人工判读并记录）；手动成长一次，新正文 ≤ 上限、摘要合理。CC 与 Codex 各一遍。 |
| 安卓 · 配对 | 电脑配对码旁有二维码；手机相机扫开网页登录自动填码；安卓壳扫码直接进入工作台，设备列表出现该手机；强制下线后 WebView 和通知流都回配对页。 |
| 安卓 · 通知 | 待拍板、人工待办、最终交付、卡住四类在手机后台收到系统通知，点开进对应页；“我已离开”关着也收；断网 2 分钟再连补齐不重复；mDNS 在本机能被发现，关局域网访问后注销。 |
| 安卓 · 语音 | 助手页只在安卓壳内出现“语音”按钮；说话 → 字幕 → 朗读；朗读中开口立即停播，下一句助手知道只听到一半；语音里派活出确认卡、点确认才执行、说“确认”不生效；聊天记录里有“语音”标记、显示原话；断网可装可用；APK 路径、大小、SHA-256 写进交付。 |
| 尾巴 | 4 条审美细节在浅色宽屏和深色 375px 截图里看得出改了；3 个启动器用例在 Windows 跑通；375px 架构图能加 part、改名、移动、连线、回滚；Crew 退出和用户停止后 leader 总对话线程从 Codex 默认列表消失、续用前解档同 ID；`FEEDBACK_REPORT` 无 `Temp/` 链接；画布单测和 CDP 实测数字进交付。 |
| 集成 | 全量测试（Python、网页、桌面）与上批基线比无新增失败；4 个冒烟按改动范围跑；改到的界面浅色宽屏 + 深色约 375px 截图，布局随宽度变化的加 760px，交 Gemini 挑刺一轮。 |
