# 2026-11-29 下一批规划书：作战室加固、人格、安卓、尾巴

- 状态：**定稿**。astra（后端、权限、迁移、契约）与 Gemini carbon（界面、人格体验、语音、裁决核对）各评审一轮，采纳的结论已原地改进正文，驳回的在理由栏写明原因。按用户要求不等逐条敲定，直接开工。评审原文：`Temp/tasks/规划书评审_任务书_last.md`、`Temp/tasks/规划书评审_carbon.md`。
- 基线：`main` = `live` = `b5655c8`，桌面版本 `0.4.0`。本批合进 main，不出包、不发布。
- 用户裁决全文：`C:\Tools\Crew\Temp\batch-1129\方向与裁决.md`（不在仓库内，下文第 1 节摘录要点）。
- 盘点和草案：`Temp/tasks/总盘点_作战室与尾巴_盘点任务书_last.md`、`Temp/tasks/总盘点_人格与安卓_盘点任务书_last.md`，以及四块各自的草案和盘点（`Temp/tasks/作战室_规划草案.md`、`人格_规划草案.md`、`安卓_规划草案.md`、`尾巴_规划草案.md`）。下文 `Q` 号指总盘点，`W/P/A/T` 号指各块草案。

## 1. 用户裁决（2026-11-28，不可推翻）

### 通用

1. 出书后评审一轮就定稿开工，不等用户逐条敲定；需要用户拍板的问题交给领头集中转达。
2. 多用 Codex 和 Gemini；astra、carbon 随便用，完全照规格的死板活给 sol（`-Effort high`）。
3. 收口：各块完成 → 总 leader 合并、集成验收 → 合入本机 live → 接着合进 main（用户 2026-11-28 经领头转达，原话“也合进 main”，覆盖裁决全文 §0 的“等指令”）：`python scripts/live.py push`，有平台差异的改动按需 `gh workflow run tests.yml --ref live`，再 `python scripts/live.py ship`，批次 PR 的 CI 全绿后合并。不出包、不改版本号。

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
17. 通知第一版用局域网 WebSocket 加前台服务（参考 HA、ntfy-android），不用 FCM。传输方式后由补充裁决 22 改为 SSE。
18. 电脑端补 mDNS 广播、通知事件流、语音文字接口；新依赖写明理由。
19. 构建环境不碰系统级设置；能用模拟器就用模拟器；产物是可安装的 debug APK 加一页安装配对说明，不上架、不签正式证书。
20. 旧语音原型 `frontend/voice.*` 和 `docs/VOICE_FEASIBILITY.md` 由总 leader 定去留，不留两套。

### 尾巴

21. 7 条审美细节；3 个红着的 `test_install_launchers`；手机上架构图能编辑；Codex 侧栏露出的 leader 总对话线程按方案 A 处理；报告出处只链仓库内文件；导图触摸板和真手机能自动测的先测，余下进手测清单。“4 条安装版检查”本批不做。

### 补充

24. **Gemini 停用**（2026-11-28，经领头转达：Gemini 额度用完）：之后所有派工都用 Codex。原来给 Gemini 的视觉、设计、审美类活改派 `gpt-6.2-astra`，照规格的照旧给 sol high；截图评审由 astra 看图（`codex exec -i` 传图），或由总 leader 自己看截图；在跑的 Gemini 任务失败不重试，直接改派 Codex。本条修订裁决 2 里的 Gemini 部分。
25. **设计、视觉、审美、看截图挑刺可改派 Claude Code CLI 的 Opus**（2026-11-28，经领头转达）：用 CLI 自己的登录和额度（约 100 美元，不是会话额度），派法 `run_claude.ps1 -Model opus`；总 leader 的集成验收截图评审最多 30 美元，四块各约 15 美元；每次把结果里的 `total_cost_usd` 记进交付，报告里汇总。照规格的活仍给 Codex sol，难点给 astra。

23. **worktree 由领头按成员决定**（2026-11-28，经领头转达，原话“我的库很大复制一份20g 所以不要代码上限制crew一定开worktree 交给leader自己判断比较好”）：Crew 不在代码里默认或强制参战成员开 worktree，由作战室领头按成员逐个决定。开了的照原方案：独立分支、合并、自动清理。不开的共用目录：文件范围不重叠、只提交自己的文件、收工时做越界检查，界面上标明“未隔离”。本条修订裁决 4 的“默认各开”。

22. （2026-11-28 补充，用户经领头确认无异议）手机通知改用 SSE 长连接（单向推送足够，标准库无 WebSocket 服务端），仍是局域网 + 前台服务、不用 FCM；mDNS 用内置源码的 python-zeroconf + ifaddr；debug APK 约 230–260 MB、模型打进 APK；复用本机已有的 Android SDK 和 JDK，只读使用。见 M43、M44、M48、M56。

26. **提醒默认渠道改 Lark**（2026-11-28，经领头转达）：Lark 和 QQ 提醒的默认渠道改为 Lark；用户已经手动选过的不变。归尾巴块。
27. **“离开电脑”多一个判定**（2026-11-28，经领头转达）：在原来的手动开关和锁屏之外，加上“电脑 1 小时内没有任何键鼠输入”，Windows 和 Mac 都要支持。归尾巴块。

## 2. 主控裁决

### 2.1 作战室（块：war-room）

