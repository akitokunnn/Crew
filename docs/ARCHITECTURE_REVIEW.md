# Crew 架构重审与多端演进方案（R0）

> 日期：2026-11-27 · 作者：架构重审主题会话（Claude）· 基线 `claude/spatial-studio-ui` `3fdfbec`（桌面 0.3.18）
> 姊妹文档：[巨石拆分边界](SPLIT_BOUNDARIES.md)（阶段二施工依据）、[操作清单与 MCP 设计](OPERATIONS_REGISTRY_DESIGN.md)（R5 施工依据，含真模型实测）、[入口全量盘点](OPERATIONS_INVENTORY.md)
> 参照：t3code `c2beadbc`（`C:\Tools\Crew-fixes\spatial-studio-ui\Temp\t3code`，只读）；盘点由 Codex `gpt-6.2-sol` 只读完成，关键条目主控抽查。
> 依赖口径：按[总计划](REBUILD_PLAN_2026-11-27.md)决策日志，默认零依赖；本文不引入任何依赖或构建步骤（理由见第 8 节）。

---

## 0. 结论

1. **底座不换。** Python 标准库 HTTP 服务 + SQLite WAL + 原生载体子进程 + 原生 JS 前端这套组合，对“单用户、本机主机、几台设备”的规模够用，而且零依赖带来的安装与升级稳定性是 Crew 的实际优势。问题不在 `BaseHTTPRequestHandler` 本身，而在**没有协议层**：路由是 600 行 if 链、动作没有登记、状态靠 3 秒全量轮询、写请求没有幂等、读接口里带写和全局锁。
2. **补一个协议层，分三件东西**：
   - **操作清单**（命令面）：所有改数据动作登记一次，HTTP / CLI / MCP 从它生成，写操作带 `request_id` 只执行一次（详见操作清单设计）。
   - **“快照 + 序号”事件流**（状态面）：工作区快照接口 + 按全局序号的 SSE 事件流，断线按序号补发，补不上回 `snapshot-required` 让客户端重拿快照；替换 3 秒全量轮询。对话日志已经是“历史 + 按序号 SSE”（`web.py:2120–2158`），只是把同一模式推广到工作区状态。
   - **统一的用户输入请求**（交互面）：Codex 原生审批、CC 的提问 / 计划确认、AI 发起的高风险操作确认，统一成一种结构，界面按 `kind` 渲染，不按载体名分支。
3. **多端客户端共享一套前端与协议**：网页与桌面继续是 `frontend/`（桌面 `home*` 并入），手机先做 PWA（现有窄屏适配 + 远程配对 + 事件流），通知先走已有的飞书 / QQ 通道；原生外壳与 Web Push 列为两个明确的后续拍板点。语音先用手机端系统语音识别 + 文本协议，不在主机处理音频。
4. **迁移按 9 步走，每步可单独合入、可验证**（第 7 节）；第一步就是已出的拆分边界，第二步是读路径卫生（立刻能减少卡顿）。

---

## 1. 现状速写

```
            ┌────────────────────────── 本机主机（Python 标准库，plugin/crew）──────────────────────────┐
 浏览器 /    │ ThreadingHTTPServer + BaseHTTPRequestHandler（web.py 2665 行）                            │
 Electron  ──┤   GET  /api/state  ← 前端每 3 秒全量拉取（app.js:1743）                                  │
 窗口 / 手机 │   POST 104 条路径 ← 600 行 if 链分派（web.py:1139–1732）                                 │
（远程配对）│   SSE  /api/chat/events?after=seq ← 每个对话的日志流（web.py:2120）                        │
            │ SQLite WAL（store.py:25–26，schema.sql 1042 行）  会话日志文件（chat.Journal，带 seq）     │
            │ 后台线程：进程轮询、通知投递、Git CI、用量扫描、维护任务（web.py:142–552）                 │
            │ 载体：CC 子进程 stream-json（chat.py）· Codex app-server JSON-RPC（codex_client.py）       │
            │ AI → Crew：Bash 调 crew CLI（cli.py，身份取环境变量）· Codex 动态工具（codex_tools.py）    │
            │ 守卫：CC hooks（plugin/hooks/hooks.json → crew_hook.py）· Codex shell 守卫                │
            │ 外部通道：QQ、飞书（自带标准库 WebSocket 客户端，qq.py / lark.py）                          │
            └─────────────────────────────────────────────────────────────────────────────────────────┘
 前端 frontend/：约 1.33 万行原生 JS/CSS，每个面板一个 IIFE（window.CrewX），app.js 1746 行持有全局状态，
                 innerHTML 字符串渲染（144 处），replace() 以整段 HTML 字符串相等判断是否重绘（app.js:215）
 桌面 desktop/src：Electron 壳 + 第二套页面 home*（约 1200 行：工作区列表、目录、健康、设置、更新）
```

