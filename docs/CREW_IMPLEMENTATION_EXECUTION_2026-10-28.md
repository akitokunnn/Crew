# Crew 完整改造执行记录

任务来源：[完整任务书](CREW_FULL_IMPLEMENTATION_TASK_2026-10-28.md)。本记录持续更新，未完成项不代表已交付。

## 工作区全量更新后的接续

2026-10-28，用户要求先全量更新工作区，并确认其他 AI 已暂停写入。`C:\Tools\Crew` 已在原分支 `codex/crew-update` 快进到 `c304edf922ed34704c902bb8c57a1e854d805d3a`，与当时最新 `origin/main` 一致；桌面源码版本 `0.2.41`，已包含模块化重构。以下其他章节保留更新前的记录，当前基线和接续以本节为准。

- 更新前 139 个本地文件已保存至 `C:\Tools\Crew-update-backups\2026-10-28-665077f0\final\files\`；上级 `manifest.json` 保存文件归属、SHA-256、旧 HEAD、目标及 stash 信息，另有 tracked/staged patch。
- Git stash 对象为 `3e4f27e5f330a34959aecba3cae779531b86231c`，保留引用为 `refs/crew-backups/workspace-update-20261028-4d86d0a1`。备份根目录 `RECOVERY.md` 有恢复说明；不要直接把整个 stash 应用到新主工作区。
- 原基线上的任务域、调度、提示词、远程接入、快照及相关测试等未提交施工代码已封存，未覆盖当前新版源码。先核对主干实际能力，再按 `docs/architecture/README.md` 的领域、`web_api/`、执行宿主和 UI 模块接口逐项迁入有效改动。
- 任务书、产品需求、交互提案、共享接口草案、执行记录和 `docs/fixtures/crew-api.json` 已恢复。接口草案的 `implementing` 表示待接续；`existing` 也需结合新版源码复核，不代表本轮验收已通过。
- 原有隔离测试目录 `plugin/tests/tmp9aqcda18/` 中有被占用的数据库及 WAL/SHM，保持原位；没有停止其进程。正式 Crew、用户 home 和其他工作树未执行更新或重启。
- 本次只更新源码基线，未构建新安装包或升级正式运行实例。更新后的定向验证结果在本节补记，不与旧基线的施工验证混用。

更新后定向验证：模块依赖方向 4 项、真实 HTTP 功能登记与假载体运行模块 19 项、前端动作/渲染/对话工作区/人员/模型目录及桌面版本一致性 32 项通过。Python 首次按包名载入时两个测试模块缺少测试目录搜索路径；补齐测试目录后载入并通过，未修改产品源码或测试断言。桌面依赖检测通过，Electron `45.0.0`、electron-builder `26.15.3` 与当前 package 声明一致。未跑全量回归、桌面交互或重新打包。

直接接续：核对本节基线 → 阅读模块指南 → 从备份提取有效增量 → 更新共享接口与实现状态 → 继续原任务。避免重新封存工作区或重复执行基线迁移。

## 新基线持续施工状态

本节对应模块化基线上的新增实现，覆盖下方更新前记录；未与旧基线测试混用。当前 HEAD `4294977`，桌面/插件源码已同步升为 `0.2.42`；后端阶段产物已构建、隔离验收，准备提交及合入最新 CI 基线。本轮尚未合并，正式 Crew 和用户 HOME 未更新。

收拢时再次 fetch，主干仅增加原型间距与置顶导航 PR #59/#60，已无冲突快进到 `4294977`；本轮后端增量保留，未编辑原型或 `plugin/crew/web/`。前端尚未交付真实业务接入，不将原型更新当作联调完成。

- 按新版架构迁入 `tasks/task_schema/task_actions`、持久 `task_scheduler`、`behavior_prompts`、分层 settings、`personal/personal_actions`、`task_artifacts`、`task_notifications` 和存储扩展。HTTP 全部在 `web_api/{tasks,behavior,personal,access,service_control}` 明确登记，未复制旧版 task_api 或 web 业务分支。
- `store` 只加载同库扩展 DDL，不反向导入领域；任务历史映射在任务领域按需补齐。board 公共事务支持嵌套 SAVEPOINT，外层失败不会遗留新 leader/半个任务。
- 已核实原生新主干能并行总对话与多个目标，保留该能力，不迁回旧版的自动猜目标/抢占身份逻辑。
- 任务 mode 进入实际拍板路径，CC CLI/Codex 先绑定当前 task 再判断自动答复。旧组 trust 不覆盖另一 task。任务详情/持续报告/成果版本和排程均有原生 action 入口；写入有 CAS/请求 ID，用户原文保留。
- 未授权讨论由真实原生权限限制；原生 model/effort 与能力目录来自配置、用户选择及发现/执行证据。设置为 next_launch，Crew 提示词为 next_turn；未获本次加载证据不称已加载。
- 个人灵感、精确操作键的多任务 TODO、固定全局助手已接同一业务层。助手跨任务修改从真实用户 journal 取原话；原生工具不接受模型自述来源。个人全局聊天不随锚点工作区包导出。
- 持续报告 CAS 冲突保留待应用正文；附件与阶段文件真实字节进入既有对象机制。普通目录/Git 目录恢复均先比对当前内容，保护后续修改，不 git init/reset。
- 新记录及报告/附件/文件版本、个人事项/TODO 关系已接 crewpack 导出导入；跨工作区仅携必要引用/明确共享操作切片。导入不恢复执行或 native binding。
- 程序故障最多两次自动恢复；通知按 event×channel 记录，成功不因另一渠道失败重发，unknown 不盲重放。已接真实服务 notice tick，并在 task detail 返回每渠道结果。
- 远程 HTTPS origin 允许列表、登录 cookie、撤销、敏感设置重新核验与代理 Host 边界已接。隔离代理可对话/控制/读附件/SSE；未配置真实域名、账号或上线。
- 桌面默认退出改为 disconnect；显式停止后台才 shutdown；保留 smoke 清理及借用服务保护。独立后台的同 PID/instance/URL 与重连复用已有真实进程证据，尚未操作 Electron 退出界面。
- 原遗留隔离测试 Python PID 73740 经命令行精确确认后已停止，只释放它自己占用的临时库；`plugin/tests/tmp9aqcda18/` 尚未清理。未停止正式或其他 AI 进程。

### 当前验证证据

所有新基线命令先清理测试进程继承的 CREW_SESSION_ID/CLAUDE_CODE_SESSION_ID，使用临时 HOME、原生数据源和假载体。

| 验证 | 实际结果 |
| --- | --- |
| `python -m unittest tests.test_tasks -v` | 10 项通过：普通目录、稳定关联、并发编辑、迁移、两任务不同决策模式及停止后的模式切换 |
| `python -m unittest discover -s tests -p test_task_http.py -v` | 最近完整 16 项通过：真实 HTTP、原生报告/next step、固定助手、认证代理/SSE/附件、等待期撤销、运行垃圾箱/恢复、TODO 图片及成员原文转达。另新增 90 MiB 头部即拒绝测试单项通过，待汇合复验 |
| `python -m unittest tests.test_remote_access -v` | 7 项通过，合成 origin，未连接真实隧道 |
| 独立执行/原生定向 | A37 scheduler 30 项，执行 278 项 / 53.752 秒及最终受影响 87 项 / 14.221 秒通过。包含有上限的缺计划核对，成功步骤不重放。早先一次正式端口守卫 7122 报错尚无确定原因，诊断组未复现；最终回归仍保持守卫 |
| 独立存储/通知定向 | 报告快照与既有存储 74 项通过（Windows 无 symlink 权限 1 项跳过）；新增真实存储往返 7 项、通知 7 项含实际服务 tick 通过 |
| 独立设置/个人领域 | 关联、标签、个人原生工具与真实资料读取已补，173 项受影响回归通过；attention 新接口与旧 state 兼容 85 项通过。角色默认偏好在最后收拢复验 |
| `node --test @(rg --files plugin/tests -g 'test_*.js')` | 网页 299 项通过；未改前端文件，不能据此称新 UI 联调通过 |
| 新 desktop detached lifecycle | 4 项通过：Node 宿主自然退出后实际 Supervisor/Crew/假载体后续 work 继续，同服务重连，显式停止/借用保护。最长场景约 4.9 秒，不声称整夜测试 |
| 完整 Python 回归 | 最终汇合 169 模块/2346 项/195.14 秒；4 个旧规则/夹具差异修正后，受影响 143 项复验通过，9 平台或 opt-in 跳过。随后仅新增能力做定向，详见下段 |
| `npm --prefix desktop test` | 172/172 通过（8.288 秒）；随后桥接 core/main 53 项、新 custom 来源桥接 1 项通过；元数据版本同步 1 项通过 |
| `node desktop/scripts/smoke-workspaces.cjs` | 41 项真实隐藏 Electron 检查通过，截图/证据在 `desktop/smoke-output/workspaces`；均为既有 UI 回归，不代表新 UI 联调 |

最终后端收拢回归：复用已只读检查的并行 CI 运行器（来源 PR #61 `819a1be`，未接管该 PR），在当前目录以 4 个隔离 Python 进程执行全部 169 模块、2346 项，195.14 秒结束，9 项平台/显式 opt-in 跳过。4 个失败模块分别为提示词缺少原有“不自动建目标”明确表述、两个 Controller intent 旧夹具不适配，以及旧收编测试按固定 effort 名字判断非法。已补提示词原约束、严格 work 意图断言、非法格式夹具和保留原生未来 effort 的正例；只重跑受影响 4 模块 143 项，27.058 秒全部通过。没有把首次全套记作单次全绿；其他 165 模块无需重复执行。日志为 `Temp/full-implementation/python-regression-final.log`、`python-regression-contract-recheck.log`。本轮未出现先前的 7122 守卫异常，不能据此宣称其原因为随机端口。

最终桌面单元回归 `desktop-regression-confirmed.log`：172/172 通过，8.288 秒。真实 Electron 正常退出另行验证；不能用单元测试替代 A30。网页旧 JS 299 项仍是此前同前端代码的有效验证，未因后端收拢重复全跑。

### 当前明确待办与接续

1. 提交已验证的后端冻结版本，再正常合入 `origin/main` 的 `f97e3e6` CI 测试夹具优化（PR #61 已由负责会话验收合并）；保留双方 RPC 修复并复验受影响夹具。临时四进程运行器不是最终 CI 方案，不再把它当仓库入口。
2. 前端正式施工/联调中，见下方工作树及服务归属。等其可追溯提交后整合桌面服务根 URL 入口、A33/A34/旧入口联合验收、最终构建和正常 PR 合并。
3. 公网仅余用户账号/域名和最终真实 HTTPS 入口验收，当前未上线。部署代码、配置检查和隔离代理已有，不以假链接交付。

角色默认收拢已过：配置只读不探测 CLI，未配置 leader/member 偏好 CC/Codex，显式/per-leader/作用域优先，已有执行不重配。51 项基础验证通过；265 项扩展首轮仅并行施工时回执缺字段，接点补齐后相关 14 项通过；执行审计另 123 项通过。来源/首选缺失原因已进入实际 send receipt、journal 和 board audit，`loaded=not_confirmed`；原生模型/effort 不硬编码。

已完成的收拢：终态任务普通问答复用只读原生执行，原轮次/需求/报告不变；停止和异步垃圾箱等待真实 reader/进程确认。个人事项任务关联、标签、原生同操作合并及固定助手跨项目资料读取已通过真实链路。导出稳定 request_id、跨服务重建下载、缺失/损坏原产物真实错误已通过；导入历史通知跨绑定 generation 不重发，隐藏任务不新发，新的用户操作仍通知。task pin 是可携带展示偏好，不进入调度优先级。

真实 Electron 退出已修：普通退出的 quit→OS 进程退出实测约 89 毫秒，三工作区/五次隐藏恢复/销毁后 12 次回调场景约 118 毫秒；后台保持相同 PID/instance/URL，重连复用后由测试自己的管理员明确停止。根因未归于应用名或某个 Socket；这些假说被反证。修复是在 Electron 与所选 Supervisor 间使用本版短命管道桥，EOF 只结束桥，明确 shutdown 才等真正管理员退出。新桥 5 项和 supervisor 33 项通过。新 `node desktop/scripts/smoke-background.cjs` 对 0.2.42 源码实际验证零模型、正常退出、同服务重连、仅回收自有后台，证据 `desktop/smoke-output/background-source.json`。

### 后端阶段 0.2.42 产物

`npm --prefix desktop run build` 成功，目录程序、ZIP、portable、setup 均生成。新 UI 尚未整合，这些是后端阶段包，最终前端合流后必须重新构建并更新校验值；不作为已发布版本。

| 产物（`C:\Tools\Crew\desktop\dist`） | SHA-256 |
| --- | --- |
| `Crew-0.2.42-win-x64.zip` | `5187e52fe0e465942034e6fcf683d72d43e3cdb40671bce434e1f0307e8fedf0` |
| `Crew-0.2.42-portable.exe` | `050563563e9ea39638b276a9c08d84d560b46784e43d402e2a3439bd650c8809` |
| `Crew-0.2.42-setup.exe` | `96b778ca1d8fa425c82625b162a27cac70689742b4256f89230d94efe65d50f5` |

包内全部 291 源码/资源文件匹配当前检出，source digest `898bb8f89e89624268970e855374fd64e4d318313049cd5a8d95181af0609806`，见 `desktop/smoke-output/bundle-source.json`。目录程序、portable、setup 原生图标均校验通过；没有把安装器安装到用户系统。

目录程序与 portable 标准隐藏 smoke 均通过，实际 renderer 截图在 `desktop/smoke-output/{packaged,portable}.png`。另外用 computer-use 在两个实际打包程序的唯一隔离窗口按 Ctrl+Q，前端 OS 进程退出后真实后台继续，相同 PID/instance/URL 重连，随后由 runner 明确停止自有后台：`background-packaged.json`、`background-portable.json`。总观察 50.821 秒/22.311 秒包含人工式窗口核对时间，不能当退出耗时；零模型轮次。打包应用按 Electron 规则不注入 require/NODE_OPTIONS；早先使用外部 hook 的测试方式已纠正。二进制交互需 `windowsHide:false` 才显示其隔离窗口，绝不选择正式窗口。源码最后一次退出 stderr 有 Chromium GPU shutdown 日志，原文保留于证据；进程退出、同服务重连和守卫断言通过。

已修并复验的独立审查：停止不受更新准入拦截、已知重复启动拒绝不记 unknown、message_id 不参与图片路径拼接、旧 message_id 不解除手动停止/故障、SSE wait 期间撤销后不发下一批、TODO 图片携带及运行中助手垃圾箱。不要按旧待办重做。

清理限制：自有测试目录 `C:\Users\admin\AppData\Local\Temp\crew-detached-Pq4QYH` 的删除被自动审批以 “blocked by policy” 拒绝；未绕过删除，目录保留。相关自有夹具 PID 24856/27668 已核实退出，不影响施工或正式服务。

### A01–A37 接入核对表

下表记录后端已有证据和仍需联合验收的部分；“已有证据”不表示整项 UI/产物验收已经通过。最终回归、构建和前端联调仍按上方待办进行。

| 编号 | 后端证据入口 | 仍需完成 |
| --- | --- | --- |
| A01–A02 | tasks、task_http、task_api_fixture；普通目录与同 ID 多工作区 | 前端双入口与真实成果展示 |
| A03 | task_followup、codex_discussion、task_http；草稿与已交付问答均受原生只读限制 | 讨论/明确继续的 UI 文案与动作 |
| A04 | goal_http、runtime_integration、task_scheduler 的独立执行身份 | 两任务页面同时推进与置顶显示 |
| A05–A07 | tasks、trust_mode、goal_links、runtime_switch；任务 mode 与原生权限分开 | 集中拍板、模式/载体切换 UI |
| A08 | task_communication、mixed_messaging、goal_messages；重要原文只转达一次 | 成员/leader 导航、私聊入口 |
| A09 | codex_runtime、goal_http、chat；原生 steer 与队列回执 | 立即/排队 UI 状态 |
| A10–A13 | task_scheduler、goal_recovery、task_followup；停止墓碑、reader 排空及历史代次 | 两种停止按钮、重连 UI |
| A14–A15 | task_notifications 实际服务 tick，有限恢复/按渠道回执 | UI 展示失败原因与任务入口 |
| A16 | CC stream-json 与 Codex app-server 协议夹具、codex_discussion | 只测试假载体，不声称真实账号运行 |
| A17–A18 | settings_scopes、behavior_prompts、runtime_catalog_sources、task_followup | 设置完整接入、实际来源和生效时机展示 |
| A19 | goal_context、maintenance、context_http、storage_context_bridge | UI 上下文/来源可查看 |
| A20–A22 | personal、personal_native、storage_personal_extensions；关联、标签、固定助手证明及跨项目资料 | 开始聊导航、个人清单与助手页面 |
| A23–A24 | task_artifacts、task_http；持续报告 CAS、真实附件、认证归属 | 文档呈现、阅读位置与上传体验 |
| A25 | task_artifacts、task_http 的真实普通目录恢复 | 前端预览与冲突处理 |
| A26 | task_git_restore 的临时真实 Git 提交、index/reflog/独立修改保护 | 前端预览与冲突处理 |
| A27 | task_http、task_followup、personal；运行先停、异步垃圾箱、恢复不启动，隐藏通知已复验 | 垃圾箱 UI |
| A28–A29 | task_storage、storage_personal_extensions、storage_export_receipts、旧 storage 套件；268+1 项受影响通过，2 平台跳过；通知跨 generation 抑制及 pin 携带通过 | 前端导出/历史入口 |
| A30 | detached-lifecycle 假载体 1→2 输入；源码真实 Electron quit/后台存活/同实例重连均通过 | 包中同路径验证 |
| A31–A32 | remote_access、task_http 认证代理/SSE 撤销、消息/控制/恢复回执 | 前端登录/直达/重连，真实公网账号与域名 |
| A33–A34 | 服务 API 已提供，不代替界面验收 | 前端 PC/手机导航、键盘、草稿和阅读位置 |
| A35 | 网页旧 JS 299 项与工作区 Electron 41 项已过，完整回归待汇合 | 新布局保留所有旧入口与最终回归 |
| A36 | 后端阶段 0.2.42 全包构建、291 文件、3 EXE 图标、目录/portable 标准与正常退出隔离验证通过 | UI 合流后的最终重建及同样验收 |
| A37 | scheduler 30 项，可控时钟/事件；278 项执行组约 53.8 秒，后台两步实际约 4.9 秒 | 记录最终组合实际时长，不称整夜压力测试 |

前端正式实施位置已确认：`C:\Users\admin\.codex\worktrees\crew-ui-prototype\Crew`，分支 `codex/crew-formal-frontend`，基线 `4294977`。用户在该会话明确授权双方沟通，已直接同步 API 启动方式、task pin 和 attention 导航字段；其修改只在前端目录，最终集成由本任务负责。

前端自行持有的联调服务：临时 HOME `C:\Users\admin\AppData\Local\Temp\crew-frontend-api-5df03d168fd14f69a45647c5d1adaf32`，假载体 API `127.0.0.1:2957`，dev proxy `127.0.0.1:4321`。这是前端的测试数据和进程，root 不停止/清理它；不与正式服务混用。前端已报告 mock 导航/草稿及真实创建讨论接入，最终联调仍需核实实际提交和验收。

前端版本 capture 发现开发夹具项目原放 HOME 下，与正确的保护规则冲突；已只改夹具为 HOME 外 `crew-api-project-<UUID>`，metadata 增 `project_root`，退出保留项目。3 项真实 API 回归通过，含 capture/preview/restore 真字节和幂等，HOME 越界仍拒绝。2957 服务未停止，前端已自行用另一个 HOME 外普通目录继续验收。

## 更新前现场与归属档案

- 工作目录 `C:\Tools\Crew`，分支 `codex/crew-update`，基线 `b159fc5fc98f1d5b3d72a52ed26843f35ebb891d`，桌面源码 `0.2.7`。正式服务与产物未核实、未修改。
- 初始状态与 tracked binary diff 保存在忽略目录 `Temp/full-implementation/start-status.txt`、`preexisting.patch`。大量既有未提交改动包含 storage-v2、QQ/Lark、设置、通知、desktop 和网页；不得整包提交或覆盖。
- 当前只此 root checkout 含本次全部未提交前置成果，暂原地增量施工；其他 worktree 未改动。
- 前端 AI 拥有 `plugin/crew/web/`，后端不改这些页面、样式和客户端。desktop main/core/preload 有既有存储成果，需逐段核对后再衔接；首页展示归前端。
- 本轮根负责人：任务域与共享接口、HTTP、迁移、最终集成。内部独立分工：执行调度及原生链路；设置及提示词；报告附件和真实成果快照。均禁止真实 AI 唤醒、正式服务/库修改及自行提交。

## 现状映射与实施决定

| 能力 | 可复用真实入口 | 已确认缺口 / 施工方向 | 验证 |
| --- | --- | --- | --- |
| 普通工作区、任务身份 | board/workspace_settings、goals | 沿用 goal UUID；扩展控制/关联表，不另建任务库 | A01-A04/A28 |
| 两载体及成员、上下文 | chat/goal_runtime/codex_runtime/goal_context/goal_messages | 任务级 mode、显式队列、停止 tombstone、持续事件调度 | A05-A19/A37 |
| 设置 | settings/models/runtime_catalog | workspace 层、继承来源、可编辑提示词与实际加载记录 | A17-A19 |
| TODO/灵感/助手 | todo/goal_links/goal_actions | 同事项多 task 关联、全局原话、助手共用业务入口 | A20-A22 |
| 报告/附件/版本 | goals/documents、storage-v2 objects | CAS 持续报告、一般附件、真实快照与冲突恢复 | A23-A26 |
| 历史/垃圾箱/导出 | storage_service、legacy/projector/packages/activation | task 生命周期与新关系进入可携带链路；历史不启动 | A27-A29 |
| 后台/远程/桌面 | web/supervisor、desktop core/main | 独立服务核实、登录与代理信任、UI adapter及隔离包验收 | A30-A36 |

关键决定：`crew.db` 仍是运行单一事实；storage-v2 复用既有迁移投影和内容对象。task.id = goal.id。旧 goals 不直接加列（现 create 使用位置 INSERT）；用扩展表。旧 leader 信任值只保留来源，不能自动扩散为每任务授权。storage-v2 schema v2 有摘要校验，修改 schema 必须真实升级，不能直接改设计 SQL。

## 接口与进度

共享契约：[CREW_API_CONTRACT.md](CREW_API_CONTRACT.md)，可公开合成数据：[fixtures/crew-api.json](fixtures/crew-api.json)。第一批 task/control/settings 接口正在实施，existing 原生聊天与 history/SSE 可复用。状态与 ID 语义已写入契约。

## 验证及交付状态

已完成只读源码盘点与现场确认；尚未重跑功能测试，不宣称任何验收通过。尚无本轮新 commit/PR/构建产物，未合并，未更新正式服务。认可 UI 尚未由本负责人核实具体交付路径；前端可以按共享契约独立施工，不阻塞后端。

## 直接接续

1. 完成同库 task_controls/task_workspaces 和 tasks 服务，提供真实 HTTP + 合成历史验证。
2. 收拢独立模块 schema/init，接入设置提示词、持久调度、报告附件与快照。
3. 补任务模式决策、个人事项/助手和新数据可携带链路，再做隔离服务联调。
4. 前端交付后按 A01-A37 记录实际证据；完整构建及正常 PR 合并后才能标记完成。