| M# | 裁决 | 理由 |
|---|---|---|
| M1 | **隔离单位是“房间 × 参战成员”，开不开由领头按成员决定**（用户裁决 23）：计划书「分工」条目写法 `- [ID] 负责人 | 事项 | 文件: a/, b.py | 独立目录`：每条都必须写「文件:」（相对工作区根，目录以 `/` 结尾）；末尾写「独立目录」的成员开一个 worktree 和分支，本房他的所有分工共用；没写的成员共用工作区目录（见 M8a）。同一成员的几条分工必须一致（`invalid_plan`，指出行号）。解析结果放进 `war_room_plans.entries_json` 的 `paths`、`worktree` 字段，不改表。非 Git 工作区标了也报 `invalid_plan`“这里不是 Git 仓库，不能开独立目录”。分工改派给新负责人时按新负责人的标记处理。分支守卫（`branch_claims.py`）按“房间分支 × 该成员本房所有适用 goal”认领：两条分工交替提交都放行，改派后原负责人失去该分工的绑定。领头不开 worktree（领头不接分工，见 M9）。 | 用户裁决 4、23：用户仓库可能很大（复制一份 20 GB），由领头权衡。评审：现有认领唯一键是项目、分支、会话，后一次提交会改写 `goal_id`。 |
| M2 | 目录 `<工作区根>/.crew/worktrees/room-<房号>-<成员slug>-<会话 ID 前 8 位>`，分支 `crew/room-<房号>-<成员slug>-<会话 ID 前 8 位>`。第一次创建时用 `git rev-parse --git-path info/exclude` 找到实际的 exclude 文件（工作区可能本身是 linked worktree 或仓库子目录），追加只忽略受管目录的一行（只在本机生效、不进提交）。建树时登记仓库顶层路径；目标目录已存在且不是本行登记的 worktree 就拒绝并报原因。 | W2：目录必须在工作区根下，用量归属、守卫和派工都按“是否在根下”判项目；`spawn` 用的 `Temp/wt` 在用户仓库里不一定被忽略。评审：`.git` 可能是文件，名字要防撞。 |
| M3 | 基点：开了 Git 管理用它的 `integration`，没有就用 `base`；没开 Git 管理用主目录当前 `HEAD`。创建时记下基点提交号。 | W3：与 Git 管理员合并目标一致。 |
| M4 | **何时建**：批准计划的同一事务里，给每位标了 `独立目录` 的分工负责人插 `war_room_worktrees(state='pending')`；改派或新版计划新标的同样补一行。`git worktree add` 在后台循环里、锁和事务之外执行，成功 `active`，失败 `failed` 带真实原因；Git 已建好但库里还没标 `active` 时（中途重启），核对路径、分支和仓库身份一致后接着标 `active`。**启动闸门放在房间目标的公共创建、启动和恢复入口**（不只 `can_work`）：有 worktree 行的成员，worktree 不是 `active` 就不开执行、不送输入；共用目录的成员不受影响。升级前已批准的作战中房间不补建，成员都按共用目录处理（原因“升级前开的房”）。 | W4：SQLite 回滚撤不回 Git 命令，按表登记可重放、重启可续。评审：普通任务输入能绕过 `can_work`。 |
| M5 | **执行目录**：统一解析 `war_room_worktree.path_for(conn, execution)`，返回 `{'path': str}`（已就绪）、`{'pending': True}`（有隔离未就绪）或 `None`（不属于隔离房间）。`goal_runtime.target()` 带上可信的 execution 身份；`workspace_execution.execution_session()` 重读会话时也走这个解析，CC 和 Codex 启动、公共 `send()` 拿到同一个目录；Codex 原生审批复核（`codex_runtime.py` 审批段）用同一有效目录比对。常驻会话 `cwd` 不动。 | W5；评审：`execution_session()` 会重读 `sessions.cwd`，单改 `target()` 兑现不了双载体隔离，审批复核也会因目录不同误拒。 |
| M6 | **合回只走 Git 管理员流程**：成员收工按守则调用现有 `git.request.add`，显式带 `branch`，由 Git 管理员合并；后端不新增合并操作。工作区没开 Git 管理时：批准计划的通知和房卡都写明“本工作区没开 Git 管理：成员收工后没合并的改动会存成备份引用，开启 Git 管理后可以照恢复命令取回再合并”；收工按 M7 (e) 备份、告知、清理。 | 用户裁决 4“收工后走 Git 管理员的合并流程”；评审指出主控草案让领头在主目录合并越出了用户口径。 |
| M7 | **何时清理**：任一条件成立即标“收工”——本人在本房绑定 goal 的分工全部终态；被移出房间；房间关闭（完成、放弃、撤销、叫停、预算结束）。本人在本房没有正在跑的执行后，后台按序处理；操作对象只取登记行，并和 `git worktree list` 实际登记双向核对（路径、分支、仓库一致，且没有进程占用）才动手：(a) 有未提交或未跟踪改动（不含被忽略文件）先做快照提交（临时 index，`write-tree` + `commit-tree`，父为当前 `HEAD`，不动分支和主目录）；(b) 分支末端（有快照看快照）已是合并目标的祖先即算已合并；(c) 已合并删 worktree 和分支；(d) 未合并但有未处理的 `git_merge_requests`：只删 worktree，保留分支，记 `awaiting_merge`，申请处理后合并了删分支、被拒按 (e)；(e) 未合并也没有排队申请：先写备份引用 `refs/crew-backup/room-<房号>/<slug>` 并读回确认指向快照或分支末端，再删 worktree 和分支，在房间时间线发一条“已备份”公告附恢复命令 `git branch <名字> refs/crew-backup/...`。每步可重放，失败记 `error` 保持当前状态，下轮重试。清理不接受客户端传入的路径或分支。分工因合并被拒或打回重新变成 `todo/doing` 时，从原分支（已删就从备份引用）重新建 worktree 恢复 `active`。 | 用户裁决 4“没合并的先备份”；W7。选引用不选 bundle：对象就在原仓库，跨平台；快照能保住未提交改动。评审：删前核对占用和登记。 Gemini 评审：打回重做时成员会因没有 worktree 卡死。 |
| M8 | **非 Git 工作区**：不建表行，所有成员共用目录；开房和批准时给领头的通知加固定一句“这个工作区不是 Git 仓库，成员共用同一个目录，没有隔离；分工按文件不重叠来切。”是否 Git 仓库用 `hooks.git_details(root)` 判断；仓库损坏或找不到 git 如实报错，不当成“没有隔离”。筹备期在计划书顶部、「敲定，开打」确认框里写明哪些成员“未隔离、共用目录直接改文件”（非 Git 工作区就是全部成员）。 | 用户裁决 4、23；W8。Gemini 评审：开打前就要知道。 |
| M8a | **共用目录成员**（没写「独立目录」的，以及非 Git 工作区的全部成员）：批准和更新计划时后台检查共用目录成员之间的「文件:」范围互不重叠（路径前缀相交即重叠），重叠报 `invalid_plan` 并指出行号。守则要求只提交自己范围内的文件。批准时在 `war_room_events` 记 `kind='isolation_base'`（基点提交）。**收工越界检查**：成员收工（同 M7 的收工条件）时比对两个来源——他本房执行 journal 里的改文件工具调用（CC 的 Edit/Write/MultiEdit/NotebookEdit，Codex 的 fileChange），以及相对基点、不在任何成员范围内的 `git diff` 改动；越界写 `war_room_events kind='out_of_scope'`（`{member, files, source}`），给领头发一条 `wake=1` 通知，并在「需要你关注」列出越界文件；不自动回滚。 | 用户裁决 23：不开的共用目录，文件范围不重叠、只提交自己的文件、收工时做越界检查。 |
| M9 | **领头不能当分工负责人**：`war_room_plan.owners` 遇到领头报 `invalid_plan`“第 N 行：分工只写参战成员的活，领头的协调不写进分工”；`set_item` 负责人是领头也拒绝，不写负责人时不再默认成发帖的 AI 自己。 | 用户裁决 6 偏差 1；Q4 指出后台反而放行领头。 |
| M10 | **筹备期不能发拍板卡**：`war_room.decide` 在 `phase='preparing'` 报 `conflict`“筹备期的问题写进计划书「等你定」，批准后再发拍板卡”；`channel.py` 里“当场发拍板卡”的动态提示只在作战中给。旧的筹备期卡片仍可答复、照常送达；`test_war_room_plan.py` 里要求筹备期发卡成功的用例改用作战中的房间。 | 用户裁决 6 偏差 2。Q4：`463e080` 去掉了阶段检查，本批按用户口径恢复。 |
| M11 | **先存草稿**：开房通知（用户开房和 AI 开房都附；AI 开房放在 `war_room.open` 的返回里）由 Crew 附上工作区概况——根目录第一层最多 40 项，Git 仓库再附当前分支、是否有改动和仓库大小（`git count-objects -vH` 的 size-pack，供领头决定开不开独立目录）——和三节齐全的计划书骨架，第一句写“先用 war_room.plan.update 按骨架保存 v1，再细化。”`draft_version=0` 时 `war_room.consult` 报 `conflict`“先保存一版计划书”。 | 用户裁决 6 偏差 3；W11：Crew 先给目录，模型不用调原生工具卡在审批上。 |
| M12 | **不发空话帖**：AI 发的房帖在 `channel.post()` 解析出真实房号之后统一过滤（覆盖 `war_room.post` 和 `channel.post(reply_to=房内帖)`，宿主生成帖不过滤）：去掉标点和空白后不超过 12 个字、整句落在固定词表里，报 `empty_post`“这条没有新信息，不用发”。词表：收到、好的、好、明白、了解、知道了、在等、等待中、我在等、继续等、稍等、马上、在处理、处理中、OK、ok、👌。用户的帖不拦。拦截次数用独立的短事务写进房间事件（`war_room_events.kind='empty_post'`），不随被拒请求回滚。判断前先去掉开头的 `@名字`；词表外再加常见组合空话（如“收到，马上处理”“好的，我在等”）的整句匹配。实测同时统计拦截数和漏网的短空话数。 | 用户裁决 7；W12。评审：在 `@board.atomic` 里先记再抛错会一起回滚。 Gemini 评审：“@领头 收到”能绕过。 |
| M13 | **进度不叫醒人**，房帖和状态板两个入口统一规则：`war_room.post` 新增可选参数 `kind` ∈ `progress`（默认）/`done`/`blocked`/`failed`/`question`。成员发的 `progress` 帖即使点名或回复领头也是 `wake=0`；`done`/`blocked`/`failed`/`question` 叫醒领头或被点名者。领头点名成员、派活照常叫醒。状态板 `war_room_plan.changed` 按事件决定：事项改成 `done` 或 `blocked` 给领头一条 `wake=1`；换负责人给新负责人一条 `wake=1`；批准开工给每位有分工的成员发开工通知（**已在房内的新负责人也发**）；其他变动（`doing`/`todo`、改说明、改契约、存草稿）不发通知。失败走 `task_returned`（已是 `wake=1`）。 | 用户裁决 7；Q5/W13：状态板一动就通知全房是发帖量主要来源。评审：`channel.post` 对点名和回复另有唤醒，只改 `changed()` 收不住。 |
| M14 | 守则文字只改作战室那一节（`plugin/prompts/roles/leader.md`、`workspace_assistant.md` 的作战室段落，`git.md` 的房间分支说明），写明：先存草稿；筹备期问题写进「等你定」；怎么决定成员开不开独立目录（改动面大、互相可能碰到同一处时开；仓库很大或改动小且范围清楚时共用目录并写清范围）；共用目录只提交自己范围内的文件；内部帖只写结论、证据和阻塞；独立目录的成员收工带分支申请合并；被看门狗叫醒后直接去查卡在哪，不写说明。**人格块不改这三份角色文件。** | 后端拦着，提示词只讲原因。 |
| M15 | **统一路由**：新增 `war_room.route(conn, notice) -> ('goal', goal_id) | ('main', None) | None`，`deliver` 和主会话认领都只调它，**选择和认领在同一事务完成**，已有认领优先：收件人在本房有已批准、绑定 goal、`todo/doing` 的分工 → 送进那个任务执行；否则收件人是领头或仍在房里的成员 → 借他的主会话；房间关闭、暂停或收件人已离开 → `None`。`'main'` 不再限于点名和拍板答复，`task_returned`、看门狗通知、M13 的唤醒通知都走这条路。认领后发送前再复核资格；认领后收件人退房、关房或分工被撤，通知标成“已取消：<原因>”，界面看得到。外部 CC 占用的主会话按现有规则等待，可托管时再投。前端回执：作战室一条消息里所有被 @ 的人都显示送达状态，等待文案改成“对方还在忙 / 已停止 / 在外部窗口”，不再写死“领头”。 | 用户裁决 5；W15。两条路互斥，避免同一条通知执行两次。 Gemini 评审：现在只查领头的回执。 |
| M16 | 判断“有没有可投递的分工”只看 goal 和分工状态，删掉 `claim_main_notices` 里“当前轮有没有执行记录”的条件。 | Q2/W16：已结束的执行记录也会挡住主会话路。 |
| M17 | 借主会话收房内消息的成员（按 M15 一定没有可投递分工）在输入前加一句“要改文件的活，等分工批准后在本房任务里做。”这句由阶段 0 的 `war_room.main_input_note()` 钩子提供。不加工具层写入限制；真 AI 实测统计借主会话回合里在主目录改文件的次数，出现就回到这里加限制。 | Q2 风险：借主会话改文件会绕开隔离。评审建议按回合限制原生写入；驳回：借主会话的成员没有分工，本来就在自己原目录工作，私聊时同样能改文件，隔离针对的是房间分工；工具层限制要同时改 CC 钩子和 Codex 审批，代价大。先量化再定。 |
| M18 | 不做“按房间单独恢复会话、授予和收回权限”。 | 用户裁决 5 授权总 leader 判断；M15 已满足需求。 |
| M19 | **整体替换旧守护**：删 `war_room_work.watch`、`end_idle`、“空闲十分钟”文案和测试；从 `schema.sql` 基础 DDL 删掉 `war_room_idle` 表和索引；`store.connect()` 迁移在事务里持锁重查旧表存在，存在才把它引用的未发提醒改 `wake=0` 再 `DROP TABLE`，一并提交。`can_work` 保留。 | 用户裁决 7“唯一的防停滞手段”。评审：空库和第二次打开时旧表已不存在，迁移要先查表。 |
| M20 | **触发**：`ChatJournal.emit` 写完 `turn_result` 后只把可在 worker 解析的身份（room_id，或 execution / session ID）放进内存“待复核”队列，不查库、不发通知；房号在 worker 里事务外解析。另几处调同一入队：`goal_runtime.sync` 发现执行状态实际转换为离线或终态；批准计划、预算恢复；服务启动把所有作战中房间入队一次。另有每 60 秒一次的后备复核，只对作战中、且上次复核因“有派工在跑”或“有待投递消息”而跳过的房间做纯 SQL 判定，补派工结束、消息送达这类没有回合事件的变化。复核不调模型。 | W20；Q3：两载体共用 `turn_result`。评审：第一次因忙跳过后可能再无回合事件。后备复核是零 token 的数据库判断，符合“事件触发、不空转模型”的本意。 |
| M21 | **停滞判定** `war_room_watchdog.stalled()`，全部成立才算：① `status='open'`、`phase='active'`、没有预算暂停；② 已批准完成标准没全勾（与 `close` 同一判断）；③ 领头和在房成员的主会话、本房绑定 goal 的执行、这些成员在别处的活动执行都不忙（`manager.busy`，CC 外部看 `live`，Codex 看 lease）；外部 CC 状态未知时不算停滞，下次复核再看；④ 属于这些会话或本房 goal 的派工（`codex_runs`，不加时间窗）没有 `running`；`unknown`（进程已不在、没交回执）不算在跑；⑤ 本房没有 `wake=1` 的未发通知（看门狗自己发的不算）；⑥ 没有开着的本房**阻塞型**拍板卡（`blocking=1`），也没有本房执行的待处理审批。⑥ 不成立时房卡显示具体在等什么（等你定 / 等你审批），不叫醒领头。 | 用户裁决 7 的五条。⑥ 是主控补充，评审认为会压住还有别的活可做的房；收窄为只看阻塞型拍板和审批——这时房里的活确实卡在用户，叫醒领头只添噪音，且只会让停滞判定更严。 |
| M22 | **宽限和叫醒**：表 `war_room_watch`。判定停滞且当前没有停滞段就开一段（`stalled_since=now`，`check_at=now+60s`）；到 `check_at` 再判，仍停滞且本段没叫过，就在同一事务里给领头发一条 `wake=1` 通知（按 M15 路由）并记 `woke_at`、`notice_id`。每段最多叫一次。宽限内恢复进展、关房或暂停时，结束本段并撤销还没送出的看门狗通知（改 `wake=0`）。“已叫醒”以通知 `sent_at` 为准。文案固定：“作战室 #N 停下了：没人在干活，目标还没完成。去查卡在哪，解决后继续朝目标推进，不用写说明。” | 用户裁决 7。评审：入队、实收、处置结束要分开。 |
| M23 | **需要你关注**：本段保留到看门狗通知被领头实际收到、且领头那一回合结束为止（领头因看门狗而忙不算“不再停滞”）。回合结束后复核：仍停滞且没有实质进展，写 `attention_since`。实质进展 = 完成标准勾选变化、事项状态变为 `done`/`blocked`、新交付、新批准版本；看门狗自己的输入、执行和被拦的空话帖不算。`progress_key` 按这几项计算。之后有实质进展或不再停滞就结束本段、清掉关注。 | 用户裁决 7。评审：原写法会被看门狗自己引起的忙碌提前清段，也会被空进度帖误判有进展。 |
| M24 | **房卡字段**：`_card` 顶层加 `isolation`（`'worktree'|'mixed'|'none'`：全员、部分、没有成员开独立目录）和 `isolation_reason`（`null|'not_git'|'pre_upgrade'|'lead_choice'`）和 `watchdog`（`null` 或 `{state:'stalled'|'woken'|'attention'|'waiting_user', since, reason}`，前端在 `attention` 和 `waiting_user` 时显示）；`get` 加 `worktrees`（只含开了独立目录的成员；用户和领头看全部，成员只看自己一行）和 `isolation_members: [{member, session_id, mode:'worktree'|'shared', paths, out_of_scope}]`，共用目录的成员在参战成员区和战况里标“未隔离”并显示声明范围。公开房卡和变更推送只带状态，不带路径和原始错误；路径、错误只在授权详情里给；备份公告照常带恢复命令。前端在现有「需要你关注」区追加“房里停下了：叫醒领头后还是没有进展。”，不伪造卡住事项或拍板卡。「需要你关注」要让人在房外和手机上也看得到：左栏作战室入口（`CrewGlobalRail.roomState`）、作战室目录、手机顶条摘要和收起的战况栏都认 `attention`，显示「等你关注」角标；关注条目做成可点，文案“房里停下了，叫醒领头仍没动静 · 问问领头 ›”，点了聚焦到和领头说话的输入框。worktree `failed` 也立即进关注区并写明原因。worktree 状态在界面上用人话：准备目录中 / 独立目录运行中 / 等合并 / 已清理 / 已备份。备份公告在主时间线可见，配「复制恢复命令」按钮。 | W24；Q3 指出 `.wr-attention` 现成。评审：成员可见范围要覆盖详情、事件和推送。 Gemini 评审：房外、手机看不到停滞，建失败会静默卡住，备份公告在时间线不渲染。 |
| M25 | **作战室界面上的 4 条审美细节**归本块（尾巴盘点 T1 的 1、2、5、6）：目录和详情共用 `roomSubtitle(room)`，去掉目标末尾多余句号，目标为空或去句号后等于房名时只显示“领头 X”；「需要你关注」里能跳转的拍板条目尾部加 `›`，纯文案条目不加；筹备房左右两栏空状态用同一局部类，水平居中、距栏头同一垂直距离（不改 `channel.css`）。「需要你关注」里关联了执行记录的卡住条目也可点、加 `›`，点开对应执行记录。 | 这些都在 `frontend/war-room.js|css`，本块同时在改关注区，避免两块撞文件。 Gemini 评审：卡住项点不动。 |