---

## 2. 值得保留的设计

| 设计 | 为什么保留 |
| --- | --- |
| **零依赖 Python 后端** | 安装只需系统 Python；升级不碰包管理；Windows / Mac 同一份源码。任何框架带来的收益都抵不过这条（见第 8 节） |
| **SQLite WAL 领域模型** | 事务、并发读、单文件可备份；`goals.transaction`、`busy_timeout` 已用好。事件表、回执表直接加在同一库里 |
| **会话日志（Journal）带序号 + SSE 订阅** | 已经是“快照 + 序号”的雏形：历史接口给全量，事件接口按 `seq` 续传，带 `X-Crew-Latest-Seq` 头（`web.py:2133`）。工作区事件流照抄这个模式 |
| **原生载体子进程 + 宿主绑定身份** | Codex 工具由宿主对象绑定 session / execution（`codex_tools.py:315–358`），不信任模型传的调用者参数——这条原则搬到 MCP 令牌上 |
| **零依赖 Hook 垫片** | `crew_hook.py` 用系统 Python 接入 CC 生命周期与守卫，不要求用户装任何东西；只需把会话身份从环境变量改为写死在 hook 命令参数里 |
| **远程访问边界** | 配对码、设备 Cookie / Bearer、浏览器允许清单与桌面拒绝清单（`remote_access.py`、`remote_routes.py`）——手机端直接复用 |
| **个人便笺的 `request_id` 回执** | 已经实现了“写请求只执行一次”，推广到全部写操作 |
| **`frontend/` 独立目录 + `STATIC` 白名单** | 静态资源边界清楚，远程鉴权共用；拆 `app.js` 时沿用 |
| **“错误保留真实原因”的工程纪律** | 写进协议：错误码固定、原文透出，不做隐式回退 |

---

## 3. 病灶（带证据）

### 3.1 后端

| 病灶 | 证据 | 后果 | 处理 |
| --- | --- | --- | --- |
| **路由无登记、无元数据** | `_post` 600 行 if 链分三个加锁阶段，阶段由代码位置隐式决定；`POST_PATHS` 与 if 链分别维护；准入（`MODEL_WORK`）又是第三处 | 新增路由易漏登记；权限、读写、加锁无法检查；AI 与界面各有一套入口 | 拆分边界 W2 → 操作清单 |
| **读接口拿全局写锁并扫描** | `Service.state()` 一进来就 `scan_usage(live_dispatch=True)`（`web.py:647`），后者持 `write_lock` + `scan_lock` 扫 Codex 派工回执，每 60 秒再全量扫原生日志（`web.py:1944–1960`）；每个打开的页面每 3 秒调一次 | 所有写请求（发消息、点按钮）都可能排在别人的轮询后面——切页与操作“卡一下”的直接嫌疑；多开窗口、手机远程会成倍放大 | 迁移第 2 步：扫描移到后台线程，`state()` 只读 |
| **读里带写的 GET** | GET `/api/documents` 持写锁刷新索引（`web.py:1842`）；GET `/api/assistant/state` 会建助手；GET `/api/personal/tags` 补登记；GET `/api/usage` 先扫描入库；`crew_channel` 被当读却推进游标 | 重试、预取、远程只读设备都会改数据；读写权限无法按方法区分 | 清单规则“读操作只读连接执行”由测试强制 |
| **身份来自环境变量** | CLI 依次取 `--session`、`CREW_SESSION_ID`、`CLAUDE_CODE_SESSION_ID`（`cli.py:520` 等 12 处）；托管会话注入（`chat.py:1053`、`codex_runtime.py:292`、`goal_runtime.py:315`） | 子进程、派工脚本、测试都会继承身份；外部 CC 会话的 ID 能冒充 Crew 身份 | MCP 每会话令牌（操作清单设计第 7 节） |
| **同一动作多条实现** | 拍板答复 4 个入口、文档列表 4 个、收编 8 个（盘点第 7 节） | 修一处漏一处——正是 AGENTS.md “举一反三”反复强调的根源 | 清单一个动作一个处理函数 |
| **载体差异散落** | `chat.py` 中按载体分支 67 处；Codex 审批（`native_approvals`）、CC 提问拦截（`pre-ask` hook）、拍板各走一套 | 每加一种载体或一种交互都要改多处 | 统一用户输入请求（第 6.3 节）；载体适配接口（第 9 节，R4 时实现） |
| **巨石文件** | `web.py` 2665、`chat.py` 2135、`app.js` 1746 行 | 并行开发必冲突 | 拆分边界（阶段二） |

`BaseHTTPRequestHandler` 本身**不是**病灶：Crew 的并发是“几个页面 + 几条 SSE + 后台线程”，`ThreadingHTTPServer` 每连接一个线程完全够用。缺的是 WebSocket，而第 5 节说明它只在语音全双工时才需要，且有零依赖做法。

### 3.2 前端

| 病灶 | 证据 | 后果 | 处理 |
| --- | --- | --- | --- |
| **3 秒全量轮询** | `setInterval(loadState, 3000)`（`app.js:1743`）；每次拿整个工作区快照，再用整份前后快照比对决定音效（`notifySounds`，`app.js:1346`） | 空闲也在耗电耗流量；弱网下 3 秒一包全量；最多 3 秒延迟；后端每次重建快照 | 事件流（第 6.2 节） |
| **全局状态 + innerHTML 拼接** | 闭包内几十个 `let`（`app.js:11–45`）；144 处 `innerHTML`；`replace()` 按整段 HTML 字符串相等跳过，否则整块重建，再靠一串启发式恢复焦点、选区、滚动与输入法（`app.js:215–235`） | 局部更新做不到，状态一变整区重绘；IME、焦点问题要逐个打补丁；每处拼接都要记得 `esc()` | 不引入框架（第 8 节）。拆分后以“按实体的局部渲染函数 + 事件驱动”逐区改造：事件说清哪个实体变了，只重绘对应区域 |
| **两套前端** | `desktop/src/home*`（约 1200 行，工作区列表、目录选择、健康、设置、更新）与 `frontend/`（约 1.33 万行）各自实现列表与设置；UI 收尾主题正为两边分别统一视觉 | 同一功能两处维护，手机端无法复用桌面首页 | 迁移第 7 步：桌面首页改由后端提供的 `frontend` 页面渲染，桌面特有能力（选目录、更新、启动设置）经 preload 暴露的桥接调用 |
| **协议无类型、无共享定义** | 前端直接拼 URL 与字段名，后端字段改名靠搜索 | 手机端若另起客户端，字段漂移不可避免 | 清单 `params` / `result` 与事件 schema 生成 `frontend/protocol.js`（JSDoc 类型），零依赖 |

---

## 4. 与 t3code 对比

t3code（Node/TypeScript、Effect、React、React Native/Expo、WebSocket RPC）解决的是同一类问题：本机或远端“环境服务器”驱动多个 AI 载体，多端客户端连接它。