### 2.2 人格（块：persona）

| M# | 裁决 | 理由 |
|---|---|---|
| M26 | 人格主体 = `rules.skill_role(conn, session)` ∈ {`leader`, `workspace_assistant`}；Git 助手、成员、系统管理员没有人格，接口返回 `persona_not_applicable`。 | 用户裁决 11；P3、P4（Git 助手 `role='leader'` 但 `skill_role` 是 `git`）。 |
| M27 | 按 `session_id` 存一份，挂 `sessions(session_id)`；换载体、丢会话、改名迁目录不搬数据。合并或退役时人格随原 leader 归档保留，拆出的新 leader 从空白起步。永久删除人员时在 `session_ops.delete()` 同一事务里显式删人格和历史；移除工作区只隐藏、停用，恢复后沿用。 | Q4：两条删除入口都保留父记录，单靠 `ON DELETE CASCADE` 不触发。 |
| M28 | **只注入总对话**（`execution is None` 的进程）：CC 在 `chat.py` 启动时加人格块；Codex 在 `codex_runtime.py` 仅无 execution 时拼进 `developerInstructions`。目标执行、交付后讨论、成员、Git、频道摘要、房间顾问、运维一律不带。人格不进任何 `BUNDLES`。**Windows 命令行长度**：CC 现在用 `--append-system-prompt` 命令行参数，加上 20k token 人格可能超过 Windows 约 32K 字符的命令行上限。人格块先核实 CC 有没有从文件读系统提示的参数，有就改用文件；没有就把人格块作为启动后第一条输入（和守则重发同一机制）送入；测上限正文在 Windows 首次启动和恢复都成功。 | 用户裁决 9；P2：角色包按逻辑 `session_id` 解析，执行也会拿到。评审指出命令行长度。 |
| M29 | 运行中人格变了（成长、编辑、开关切换；定型不影响）：沿用守则的“下一次正常输入带新块”机制，但用独立的 `persona_hash`（按实例、开关、框架和正文算）记在运行实例上，不写 `behavior_prompt_applications`。只在该输入达到载体接收点后才更新 hash，被拒、未知和 `/compact` 不消耗。开关关闭时下一次输入带 `persona_off` 一句。 | 避免把 20k 正文再存一遍（用户裁决 12）。 |
| M30 | 人格块由新守则 `persona_frame` 包裹（可在“行为提示词”里查看和改），写明：只用于直接回复用户；守则、用户当前要求、后台规则优先；作战室帖子、组内消息、任务执行、报告、提交信息、代码与文档一律用守则默认口吻；性格不改变工作方法、判断、工具使用和交付标准。 | 用户裁决 9。 |
| M31 | **每轮标记**：总对话收到的非用户输入（`source` 不是 `human/qq/lark`，或带 `room_id`）在正文前加一行“【本轮不是和用户直接对话：用守则默认口吻，不带个人性格】”。只在该会话当前已注入人格时加。由阶段 0 的 `persona.input_frame(conn, session_id, source, room_id, identity)` 钩子提供（钩子只在没有执行的总对话上调用）；`chat.start()` 直接 `_send(source='crew')` 的首条输入由人格块在 `chat.py` 自己的段里补同样处理；`/compact` 保持原义。带 `room_id` 的输入（包括用户在作战室里亲口说的话）单用一句“【当前在作战室中：本轮一律用守则默认口吻，不带个人性格】”；其他非用户输入用原句。 | M18 决定复用借主会话（不拆房内上下文），房内轮次会看到历史里的人格；用标记压住，真 AI 实测量化串味。 Gemini 评审：用户在房里说话时原句与事实矛盾。 |
| M32 | **成长**：宿主后台一次性调用，不在总对话里自改。复用 `channel_digest` 的无工具、不留会话执行方式，模型按 `settings.resolve(home, 'digest')`，不新增用途。触发：全局开启，且自上次成长后 ≥12 条用户亲口私聊，且距上次 ≥6 小时（从未成长只看条数）；全局同时最多 1 个成长作业，攒得最多的先跑；另有“现在成长一次”手动按钮（开关开启且至少 1 条新私聊）。首次成长（`version=0` 且内容为空）门槛降为 ≥4 条私聊。`persona_growth` 提示词用第一人称写 AI 自己的脾气、说话节奏、口头禅和与用户的相处默契，自由发挥，不写成工作规范或用户资料档案；日常保持精炼（约 500–2000 字），超过 60% 再合并瘦身。 | 用户裁决 10；成本有界，也供实测。 Gemini 评审：12 条才首次见效太慢；不限视角容易写成用户偏好档案。 |
| M33 | **计数口径**：`persona.note_input(conn, session_id, source, room_id, identity)` 在原生接收后调用；主体总对话、`source='human'`、无 `room_id`、identity 不是宿主合成前缀（清单写死在 `persona.py`）才 `pending_turns += 1`。计数只是成长的触发线索，不当证据：成长时重新读 `HOME/chat/<session_id>.jsonl`，按 `growth_cursor` 之后已接收的用户输入（同一口径，按 message_id 去重）配上该轮 AI 最终回复，倒序取到 24,000 字符；关闭期间的输入不采样。手机语音输入（`source='human'`、`input_mode='voice'`）照常计数。 | P 草案 M9。评审认为前缀黑名单不能证明是用户亲口私聊；采纳“以 journal 为准、按 message_id 去重”，计数器只决定何时去读，偶发多计少计不影响内容。 |
| M34 | 成长输出：完整新正文 + 一句话变化摘要（固定两段格式，解析失败算失败）。一个事务里复查开关、资格、`version` 未变（期间用户编辑或重置过就丢弃候选，记“被用户修改覆盖，已放弃”）、长度 ≤ 上限才写新版本（`action='grow'`）。失败保留旧版本，记 `growth_error` 真实原因，不重试，等下次触发。 | 用户裁决 10。 |
| M35 | **20k 的计法**：本地保守估算 `est-v2`，每个字符都计（含空白）：ASCII 字符 ÷ 3，中日韩字符与全角标点 × 1.5，其余字符（emoji、其他文字）按 UTF-8 字节数计，求和向上取整。系数取偏大值，宁可多算。编辑、选起点、恢复、成长都用同一函数校验 ≤ 20,000，超限拒绝（`persona_too_long` 带当前估算），不截断。界面显示“篇幅 xx%”并注明是估算。成长提示词带当前篇幅：≥60% 时先合并相近特质、删过时细节压到 ≤50%；平时新正文 ≤80%。测试覆盖长空白、emoji、代码、中英混排。 | Q5：仓库没有 tokenizer，不引入新依赖。评审：原公式忽略空白、低估 emoji，不能当上限；改成按字符类别偏大估算。 |
| M36 | 起点：内置 5 个 `plugin/prompts/personas/<key>.md`（`natural`/`crisp`/`gentle`/`dry_humor`/`candid`）+ “空白” + “自己写”（`seed='custom'`，`seed_text` ≤1000 字存进 `personas.seed_text`）。选起点覆盖当前内容（`action='seed'`）；重置分两种：回到所选起点（自己写的起点按 `seed_text` 回）或清空。历史每次写入追加，每人保留最近 50 版，可“恢复到此版本”。另有每人一个“定型”勾选（`frozen=1`）：不计数、不成长，注入照常，不增版本、不进历史。内容非空时换起点、重置都先弹确认（写明旧版在历史里能恢复）。5 个起点只定语气，不带任何职能设定。 | 用户裁决 11。评审：自写起点要单独保存，裁剪历史后仍能重置。“定型”由用户自己选，不妨碍“AI 自己随便发挥”。 Gemini 评审：手滑会丢掉养成的性格。 |
| M37 | 全局开关 `persona_enabled`：库里没记录 = 关（老用户升级不突然多出成本和口吻变化）；新手引导这一步默认选“要”，**完成引导时无论引擎是否改动都写入这个选择**；跳过引导不写。关闭：停注入、停成长、停计数，内容保留，界面写“已暂停，内容保留”。网页、远程桌面读 `settings_state()`，本机桌面走 operations worker，两条写入口都认这个字段，并写后读回验证。引导里的开关放在第 2 步「连接 AI 引擎」下方，标题用用户原话“是否想要 AI 慢慢有自己的性格？”，副标题“只影响平时聊天的语气，不影响干活；随时可以在设置里改”。 | 用户裁决 8；P6；评审：引导现在只在引擎变动时保存。 |
| M38 | 只存本机：人格表不进 `storage_v2` 工作区包和导出，导出验证同时检查聊天投影和提示词审计里没有人格正文。设置说明写一句“性格正文会作为提示词发给所选 AI”。权限：人格读写 scope = `HBD`，`get/list/history/grow/restore` 都按工作区、主体资格和归档可见性校验；全局开关跟设置走 `HD`。**`ops/domains/assistant_cover.py` 显式排除全部 `persona.*`**，系统管理员版 `settings.save` 仍只收 `ai`；AI 不能通过 MCP 读写人格（成长由宿主后台做），用最终注册表和实际调用验证。变更推送只带刷新所需的元数据。 | 用户裁决 12。评审：`HBD` 注册后 `assistant_cover` 会自动向全局助手开放人类读写操作。 |
| M39 | **人格块拥有的共享界面文件**：新手引导 `desktop/src/home-onboarding.js|css`（含尾巴盘点 T1 第 7 条：窄屏引导「开作战室」卡描述加 `text-wrap: pretty`，≤620px 按钮换到下一行，文案不变）、`frontend/settings-ui.js`、`frontend/index.html` 和 `frontend/assistant.html` 的新脚本登记、`desktop/src/main.cjs` 的设置资源注入清单。人格的窄屏适配写在 `frontend/persona-ui.css` 里，不改 `mobile*.css`。**人格界面规格**：按钮统一叫「性格」（和「行为配置」并列区分），系统管理员页（`PROJECT === null`）不显示；设置页新增「AI 性格」分类（总开关 + `persona.list` 角色列表，点名字直达编辑），说明写“开启后，每位 Leader 和工作区助手会慢慢形成自己的说话方式；只存在本机，聊天时发给当前使用的 AI”；面板含正文编辑、起点选择、定型、现在长一次、最近一次成长摘要（顶部突出）和历史恢复；容量显示“容量已用 xx%（上限 20k，估算）”，`growth_error` 转人话并配重试按钮；空白态写“再聊几句就会长出性格，也可以先选个起点”。 | 避免与尾巴、安卓撞文件。评审：独立助手页和桌面设置宿主各自加载资源。 Gemini 评审：缺面板布局和入口位置，系统管理员页会点出报错。 |