| 方面 | t3code 做法（证据） | Crew 现状 | 取舍 |
| --- | --- | --- | --- |
| 分层 | 服务端拥有载体进程、终端、Git、持久状态；Web / 桌面 / 手机共享 `client-runtime` 与 `contracts`（`docs/internals/01_architecture.md:10–16`） | 主机同样拥有一切；客户端只有一套网页前端 | **借鉴分层**：协议定义集中一处（操作清单 + 事件 schema），所有客户端照它走 |
| 传输 | `/ws` 上的 JSON Effect RPC，流式订阅；快照走 HTTP（`apps/server/src/ws.ts:3830–3903`、`environmentHttp.ts:775–799`） | HTTP + SSE | **不照搬 WebSocket RPC**：Crew 的推送是单向的，POST + SSE 足够，且标准库可直接实现 |
| 命令幂等 | 客户端生成 `commandId`，服务端 `orchestration_command_receipts` 以它为主键，重复则重放回执、带 `replayed: true`（`Orchestrator.ts:16531–16569`、`002_OrchestrationCommandReceipts.ts:8`） | 只有便笺有 `request_id` | **借鉴**：`op_requests` 回执表，见操作清单设计 5.5 |
| 快照 + 序号 | 事件带 `sequence`；客户端用 `snapshotSequence` 作 `afterSequence` 续订；超出 1000 条或已裁剪则回 `snapshot-required`（`ShellStream.ts:45–65`、`ws.ts:1081,1385,1432`） | 对话日志有序号；工作区状态全量轮询 | **借鉴**：第 6.2 节 |
| 审批与用户输入 | `RuntimeRequest` 统一结构（`kind`、`status`、`responseCapability: live/message/not_resumable`、`decision`/`answers`），各载体适配器转换，界面按 `kind` 渲染（`orchestrationV2.ts:1427–1443`、`RuntimeRequestService.ts:118`） | 三套各自实现 | **借鉴**：第 6.3 节 |
| 权限穷尽 | `RPC_REQUIRED_SCOPES` 以 `Record<方法, scope>` 覆盖全部 RPC，测试比较键集合（`RpcAuthorization.ts:254`、`.test.ts:44`）；但单个编排命令与 HTTP 路由未做到穷尽 | 无 | **借鉴并加强**：清单每条必须声明范围，覆盖到 HTTP 路由、按 action 分派的入口、界面调用点（操作清单设计第 10 节） |
| 载体抽象 | `ProviderAdapterV2`：`getCapabilities`、`openSession` → 会话的 `ensureThread/resumeThread/startTurn/steerTurn/interruptTurn/respondToRuntimeRequest` 与类型化事件流（`provider-core/.../ProviderAdapter.ts:777–912`） | `chat.py` 与 `codex_runtime.py` 各自实现，载体分支散落 | **借鉴接口形状**，R4 时实现（第 9 节） |
| MCP | 自有 `/mcp`，每会话随机令牌存哈希，绑定线程与 provider session；`tool_search` 后 `list_changed`；Codex 侧把少数工具 `omit_tools_from: ["deferred"]` 直出（`McpSessionRegistry.ts:125–133`、`McpToolExposure.ts:56–80`、`CodexAdapterV2.ts:1747–1761`） | Bash 调 CLI + Codex 动态工具 | **借鉴令牌与 Codex 直出做法**；本会话实测两种载体都原生延迟加载 MCP 工具，因此不需要 `list_changed` 加载协议（操作清单设计 5.4、第 12 节） |
| 手机 | Expo + React Native，共享 `client-runtime`；直连 / Tailscale / 中继；扫码配对 + DPoP / Bearer；短效 WebSocket 票据（`apps/mobile/package.json:72–128`、`docs/internals/05_remote.md`、`04_environment-auth.md:28`） | 窄屏网页 + 局域网配对码 | **先不照搬原生栈**：PWA 复用全部现有前端；扫码配对可加（只是把现有配对链接画成二维码，零依赖） |
| 语音 | 手机端录音 + 系统识别（iOS Apple Speech、Android ML Kit）（`voiceTranscription.ios.ts:156`、`.android.ts:73`） | 无 | **借鉴**：语音识别在手机端做，主机只收文本（第 5.4 节） |
| 工程约束 | 协议改动集中在 `contracts`；持久事件跨版本可解码；离线缓存不得冒充在线；后端改动先写失败测试（`AGENTS.md:62`、`01_architecture.md:46`、`09_connection-runtime.md:35`、`15_testing.md:12–13`） | AGENTS.md 已有类似纪律 | **借鉴两条写进 AGENTS.md**：事件 schema 只增不改（旧事件可解码）；客户端断线时界面必须显示离线，不拿旧快照冒充在线 |

不借鉴的：TypeScript + Effect + React 全栈与 pnpm 单仓（引入构建与大量依赖，Crew 的零依赖与“改完刷新即生效”的开发方式会丢失）；中继服务（Crew 的远程是局域网，用户没有要求公网中继）。

---

## 5. 目标架构：主机 ↔ 统一协议 ↔ 多端客户端

```
┌──────────── 主机引擎（Python 标准库）────────────┐        ┌──────── 客户端 ────────┐
│ 操作清单 ops/  ── 命令执行器（鉴权、回执、准入）  │  POST  │ 网页 / Electron 窗口    │
│     │                                           │ /api/op│  frontend/ + protocol.js│
│     ▼                                           │◀───────│ 手机 PWA（同一前端）     │
│ 领域模块（board、goals、channel、chat…）         │        │ CLI（由清单生成）        │
│     │ 写成功 → 事件                              │  GET   │                         │
│     ▼                                           │/api/   │                         │
│ 事件日志 events（全局序号、有界窗口）+ 快照投影   │snapshot│                         │
│     │                                           │───────▶│                         │
│     ▼                                           │  SSE   │                         │
│ 事件流 /api/events?after=seq                    │───────▶│                         │
│ 用户输入请求（审批 / 提问 / 确认）               │        │                         │
│ 载体适配（CC、Codex；R4 再加）── /mcp（令牌）    │◀──MCP──│ AI 会话                 │
└─────────────────────────────────────────────────┘        └─────────────────────────┘
```

### 5.1 命令面：操作清单

见[操作清单设计](OPERATIONS_REGISTRY_DESIGN.md)。要点：一个动作一个条目；`POST /api/op {op, args, request_id}` 是所有客户端的统一写入口（迁移期旧 URL 作为条目的绑定保留）；AI 走 `/mcp`，令牌决定调用者；写操作回执去重。

### 5.2 状态面：快照 + 序号事件流

见第 6.2 节。

### 5.3 交互面：统一用户输入请求

见第 6.3 节。

### 5.4 语音

- **第一步（零依赖，R7 原型可直接做）**：手机端用系统语音识别（浏览器 Web Speech API；以后若做原生外壳，iOS Apple Speech / Android 系统识别，t3code 同此做法）把话转成文字，走现有 `chat.send`；回复用系统朗读（`speechSynthesis`）。主机不碰音频。
- **第二步（全双工，视 R7 结论）**：手机直接连实时语音模型服务，主机只负责签发短效凭据并把转写结果作为普通输入写入会话。主机仍不处理音频流。
- **只有在必须由主机中转音频时**才需要服务端 WebSocket：`qq.py` / `lark.py` 已有标准库 WebSocket 客户端分帧，服务端握手与分帧约 150 行，可零依赖实现。届时作为独立端点 `/api/voice/ws`，不与事件流混用。

### 5.5 手机端承接现有交互

手机端 = 同一份 `frontend/`（PWA），在 `<=760px` 断点下已有底部胶囊标签栏（`mobile-shell.js`）与手机专用样式。下表按交接文档第 4 节的硬门禁逐类说明；逐条验收以 [交互清单](INTERACTION_INVENTORY.md)（774 条，含 375×812 视口与粗指针条件）为准，手机端每推进一步都按该清单的手机视口条目勾验。

| 交互类（交接文档 §4） | 手机端承接方式 | 需要新增的 |
| --- | --- | --- |
| 上下文压缩小环 | 同一组件，窄屏已显示在头部；点击弹出确认改为底部弹层 | 弹层样式（窄屏） |
| 活动计时与输出滚动 | 数据从轮询改为事件流后更及时；计时仍以服务端时间校准 | 无 |
| 边看边聊阅读器 | 窄屏不分栏：阅读与对话两个标签切换，“正在看”chip 保留；选中引用用系统选区菜单 | 窄屏标签切换 |
| 输入框与富文本 | `/`、`$` 技能面板改为全宽底部列表；图片粘贴改为相册选择 | 移动端附件入口 |
| 团队与会话治理 | 拖拽排序在触屏改为长按进入排序模式（AGENTS.md 要求不能只靠悬停）；头像裁剪、换载体、删除确认沿用弹层 | 长按排序 |
| 审批、Git、想法墙、助手、设置 | 统一用户输入请求在手机上以卡片呈现（允许一次 / 拒绝 / 回答）；主机管理类设置（远程访问、存储、运维）对远程设备本就拒绝，手机端隐藏 | 请求卡片组件（桌面手机共用） |
| 通知 | 先用已有飞书 / QQ 主动通知；PWA 打开时事件流驱动页内提醒与音效 | 无 |
| 配对 | 现有配对码 + 链接；加二维码显示（纯前端绘制） | 二维码绘制 |

PWA 需要的 `manifest.json` 与 Service Worker 只做“可添加到主屏 + 离线显示‘已断开’页”，不做离线缓存业务数据（避免旧数据冒充在线，借鉴 t3code 的约束）。注意：Service Worker 要求安全上下文，局域网 `http://IP` 下浏览器不允许注册——因此“添加到主屏”在局域网 HTTP 下只能以普通书签形式工作；要完整 PWA 需要 HTTPS（自签证书或 Tailscale 证书），列为拍板点（第 8 节）。

---

## 6. 借鉴 t3code 的五个机制（落地规格）

### 6.1 命令带 ID 只执行一次