### 2.3 安卓（块：android）

| M# | 裁决 | 理由 |
|---|---|---|
| M40 | **手机用现有 `browser` 设备身份（Cookie），不新增设备类型。** 原生配对页用 OkHttp 向 `POST /remote/login` 提交 `{kind:"browser",code,device_name}`，带同源 `Origin` 和 `X-Crew: 1`；返回的 `Set-Cookie` 写进 WebView `CookieManager`（`flush()` 后再加载）。原生请求从同一 CookieManager 取 Cookie。一次配对、一个设备记录，电脑“强制下线”同时断开 WebView 和原生。原生 HTTP 用 OkHttp（Apache-2.0），理由：Android 上 SSE、Cookie 和超时控制的事实标准，配 okhttp-sse 直接读事件流。电脑「已连接设备」用配对时的 `device_name` 显示为“Crew 安卓版 · <手机名>”。 | 用户裁决 15；Q7：desktop Bearer 令牌不能当 browser Cookie，WebView 子请求也无法统一加头。 Gemini 评审：新依赖要写许可证和理由；设备列表会显示成“浏览器”。 |
| M41 | **二维码内容就是现有配对链接** `http://<ip>:<port>/remote/login#code=<8位>`。电脑「局域网访问」生成配对码时每条链接旁画二维码；手机相机直接扫能打开网页登录，安卓壳扫到则自己解析完成原生配对。二维码生成用 `qrcode-generator`（MIT）内置到 `frontend/vendor/`，在 `web.py` 静态清单登记精确路径，由 `remote-access-ui.js` 按需加载，不改 `index.html`。`qrcode-generator` 选型理由：单文件、无依赖、MIT，浏览器端直接画；Python 标准库生成不了二维码。绑定 `0.0.0.0` 时虚拟网卡的二维码默认折叠，配对区补一句“用 Crew 安卓版直接扫码”。 | Q7：现有链接已含全部信息。评审：按需加载也要有静态路由。 Gemini 评审：多网卡易扫错。 |
| M42 | 始终用数字 IPv4 访问（`Host` 必须等于电脑本机接收连接的 `IPv4:端口`）。 | 安卓盘点 Q3。 |
| M43 | **mDNS 用 python-zeroconf 0.154.0（LGPL-2.1）+ ifaddr（MIT），未修改源码随仓库内置到 `plugin/vendor/`**，附版本、来源、许可证原文，不走 pip，不改打包。公告来源是局域网访问控制器快照里的实际端口和 `bound/available` 地址：只在局域网访问开启时、只在实际绑定的非回环 IPv4 上公告 `_crew._tcp.local.`；停止或换网卡时注销。TXT 只放 `v=1`、`id=<HOME 路径哈希前 16 位>`、`name=<电脑名>`。依赖导入或启动失败时，局域网访问面板如实显示“自动发现不可用：<原因>”，扫码和手填照常。交付列出导入路径、传递依赖、随附许可证，以及 Windows 源码装载实测（Mac 留到出包）。 | 用户裁决 18 已点名 zeroconf，理由：后端无第三方依赖安装链路（Q11），内置源码不改 Windows/Mac 打包；自写 mDNS 要处理冲突探测、多进程共用 5353、goodbye 包，不划算。LGPL 以未修改源码 + 许可证随包分发满足可替换。 |
| M44 | **通知用 SSE 长连接 + 前台服务**（用户补充裁决 22 已确认）：新增手机提醒表 `device_notifications` 和 `GET /api/notifications/events?after=N`（SSE，支持 `Last-Event-ID`）。来源复用 QQ/Lark 提醒的同一套事项（待拍板、人工待办、最终交付、卡住），采集放在 away 开关和渠道启用条件之外；按事项 ID 去重，保留期裁掉的旧事项不会被重新插成新通知；保留 7 天或最近 500 条；无游标首连只发 `ready`，不回灌；`after=oldest-1` 是连续续读。 | 用户裁决 17 原文是 WebSocket；主控改用 SSE 的理由（单向推送足够、Crew 已有 SSE 和心跳、标准库无 WebSocket 服务端）已经领头转给用户，用户无异议（裁决 22）。评审要求恢复 WebSocket，因用户已确认而驳回。 |
| M45 | **语音复用 `chat.send`**，加可选参数 `input_mode: text|voice`（默认 text）和 `heard: {reply_id, chars}`（只在 voice 时允许）。阶段 0 已落：voice 只接受本工作区助手的总对话（拒绝普通 leader、系统管理员、带 `goal_id`），`input_mode` 传进 `DeliveryMixin.send()`，写进 `input_delivery` 事件，voice 时计入输入去重摘要。安卓块：校验 `heard.reply_id` 是该助手上一条回复，`chars` 统一按 Unicode 码点计；在 `web_api/sessions.py` 现有 `prefix`/`display_text` 路径里给 voice 输入加说明（语音可能有同音错字；回复会被朗读，先说结论、口语、短句，不念代码和链接；语音回合不直接改文件，要改先发确认卡；有 `heard` 时补“上一条回复用户只听到前 N 字”）；界面只显示原话，消息旁显示“语音”标记。 | Q9；不新开语音接口，避免两套。 |
| M46 | **语音话轮里的写操作一律走确认队列**：`ops/executor.py` 的高风险判定改为 `caller.is_ai and not confirmed and (op.risk == 'high' or (caller.role == 'workspace_assistant' and op.access != 'read' and voice_turn(...)))`。`voice_turn` 优先看这次调用绑定的原始消息证明（`message_id`）对应输入的 `input_mode`；没有证明时看该会话最近一条已接收的用户输入。确认记录保存原消息证明、调用者、项目、参数摘要和请求号；用户点确认时按同一证明复核、只执行一次（修好现在 `confirmed=True` 回放不带 `message_id`、`risk='confirm'` 操作点了仍失败的问题）。CC 在语音回合由 `hooks.py` 的工具前钩子拒绝原生改文件工具；Codex 的原生改文件只靠语音前缀约束，作为已知限制写进交付和手测清单。手机语音页拉 `interaction.list` 画确认卡，只能点按钮确认；说“确认”不生效。语音页有挂起确认卡时：播报一句“列好了，请点屏幕确认”；端侧拦截挂起期间的纯短确认词（确认、好的、执行、同意），不送 `chat.send`，只轻闪确认卡提示手点；点完播报结果。工作区助手高频写操作（建任务、开作战室、记想法、待办等）补人话确认摘要（做什么、交给谁），不露内部 ID。 | 用户裁决 16 写进后端，不靠提示词。评审：最近一条输入会被插话改变，确认回放会失败，原生写入也要管。 Gemini 评审：口头“确认”会被当成新一轮输入、循环出卡；普通写操作的卡会露原始字段。 |
| M47 | sherpa-onnx 用 GitHub 官方 AAR v2.0.3（校验 SHA-256），构建脚本下载到 `android/app/libs/`（不进 git）；只打 `arm64-v8a` 和 `x86_64`。 | 安卓调研：Maven Central 无官方包，JitPack 构建失败。 |
| M48 | **模型打包进 APK**（按 `android/assets.lock.json` 的 URL + 字节数 + SHA-256 下载到 assets，不进 git）：ASR `x-asr-160ms-streaming-zipformer-transducer-zh-en-punct-int8`（中英混说、带标点）+ `silero_vad.onnx` + TTS `vits-icefall-zh-aishell3`。debug APK 约 230–260 MB，接受。 | Crew 口语中英混说多；侧载不受商店体积限制；断网和模拟器都能测；国内直连 GitHub/HF 不稳。 |
| M49 | TTS 默认 sherpa AISHELL-3，设置里可切系统 TTS（系统没中文音色时不显示）。不用 matcha-baker、vits-zh-ll、kokoro、piper-phonemize（授权问题）。音质差就把默认改成系统 TTS，列进手测清单。块内实测必测中英混说的回复朗读；AISHELL-3 吞英文或乱读时，默认改成支持中英混读的系统 TTS（sherpa 作没有系统中文音色时的离线备选），语音页顶栏可快速切换。 | 用户裁决 13、16 的许可证红线。 Gemini 评审：Crew 口语里 Leader、Git、PR、Codex 很多。 |
| M50 | 打断在端侧做，状态照 OpenLive 四态 `idle → listening → thinking → speaking`（Apache-2.0 只借鉴设计，Kotlin 重写）；朗读中确认是人声（连续 ≥250 ms，朗读期提高门限）立即停播、丢弃未播 PCM、取消后续合成，已播字数随下一句以 `heard` 上送。“停止朗读”不等于“取消助手正在做的事”，后者是单独按钮（复用现有中断操作）。句末静音判停约 1.0–1.2 秒，提供「闭麦」开关；语音页顶栏大字写当前工作区名，「停止朗读」和「取消生成」分开两个按钮；`heard` 按已播的分句回传字数。 | 用户裁决 16。 Gemini 评审：想词停顿会被切碎。 |
| M51 | 录音 `VOICE_COMMUNICATION` + `AcousticEchoCanceler` + `NoiseSuppressor`（逐个 `isAvailable()`，结果写进语音页“诊断”）；只在语音页可见时录音，不做后台录音。 | 用户裁决 16；后台录音要 `microphone` 类前台服务，第一版不需要。 |
| M52 | 通知前台服务类型 `connectedDevice`，声明 `CHANGE_WIFI_MULTICAST_STATE`；常驻通道 LOW，提醒通道 HIGH；断线重连退避 5、10、15、20、30、45、60、120 秒封顶；Android 13+ 单独请求 `POST_NOTIFICATIONS`。通知权限在配对成功进入工作台时申请；常驻通知写“已连接 <电脑名> · 随时接收提醒”。 | `dataSync` 在 Android 15 有 6 小时配额；退避照 ntfy（Apache-2.0）。 |
| M53 | 明文 HTTP 只放给已配对主机：debug 构建 base-config 放开明文（局域网 IP 不固定、不支持 IP 段通配），应用层约束——WebView 只允许已配对 `scheme+host+port`，其他链接交系统浏览器；原生 HTTP 只发往已配对主机，禁用重定向跟随。**首次配对例外**：未配对时只向用户选中的候选地址发一次不带任何凭据的配对请求，成功后绑定 origin、实例 ID 和 Cookie。切换电脑时凭据和通知游标按实例隔离，旧凭据不发给新地址。 | 安全边界。评审：原写法与首配请求冲突。 |
| M54 | JS 桥 `window.CrewAndroid`，只注入已配对主机的主 frame（`addWebMessageListener` 限定 origin，同时核对当前已配对实例）；桥消息统一 `{id, method, params}` → `{id, ok, result|error}`；能力：`available()`、`openVoice(project)`、`notificationsEnabled()`/`requestNotifications()`、`switchComputer()`。`openVoice(project)` 打开后由服务端 `assistant.open` 重新解析该工作区助手身份。网页端只在**工作区助手页**（不是系统管理员页）检测到 `CrewAndroid` 时显示“语音”按钮。扫码用 CameraX + ZXing core（Apache-2.0），不用依赖 Google Play 的扫描器。手机工作区首页顶栏也给一个语音直达入口（同样只在安卓壳内）。点中发现的电脑后提示“去电脑 Crew「设置 → 局域网访问」生成配对码，扫码或输 8 位码”。WebView 和电脑断连时显示原生重连页，能按 `instance_id` 重新发现换了 IP 的同一台电脑。 | Q13：不能借 `CrewBrowser` 或伪装 `crewDesktopShell`。评审：`assistant.html` 同时承载系统管理员和工作区助手。 Gemini 评审：发现电脑后不知道去哪拿码；电脑换 IP 会白屏。 |
| M55 | **旧语音原型废弃**：阶段 0 删除 `frontend/voice.html|js|css`、`web.py` 的三条静态登记、`test_assistant_http.py` 里对应三项；安卓块在 `docs/VOICE_FEASIBILITY.md` 顶部加一句“已由安卓原生语音实现，见 docs/ANDROID.md”，正文保留作选型依据。 | 用户裁决 20：不留两套；原生层才能拿到麦克风。 |
| M56 | 构建环境：复用本机已有的 JDK 21 和 Android SDK（`%LOCALAPPDATA%\Android\Sdk`，已有 android-36 平台、模拟器和 WHPX 加速），**只读使用，不往里装组件**；Gradle 用 wrapper（下载到用户级 `~/.gradle`）；专用 AVD `crew_test_api36`。确需新增 SDK 组件时装到 `C:\Tools\android-sdk` 独立目录。 | 用户裁决 19“不碰系统级设置”；安卓构建环境实测已通过（AGP 9.5.3 / Gradle 9.9.3 / Kotlin 2.4.21）。 |
| M57 | WebView 里的手机适配沿用现有 ≤760px 样式；只修安卓实测发现的问题（软键盘遮挡、返回键、安全区 inset），改动限于 `mobile.css`、`mobile-chat.css` 的相关选择器和壳；`mobile-pages.css` 的 `.sl-categories` 归尾巴块。 | Q13；避免撞文件。 |