- 所有写操作参数必含 `request_id`（客户端生成 UUID）。`op_requests` 表主键 `(caller_key, request_id)`，记录参数哈希、状态（`running` / `done` / `rejected`）、结果、所产生事件的最后序号。
- 重复到达：参数相同 → 返回原结果 + `replayed: true`；参数不同 → `409 request_id_reused`；仍在执行 → `status: running`。被拒绝过的命令重放拒绝原因（t3code 同此）。
- 写成功的响应带 `seq`（该命令产生的最后一个事件序号），客户端据此判断“我的修改是否已在当前快照里”，不用再拉一次全量。

### 6.2 快照 + 序号增量推送

- **事件表**：`events(seq INTEGER PRIMARY KEY AUTOINCREMENT, at, scope, type, entity, data JSON)`，`scope` 为 `workspace:<名>` 或 `global`。写操作在**同一事务**里写业务数据、回执与事件（t3code `01_architecture.md:35` 同此），保证“看到事件 = 数据已提交”。保留最近 10,000 条或 24 小时，`pruned_through` 记录裁剪水位。
- **事件内容**：按实体给出新值（`{"type": "leader.updated", "entity": "<session_id>", "data": {...该 leader 在快照里的完整结构...}}`），删除给 `*.removed`。不用 JSON Patch：客户端按实体 ID 替换，简单且与快照结构一致。后台线程产生的变化（进程状态、活动阶段、用量）同样写事件，节流到每实体每秒最多一条。
- **接口**：
  - `GET /api/snapshot?scope=workspace:<名>` → `{seq, data}`，`data` 就是现在 `/api/state` 的结构（由投影函数生成，**只读、不加写锁**）。
  - `GET /api/events?scope=...&after=<seq>`（SSE，`id:` 为序号）→ 先补发 `(after, 当前]`，发一条 `sync` 标记，再接实时事件；`after` 早于裁剪水位、超前于当前、或差距超过 1000 条时，只发一条 `snapshot-required` 后关闭。
  - 对话日志 `/api/chat/events` 保持现有格式，作为 `scope=session:<id>` 并入同一信封格式（字段对齐，接口路径迁移期保留）。
- **客户端**：拿快照 → 记 `seq` → 订阅 `after=seq` → 按实体合并；断线重连用最后序号续订；收到 `snapshot-required` 重拿快照。**重连只恢复订阅，绝不重发写请求**；写请求的结果用 `request_id` 查询。连接断开期间界面显示“已断开”，不展示旧数据为在线状态。
- **音效与提醒**由事件类型驱动（如 `chat.reply_finished`），取代现在整份快照前后比对。
- 全量轮询删除，不保留“SSE 不通就退回轮询”的隐式回退：SSE 断开即显示断开并按退避重连。

### 6.3 审批与用户输入请求统一结构

```
interaction_requests(
  id, scope, session_id, execution_id NULL,
  kind        -- 'approval'（命令 / 文件 / 越沙箱）| 'question'（AskUserQuestion 类）| 'plan'（ExitPlanMode）| 'confirmation'（AI 发起的高风险操作）
  source      -- 'codex' | 'claude' | 'crew'：只用于把回答送回去，界面不据此分支
  native_ref  -- 载体原生请求 ID（Codex JSON-RPC id、CC hook 调用 ID）或操作清单的 request_id
  payload     -- 统一字段：title、detail、questions[{id, header, question, options[], multi_select, allow_custom}]、
                 approval{command?, paths?, reason}、operation{op, args, risk}
  response_capability -- 'live'（载体还在等，可直接回复）| 'message'（载体已不等，回答作为新消息送达）| 'not_resumable'
  status      -- 'pending' | 'answered' | 'expired' | 'cancelled'
  answer      -- decision（allow_once / deny）或 answers{question_id: [...]}，及回答人、时间
)
```

- Codex 越沙箱审批（现 `native_approvals`）、CC `AskUserQuestion` / `ExitPlanMode`（现 `pre-ask` hook）、操作清单 `risk=high` 的 AI 发起确认，全部写成这张表的行，并产生 `request.created` / `request.resolved` 事件。
- 回答统一走一个操作 `request.respond {id, decision | answers, request_id}`；后端按 `source` + `response_capability` 送回载体（这是唯一按载体处理的地方，在适配层里）。
- 界面只有一个“待你处理”组件，按 `kind` 渲染（桌面侧栏、手机卡片共用）。拍板（decisions）保留为独立业务对象（有讨论、补充、授权模式等生命周期），但在“待你处理”里以同一视图模型列出。

### 6.4 重连不重发写操作