### 2.4 尾巴（块：tails）

| M# | 裁决 | 理由 |
|---|---|---|
| M58 | **审美细节 3、4 归本块**：手机会话库分类胶囊复用想法墙 `.nw-filters` 的 `mask-image` + `data-more`，右边还有内容才渐隐（`frontend/session-library.js`、`mobile-pages.css` 的 `.sl-categories`）；空首页「推荐」标签改成 `12px × --fs`、`--accent-text` 字色加 `--accent-soft` 浅底胶囊，浅深两主题都看，只改 `frontend/workspace-home.css` 的局部选择器。细节 1、2、5、6 见 M25（作战室块），细节 7 见 M39（人格块），共 7 条。 | 按文件归属拆，避免撞车。评审：反馈要的是颜色和字号都调。 |
| M59 | **3 个 `test_install_launchers` 修测试，不修启动器**：两条帮助用例断言退出 0、`usage: crew`、`--home`、`doctor`，保留含空格/引号/中文的 HOME、PATH 陷阱和不起后台断言；HOME 用例改用 `crew doctor`，两套隔离安装 HOME 各一个项目名再注入一套外部 `CREW_HOME`，断言读到自己那套、都没有 `web.json`/`chat/`。找 bash 改成按 `git.exe` 所在 Git 根找 `bin/bash.exe`（PATH 首项是 `mingw64/bin/git.exe` 时现在会跳过）。 | Q8：CLI 拆分 `3a56f29` 后 `--session` 已删，启动器本身正常。 |
| M60 | **手机宽度架构图可编辑**：去掉 `editable = () => !phone.matches` 的“窄屏 = 只读”；触屏入口照思维导图范式——点选 part 出底部操作栏（文案见本条末尾），改名弹单行输入，Leader 节点可「设负责人 / 移出 / 进对话」，连线用「连接」后点第二个节点；选中后单指拖节点移动、空白拖平移、双指缩放（沿用 `canvas-core.js`），不做长按；撤销重做、自动排列、方向放进标题栏「更多」；手机历史抽屉开放回滚（二次确认）；触点 ≥44px，`.arch-pin` 在 760px 下恢复可点。桌面行为不变。点 Leader 先选中、出底部栏「进对话 · 设负责人 · 移出 · 更多」（进对话放第一位）；**没选中节点时单指划过一律平移画布，选中高亮后再拖才移动**；连线模式顶部出“点目标设为下级 · 取消”提示条；`.arch-pin` 只扩透明热区不放大圆点；按钮文案全中文（「加子项 · 改名 · 移到… · 删除」）。 | 用户裁决 21；Q9。 Gemini 评审：开放编辑后点 Leader 与点选冲突，划屏会误拖节点。 |
| M61 | **Codex 侧栏 leader 总对话线程，补方案 A 的缺口**（主体已在 `7a7a11d` 落地）：关闭原因在生命周期入口显式传入（`chat_lifecycle.py`）；Crew 自建的人员总会话在 `idle_reclaim`、`service_close`（服务关闭）、`user_stop` 时归档；载体切换和交接走的 `detach` 显式带不归档的原因，不再落成 `managed_stop`。`codex_visibility.reconcile_stopped` 加人员总会话候选（离线、不在运行、仍由 Crew 持有、Crew 自建、`archive_confirmed=0`、不是 detach 或已移交），取租约后重查再归档；解档后 `archive_confirmed` 复位；用户接入和来源不明的不碰。`docs/CODEX_INTEGRATION.md` 与实现对齐。 | Q10；方案 A 见用户 2026-11-19 调研结论。评审：服务关闭实际标 `service_close`，`detach()` 调 `close()` 会落成 `managed_stop`，按原写法会漏归档又误归档。 |
| M62 | **报告出处写法**：`docs/FEEDBACK_REPORT_2026-11-28.md` 里指向 `Temp/` 和仓库外的链接改成反引号路径文本，仓库内文件保留链接；规则写进 `AGENTS.md`“验证与交付”一句。产品内 `plugin/prompts/final_report.md` 不改。**`AGENTS.md` 本批只归本块改**，其他块需要补的说明写在交付里，由总 leader 合并时补。仓库外的 `plan-dispatch` 技能由总 leader 收尾时同步。同一报告里指向 `.claude/worktrees/.../Temp/` 的链接一并改掉。 | 用户裁决 21；Q11。 |
| M63 | **导图触摸板/触屏能自动测的部分**：`test_canvas_core.js` 补无修饰 wheel 平移、Ctrl+wheel 以指针为锚缩放、原生可滚区绕过、单指空白平移后保存相机、捏合后无幽灵 click 和误提交、失捕获回滚；另用 Electron + CDP（`Input.dispatchTouchEvent`、`synthesizePinchGesture`、带 `ctrlKey` 的 wheel）对导图和架构图各走一遍，脚本放 `Temp/`、结果进交付，不新增冒烟脚本。真机才能测的列进手测清单。 | 用户裁决 21；AGENTS.md 冒烟脚本限 4 个。 |

## 3. 能力和权限

| 能力 | 谁能做 | 校验位置 |
|---|---|---|
| 写计划书分工 | 领头，负责人不能是领头 | `war_room_plan.owners`、`war_room.set_item`（M9） |
| 发拍板卡 | 领头，仅作战中 | `war_room.decide`（M10） |
| 请顾问 | 领头，需已有草稿 | `war_room.consult`（M11） |
| 房帖 | 成员和领头；AI 空话被拒 | `channel.post` 房内分支（M12） |
| 建、清 worktree | 只有宿主后台，按登记行和 `git worktree list` 双向核对；不接受客户端传入的路径或分支 | `war_room_worktree.sweep`（M4、M7） |
| 改人格 | 用户（本机、远程桌面、已认证手机浏览器，`HBD`）；全局助手和所有 AI 都不能读写；AI 只能经宿主后台成长 | `ops/domains/persona.py` + `persona.py` 资格判定；`assistant_cover.py` 排除 `persona.*`（M26、M38） |
| 改人格总开关 | 用户（`HD`） | `settings.save` 与桌面本机 worker（M37） |
| 语音话轮写操作 | 只能进确认队列，用户点按钮执行一次 | `ops/executor.py`、`ops/confirmations.py`；CC 原生改文件由 `hooks.py` 拒绝（M46） |
| 读手机通知流 | 本机、已配对 browser 和 desktop 设备 | `remote_routes.TRANSPORT_GET` + 现有长流的撤销和监听复查（M44） |

## 4. 接口约定（冻结）

### 4.1 表结构（阶段 0 已提交，`schema.sql` 基础 DDL，`CREATE TABLE IF NOT EXISTS`，新旧库连接即建）

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
    seed_text TEXT,
    frozen INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0,1)),
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

- `schema.sql` / `store.py` 的后续改动只有两处，各归一块：作战室删 `war_room_idle`（M19）；安卓若要给 `interaction_requests` 补原消息证明列（M46），在 `store.py` 新增独立的迁移函数并同步基础 DDL。其余块需要新列先找总 leader 改契约。

### 4.2 阶段 0 钩子（签名冻结，阶段 0 为空实现）

| 位置 | 签名 | 阶段 0 行为 | 归属 |
|---|---|---|---|
| `chat_delivery.DeliveryMixin.send()` | 新增参数 `input_mode='text'`（`'text'|'voice'`，其他值报错） | voice 时写进 `input_delivery` submitted 事件的 `input_mode` 字段，并计入输入去重摘要 | 安卓读取 |
| 同上，输入投递前（只在没有执行的总对话上调用） | `war_room.main_input_note(conn, session_id, source, room_id) -> str|None` | 返回 None | 作战室实现（M17） |
| 同上 | `persona.input_frame(conn, session_id, source, room_id, identity) -> str|None` | 返回 None | 人格实现（M31） |
| 同上，原生接收后 | `persona.note_input(conn, session_id, source, room_id, identity) -> None` | 无操作 | 人格实现（M33） |
| `chat.send` 操作参数（`ops/domains/sessions.py` 声明，`web_api/sessions.py` 分流） | `input_mode`（enum `text`/`voice`）、`heard`（`{reply_id:string, chars:integer≥0}`） | voice 只接受本工作区助手总对话；非 voice 带 `heard` 报错；`heard` 内容暂不使用 | 安卓实现前缀和 `heard` 校验 |
| `web_service/workers.py` `deliver_notices()` | `war_room_worktree.sweep(manager, conn, now)`、`war_room_watchdog.tick(manager, conn, live, now)` | 无操作 | 作战室实现；可以把调用移出 `write_lock`/`chat.lock` |
| `web_service/workers.py` `_poll_notices()` | `persona_growth.tick(manager, conn, now)` | 无操作 | 人格实现 |