已并入 6.1 与 6.2：客户端写请求只发一次；网络失败时用同一 `request_id` 查询结果（`op.status`），查不到才允许用户手动重试，并沿用同一 `request_id`。现有远程访问已有“断线不重发成功输入”的测试要求（AGENTS.md 交付证据规则），这里变成协议层保证。

### 6.5 权限穷尽声明由测试强制

清单条目构造时缺范围即报错；测试遍历“全部操作 × 全部角色”与权限表数据比对；HTTP 路由、按 action 分派的入口、界面与桌面的改数据调用点、CLI 叶子命令、MCP 工具列表都必须能对应到条目（操作清单设计第 10 节）。比 t3code 多覆盖了 HTTP 与界面调用点。

---

## 7. 分阶段迁移路线

每一步：可单独合入集成分支；有明确的验证；不依赖后续步骤才能正常运行。

| 步 | 内容 | 依赖 | 验证 | 对应总计划 |
| --- | --- | --- | --- | --- |
| 1 | **拆巨石**（纯搬移）：W1 → W2 → C1 → A1 | — | 拆分边界的核对脚本 + 全量测试差集为空 | 阶段二 1–3 |
| 2 | **读路径卫生**：`state()` 不再调用 `scan_usage`、不拿写锁，扫描只在后台线程；GET 的副作用（文档刷新、建助手、标签补登记、用量扫描）改为显式写操作或后台步骤 | 1 | 性能基线脚本：`/api/state` 耗时与并发写请求的排队时间改前改后对比；相关 HTTP 测试 | 阶段二 5 |
| 3 | **清单骨架**：`ops/` 包，W2 的路由表全部改为清单条目（处理函数不变），加覆盖测试与权限穷尽测试；`/api/op` 上线 | 1 | 覆盖测试全绿；全量测试差集为空 | 阶段三 R5 |
| 4 | **MCP 与令牌**：`/mcp`、令牌签发与吊销、CC / Codex 启动参数注入；hooks 身份写死在命令参数；删除环境变量身份、Bash 调 `crew`、Codex 动态工具、`/api/assistant/native-action`；生成 CLI；守则精简 | 3 | 操作清单设计第 12 节实验在产品端点复跑；Haiku 5.5 + luna low 真实会话跑通核心工具；grep 旧路径为空 | 阶段三 R5 |
| 5 | **事件流**：`events` 表、写事务内落事件、`/api/snapshot`、`/api/events`；`frontend/app/15-data.js` 改为订阅；删除 3 秒轮询 | 2、3 | 断线续传 / `snapshot-required` / 不重发写请求的单测；两个页面同时打开时操作互相实时可见的浏览器走查；空闲时网络请求数为 0（除心跳） | 阶段三“增量事件流” |
| 6 | **统一用户输入请求**：`interaction_requests` 表与 `request.respond`；Codex 审批、CC 提问 / 计划确认迁入；“待你处理”组件 | 5 | 双载体真实会话触发审批与提问并在桌面、窄屏各回答一次 | 阶段四前置 |
| 7 | **桌面首页并入 frontend**：`desktop/src/home*` 的列表、目录、健康、设置改为 `frontend` 页面，桌面专有能力经 preload 桥接 | 3（UI 收尾主题完成后） | `smoke-workspaces.cjs`；三视口截图 | 阶段一 UI 收尾的后续 |
| 8 | **前端全部改用 `/api/op`**，删除旧 URL 绑定；清单生成 `frontend/protocol.js` | 3、5 | 覆盖测试中旧绑定为 0 | — |
| 9 | **手机 PWA**：二维码配对、窄屏交互补齐（第 5.5 节）、通知走飞书 / QQ；语音第一步（R7） | 5、6 | 375px 真机或模拟器走查；远程设备权限测试 | R7 |

步 2 与步 3 可并行（不碰同一文件）；步 5 依赖步 2 的“快照只读”；步 4 与步 5 可由不同会话并行（一个在 `ops/` 与载体启动，一个在事件与前端数据层）。

---

## 8. 依赖决策

| 候选 | 能解决什么 | 不引入的理由 / 代价对比 | 结论 |
| --- | --- | --- | --- |
| 异步 HTTP 框架（aiohttp、FastAPI/uvicorn） | 高并发、WebSocket | Crew 并发量小，线程模型够用；桌面直接使用用户系统里的 Python 3.11+（`desktop/src/python.cjs`），引入后要在用户的 Python 里 pip 安装并管理版本，升级多一层故障面 | 不引入 |
| WebSocket 库 | 双向推送、语音 | 推送单向，SSE 足够；语音第一步不经主机；必要时标准库约 150 行可写（已有客户端分帧代码） | 不引入 |
| TypeScript / 构建工具 | 协议类型、编辑器提示 | 引入构建步骤，失去“改完刷新即生效”；用 JSON Schema 生成 JSDoc 类型（`protocol.js`）可得到大部分好处 | 不引入 |
| 前端框架（React/Vue/Preact、lit） | 组件边界、局部更新 | 一次性重写 1.3 万行风险极高，交互零丢失门禁难守；事件驱动 + 按实体局部渲染可渐进获得局部更新 | 不引入；拆分后逐区改造 |
| Effect 等函数式运行时 | 类型化错误与资源管理 | 只对 TypeScript 栈有意义 | 不引入 |
| `cryptography`（Web Push 的 VAPID 签名需要 ECDSA P-256） | 手机系统级推送 | 标准库没有 ECDSA；已有飞书 / QQ 主动通知可覆盖“离开时提醒” | **拍板点 1**：先用飞书 / QQ；用户要求系统推送时再评估 |
| HTTPS（自签证书 / Tailscale 证书） | 完整 PWA（Service Worker）、麦克风权限（浏览器要求安全上下文） | 证书分发与信任配置对用户有成本；**注意 Web Speech API 的麦克风权限同样要求安全上下文**，所以语音第一步在局域网 HTTP 下的手机浏览器里可能不可用 | **拍板点 2**：R7 原型阶段先在本机 `localhost` 与 HTTPS 隧道下验证；产品化前决定证书方案 |
| 原生手机外壳（Capacitor / Expo） | 后台音频、系统推送、系统语音识别 | 新增一整套构建链与商店分发 | **拍板点 3**：PWA 覆盖不了的需求（后台语音、锁屏推送）确认后再定 |

本次 R0 不引入任何依赖或构建步骤，按决策日志由主会话自行确认即可；拍板点 1–3 在相关主题（R7 语音、手机端）推进到那一步时再交用户。

---

## 9. 载体适配接口（R4 暂不做，只定形状）

参照 t3code `ProviderAdapterV2`，把 `chat.py`（CC）与 `codex_runtime.py`（Codex）中已有的能力收敛为一个接口，供以后接 Gemini / DeepSeek：

```
class CarrierAdapter:
    id: str                                  # 'claude' | 'codex' | …
    def capabilities(self) -> dict           # 模型 / effort / 速度档、是否支持中途引导、压缩、MCP、原生审批类型
    def open(self, session, spec) -> Running # 启动或恢复（现 _launch_native / CodexRuntime.start）
    def send(self, running, input)           # 一轮输入（现 _send / turn/start）
    def steer(self, running, input)          # 运行中追加（若 capabilities 支持）
    def interrupt(self, running)             # 中断本轮（现 interrupt）
    def compact(self, running)               # 压缩上下文
    def respond(self, running, request, answer)  # 回答统一用户输入请求（第 6.3 节）
    def close(self, running)
    events: 产出统一事件（消息、工具调用、活动阶段、用量、用户输入请求、轮次结束）
```

MCP 注入（操作清单设计第 7 节）是适配器 `open` 的一部分；各载体只在适配器内处理原生协议差异。这一步是行为改动，不在阶段二纯搬移内；阶段二的 `chat_native.py` / `chat_delivery.py` 拆分为它准备好了位置。

---

## 10. 风险与未验证

- **性能数字未测**：`/api/state` 的耗时、体积与写锁排队时间由阶段一“性能基线”任务产出；第 3 节对读路径持写锁的判断来自代码，影响幅度待基线数据确认。
- **事件节流参数**（每实体每秒一条、保留 10,000 条 / 24 小时、续传上限 1000 条）是初值，步 5 用真实工作区数据校准。
- **载体延迟加载**依赖 CC 与 Codex 的当前行为（已实测，操作清单设计第 12 节）；版本升级后需复跑对照实验。
- **手机端麦克风与 Service Worker 需要安全上下文**：局域网 HTTP 下不可用，影响语音第一步的可用范围（第 8 节拍板点 2）。
- 本文的 t3code 结论来自源码静态阅读（Codex 盘点 + 主控抽查 `McpToolExposure.ts`、`CodexAdapterV2.ts:1740–1761`），未运行 t3code。

---

## 附录 A：只读评审意见与取舍

（待 `gpt-6.2-astra` 只读评审后补写。）