- 各钩子返回的非空行按“作战室提示、人格标记”的顺序加在正文前，`display_text` 保留原话，journal 原话和去重摘要不含提示。
- **后台循环纪律**：`manager` 就是 `self.chat`，时钟是 `self.now().isoformat()`，`live=None` 表示外部 CC 清单未知。传入的 `conn` 只在调用期间有效。锁里只做短事务认领；Git 命令和 AI 调用在锁外、用新连接提交结果；成长作业登记到服务 stop/close。

### 4.3 作战室

- 计划书「分工」条目：`- [ID] 负责人 | 事项 | 文件: a/, b.py | 独立目录`，「文件:」必填，「独立目录」可选（M1、M8a）。
- 房卡 `_card`：新增 `isolation: 'worktree'|'mixed'|'none'` 和 `isolation_reason`、`watchdog: null | {state:'stalled'|'woken'|'attention'|'waiting_user', since, reason}`；`get` 新增 `worktrees: [{member, path, branch, state, outcome, backup_ref, error}]`，成员只看自己一行；新增 `isolation_members`（每人 `mode`、`paths`、`out_of_scope`）；公开房卡和推送不带路径与原始错误（M24）。
- `war_room.post` 新增可选参数 `kind`：`progress`（默认）/`done`/`blocked`/`failed`/`question`（M13）。
- 错误码：新增 `empty_post`；沿用 `invalid_plan`、`conflict`。不新增操作名。
- 内部函数（块内，列出供对齐）：`war_room.route(conn, notice) -> ('goal', goal_id) | ('main', None) | None`；`war_room_worktree.ensure(conn, home, room_id)`、`release(conn, room_id, session_id, now)`、`sweep(manager, conn, now)`、`path_for(conn, execution) -> {'path'} | {'pending': True} | None`；`war_room_watchdog.note_turn(room_id)`、`tick(manager, conn, live, now)`。

### 4.4 人格

全部走 `/api/op/<name>`，注册在新文件 `plugin/crew/ops/domains/persona.py`（人格块新建，同时在 `ops/domains/__init__.py` 登记导入）。

| 操作 | 参数 | 返回 / 错误 |
|---|---|---|
| `persona.get` | `{session_id}` | `Persona` |
| `persona.save` | `{session_id, content, expected_version}` | `Persona`；`conflict`、`persona_too_long`、`persona_not_applicable` |
| `persona.seed` | `{session_id, seed, seed_text?, expected_version}`，`seed` ∈ `natural`/`crisp`/`gentle`/`dry_humor`/`candid`/`blank`/`custom`；`custom` 必带 `seed_text`（≤1000 字） | 同上 |
| `persona.reset` | `{session_id, mode:'seed'|'clear', expected_version}` | 同上 |
| `persona.freeze` | `{session_id, frozen: bool}` | `Persona`（不增版本、不进历史） |
| `persona.restore` | `{session_id, version, expected_version}` | 同上 |
| `persona.history` | `{session_id, before?, limit?(1–50)}` | `{history:[{version, action, actor, summary, tokens_estimate, created_at, content}], next_before}` |
| `persona.grow` | `{session_id}` | `{started:true}`；`persona_disabled`、`persona_nothing_new`、`persona_busy`、`persona_frozen` |
| `persona.list` | `{project?}` | `{enabled, items:[{session_id, name, project, subject, version, percent, frozen, last_growth_at, growth_error}]}` |
| `settings.save` / `settings.get` | 新增字段 `persona_enabled: bool` | 现有返回 |

`Persona` = `{session_id, subject:'leader'|'workspace_assistant', enabled, seed, seed_text, frozen, content, version, tokens_estimate, limit:20000, percent, estimate:'est-v2', last_growth_at, last_growth_summary, growth_error, growing, pending_turns, presets:[{key,name,summary}]}`。写入后 `change_feed.touch(conn, 'persona', project, session_id)`，人格块负责在 `change_feed.py` 登记这个 kind 和作用域，推送只带元数据。新守则 key `persona_frame`、`persona_growth`、`persona_off` 加进 `behavior_prompts.SPECS`，不进任何 `BUNDLES`。

### 4.5 安卓与电脑端

- `GET /api/access/context` 远程分支新增 `host_name`、`instance_id`（同 mDNS TXT `id`）。
- mDNS：服务类型 `_crew._tcp.local.`，实例名 `<电脑名> Crew`，SRV 端口为远程监听实际端口，A 记录为实际绑定 IPv4（`0.0.0.0` 时为每个可用非回环网卡），TXT `v=1`、`id`、`name`（≤63 字节）。`remote_access.get` 快照新增 `discovery_advertising: {active, error}`。
- 通知流 `GET /api/notifications/events?after=<seq>`：本机、browser 和 desktop 设备可用（加进 `remote_routes.TRANSPORT_GET`）。帧 `id: <seq>` + `data: {"seq","type":"notification","item_id","kind","project","title","body","url","created_at"}`；无游标首连 `{"type":"ready","seq":<最大 seq>}`；`after` 小于保留最小 seq 减 1 时先发 `{"type":"gap","oldest":N}` 再补发；空闲 15 秒心跳；设备撤销或远程关闭时断流（发送前和空闲期间都复查）。
- `chat.send` 的 `input_mode`、`heard` 见 4.2；`voice_turn` 见 M46。
- 安卓内部数据（已配对电脑、按实例隔离的凭据和游标、语音会话）由安卓块自定，不影响他块。

## 5. 数据迁移

- 新表都是 `CREATE TABLE IF NOT EXISTS`，旧库下次连接即建，无回填。
- `war_room_idle`（M19）：从基础 DDL 删掉；迁移在事务里持锁重查旧表存在，存在才 `UPDATE leader_notices SET wake=0 WHERE id IN (SELECT notice_id FROM war_room_idle WHERE notice_id IS NOT NULL) AND sent_at IS NULL`，再 `DROP TABLE`，一起提交。
- 升级前已批准的作战中房间：不补建 worktree，成员按共用目录处理，`isolation='none'`（原因“升级前开的房”），不做范围校验和越界检查（旧计划没有范围栏）；旧版 `legacy=1` 的历史房照旧只能收尾。
- 人格：老库没有 `persona_settings` 行 = 关；没有 `personas` 行 = 空白。
- 要验证的库形态：空库；旧结构测试库（参照 `test_war_room_plan.py`）里 `war_room_idle` 为空、有已发和未发提醒；有作战中的旧房；连续打开两次；两个连接同时打开；撤销提醒和删表之间失败后重开。正式库只在可写的隔离副本上验证（`store.connect` 会建表迁移），正式 HOME 不碰。

## 6. 施工拆分

阶段 0 已由总 leader 提交到集成分支 `claude/batch-1129-259f96`：4.1 六张表；4.2 全部钩子和空模块（`war_room_worktree.py`、`war_room_watchdog.py`、`persona.py`、`persona_growth.py`）；`chat.send` 两个参数和 voice 准入；M55 删除旧语音原型；`web.py` 登记 `persona-ui.js|css` 并建占位文件；本规划书。各块把阶段 0 提交合进自己的分支后开工。

| 块 | M 条 | 模型 | 拥有的文件（只改这些；同文件分段见下） |
|---|---|---|---|
| 作战室 war-room | M1–M25 | astra（路由、worktree、看门狗）、sol（守则后端）、Opus CLI 或 astra（界面，裁决 24、25） | `war_room*.py`、`war_room_worktree.py`/`war_room_watchdog.py` 的实现、`ops/domains/war_room.py`、`goal_runtime.py` 的 `target()` 和 `sync` 回调、`workspace_execution.py`、`branch_claims.py`、`chat_journal.py` 的回合钩子、`channel.py` 房内分支、`chat_delivery.py` 的主会话认领段（约 `:270–340`）、`codex_runtime.py` 的原生审批目录复核段（约 `:540–560`）、`goals.py` 终态调 `release`、`web_service/workers.py` 的 `deliver_notices()`、`schema.sql`/`store.py` 删 `war_room_idle`、`prompts/roles/leader.md`/`workspace_assistant.md`/`git.md`、`frontend/war-room.js|css`、相关测试 |
| 人格 persona | M26–M39 | astra（注入、成长）、sol（存储接口）、Opus CLI 或 astra（界面，裁决 24、25） | `persona.py`/`persona_growth.py` 实现、新 `ops/domains/persona.py` 和 `domains/__init__.py` 导入、`ops/domains/assistant_cover.py`、`change_feed.py`、`settings.py`、`web_api/admin.py`、`ops/domains/admin.py`、`web_service/workspace.py`、`operations_chat.py`、`session_ops.py`、`behavior_prompts.py` 的 SPECS、`chat.py` 启动拼接和首条输入、`codex_runtime.py` 的 `developerInstructions` 段（约 `:262–290`）、`chat_delivery.py` 的 `_send` 守则重发段（约 `:81–100`）、`web_service/workers.py` 的 `_poll_notices()` 和服务 stop/close 登记、`plugin/prompts/persona_*.md` 和 `personas/`、`frontend/persona-ui.*`、`member-ui.js`、`settings-ui.js`、`index.html`、`assistant.html`、`assistant.js` 的页头入口、`desktop/src/home-onboarding.*`、`desktop/src/main.cjs` 的设置资源注入、相关测试 |
| 安卓 android | M40–M57 | astra（语音、确认判定）、sol（电脑端接口、壳、通知）、Opus CLI 或 astra（界面，裁决 24、25） | `android/` 全部、`docs/ANDROID.md`、`docs/VOICE_FEASIBILITY.md`、新 `discovery.py`/`device_notifications.py`、`plugin/vendor/`、`frontend/vendor/qrcode-generator*`、`remote_access.py`、`remote_routes.py`、`notifications.py`、`web.py` 的通知流路由和二维码静态登记、`web_api/sessions.py` 的语音前缀和 `heard` 校验、`ops/executor.py`、`ops/confirmations.py`、`hooks.py` 的语音回合工具前拒绝、`store.py` 的 `interaction_requests` 证明列迁移（如需要）、`frontend/remote-access-ui.*`、`assistant.js` 的输入栏语音按钮、`chat.js` 的语音标记、`mobile.css`/`mobile-chat.css` 相关选择器、相关测试 |
| 尾巴 tails | M58–M63 | astra（架构图触屏、线程归档）、sol（测试、文档）、Opus CLI 或 astra（审美细节，裁决 24、25） | `frontend/session-library.js`、`mobile-pages.css` 的 `.sl-categories`、`workspace-home.css` 的推荐标签、`plugin/tests/test_install_launchers.py`、`frontend/architecture.*`、`canvas-core.js`、`chat_lifecycle.py` 的关闭原因、`codex_runtime.py` 的关闭/归档段（约 `:740–780`）、`codex_visibility.py`、`native_origins.py`、`docs/CODEX_INTEGRATION.md`、`docs/FEEDBACK_REPORT_2026-11-28.md`、`AGENTS.md`、相关测试 |

- **同一文件分段归属**：`chat_delivery.py`（作战室：主会话认领段；人格：`_send` 守则重发段；`send()` 里的共享调用点已在阶段 0 落好，各块只实现钩子函数）、`codex_runtime.py`（人格：启动参数段；作战室：审批目录复核段；尾巴：关闭归档段）、`web_service/workers.py`（作战室：`deliver_notices()`；人格：`_poll_notices()` 和 stop/close）、`frontend/assistant.js`（人格：页头入口；安卓：输入栏语音按钮）、`store.py`（作战室：删旧表；安卓：确认证明列，各自独立函数）。各块只改自己的段，合并冲突由总 leader 按意图解决。
- `plugin/tests/ci_core.txt` 各块都可追加新模块，合并时总 leader 合并两边。
- 跨块契约要变：找总 leader 改本规划书，再通知受影响的块。
- 块内测试只跑相关模块；全量测试、完整截图矩阵、作战室和人格的多轮真 AI 实测放在集成验收。块内真 AI 实测只在本块就是 AI 调用链本身时做：作战室（守则与看门狗各一轮冒烟）、人格（注入和成长各一轮）、安卓（语音链路）、尾巴（线程归档）。
- 施工顺序建议（块内）：作战室先做执行目录解析和启动闸门，验证通过再接自动清理，最后才删旧守护、切换看门狗；安卓先做语音回合绑定和确认回放，再接原生录音；人格先做存储和注入，再做成长。

## 7. 验收

| 项 | 判定 |
|---|---|
| worktree 隔离 | Git 工作区批准计划后，标了 `独立目录` 的成员有独立 worktree 和分支，没标的不建；CC 和 Codex 的实际 cwd、Codex 原生审批都用它；一个成员两条分工、一次改派、中途重启都对；非 Git 工作区标独立目录被拒、领头收到“没有隔离”；升级前的旧房不换目录。 |
| 共用目录 | 分工缺「文件:」、共用目录成员范围重叠时计划被拒并指出行号；界面标“未隔离”并显示范围；收工时越界改动（工具调用或 git diff 两个来源）记 `out_of_scope`、叫醒领头、进「需要你关注」，范围内的不报。 |
| 收工清理 | 已合并 → worktree 和分支都删；有排队申请 → 只删 worktree 记 `awaiting_merge`；未合并无申请 → 有备份引用、时间线有公告和恢复命令，照命令能取回内容；“已有合并申请但又新增了未提交文件”的快照被保住；房间放弃、叫停也走同一流程；没开 Git 管理时有明确提示；重启后能续。 |
| 叫醒参战 leader | 无分工、无执行的成员被 @ 后经主会话收到并回帖；`task_returned` 和看门狗通知也能送到无执行的领头；同一通知不会既走任务又走主会话；认领后退房的通知显示已取消。CC 和 Codex 各一遍，外部 CC 占用时按设计等待后送达。 |
| 守则后端 | 分工写领头、筹备期发卡、无草稿请顾问、空话帖各被拒并给出原因，空话拦截计数恰好加一；成员 `progress` 帖点名领头也不叫醒，`done/blocked/failed/question` 叫醒；状态板改 `doing` 不发通知。 |
| 看门狗 | 五条加阻塞等用户条件全部满足 60 秒后叫醒领头一次且只一次；有派工在跑（含早启动的）、有待投递、有人在忙、外部 CC 状态未知、等用户时都不叫；宽限内恢复则撤销；领头因看门狗而忙不清段；叫醒后无实质进展显示「需要你关注」，有实质进展后消失；派工结束或消息送达后由后备复核接上；旧 `war_room_idle` 已删，旧未发提醒不再唤醒。 |
| 真 AI · 作战室 | Codex `gpt-6.1-terra` low 与 Claude Code Sonnet low 各跑至少 3 次完整流程（开房 → 写计划 → 批准 → 两成员各改一处 → 申请合并 → **真 AI Git 管理员合并房间分支** → 关房）：两载体各至少 1 次是“一人独立目录、一人共用目录”的混合房（其余由领头自己决定，记录它的选择和理由）；统计每人发帖数、空话被拦次数、三类偏差尝试次数、越界检查结果、看门狗叫醒次数、借主会话回合在主目录改文件次数、worktree 是否清干净、备份引用是否正确。 |
| 人格 | 引导开关（引擎不变也写入）和设置两条写入口都生效并读回；leader A、B、工作区助手各一份不串；总对话启动含人格块，执行、顾问、成员不含（测试断言）；上限正文在 Windows CC 首次启动和恢复都成功；运行中编辑下一次输入带一次新块；超 20k 估算（含空白、emoji、代码、中英混排）的各种写入被拒；成长期间用户编辑丢弃候选；定型后不成长；历史 50 版与恢复可用；自写起点能重置回去；全局助手和 MCP 读写不到人格；人格不进工作区包导出。 |
| 真 AI · 人格 | 同一问题两种起点风格可辨；同一小任务开关人格结果一致、测试都通过；开房后领头房内帖不带性格口吻（人工判读并记录）；手动成长一次，新正文 ≤ 上限、摘要合理。CC 与 Codex 各一遍。 |
| 安卓 · 配对 | 电脑配对码旁有二维码；手机相机扫开网页登录自动填码；安卓壳扫码直接进入工作台，设备列表出现该手机；强制下线后 WebView 和通知流都回配对页；切换电脑不串凭据。 |
| 安卓 · 通知 | 待拍板、人工待办、最终交付、卡住四类在手机后台收到系统通知，点开进对应页；“我已离开”关着也收；断网 2 分钟再连补齐不重复；mDNS 在本机能被发现，关局域网访问后注销。 |
| 安卓 · 语音 | 只在安卓壳内的工作区助手页出现“语音”按钮，系统管理员页和普通浏览器没有；说话 → 字幕 → 朗读；朗读中开口立即停播，下一句助手知道只听到一半；语音里派活出确认卡、点确认才执行且只执行一次、说“确认”不生效，语音和文字交错发送也不串；CC 语音回合不能直接改文件；聊天记录里有“语音”标记、显示原话；断开外网仍能在局域网配对和对话，与电脑断连时只有端侧识别和朗读可测；APK 路径、大小、SHA-256 写进交付；`docs/ANDROID.md` 是给用户的一页说明（侧载安装、扫码配对、开通知和后台保活）。 |
| 尾巴 | 7 条审美细节（M25 四条、M58 两条、M39 一条）各有浅色宽屏和深色约 375px 截图；3 个启动器用例在 Windows 跑通；375px 架构图在配对的 browser 身份下能加 part、改名、移动、连线、回滚；Crew 服务关闭和用户停止后 leader 总对话线程从 Codex 默认列表消失、续用前解档同 ID，载体切换不归档；`FEEDBACK_REPORT` 无 `Temp/` 链接；画布单测和 CDP 实测数字进交付。 |
| 提醒渠道与离开判定 | 新装或从没选过渠道时默认 Lark，已手动选过 QQ 的升级后仍是 QQ；Windows 上 1 小时无键鼠输入后判为离开、有输入后恢复（可注入假空闲时长测），锁屏和手动开关照旧；Mac 分支有测试覆盖（真机留到出包）。 |
| 集成 | 全量测试（Python、网页、桌面）与上批基线比无新增失败；4 个冒烟按改动范围跑；改到的界面浅色宽屏 + 深色约 375px 截图，布局随宽度变化的加 760px，交 Claude Code CLI Opus 看图挑刺一轮（裁决 25，读截图绝对路径），总 leader 抽看关键图。客户端覆盖本机 Electron、远程桌面、配对浏览器、安卓 WebView。本批交付报告无 `Temp/` 和仓库外链接。 |
