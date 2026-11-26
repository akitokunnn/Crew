# Crew 架构重审与多端演进方案（R0）

> 日期：2026-11-27 · 作者：架构重审主题会话（Claude）· 基线 `claude/spatial-studio-ui` `3fdfbec`（桌面 0.3.18）
> 姊妹文档：[巨石拆分边界](SPLIT_BOUNDARIES.md)（阶段二施工依据）、[操作清单与 MCP 设计](OPERATIONS_REGISTRY_DESIGN.md)（R5 施工依据，含真模型实测）、[入口全量盘点](OPERATIONS_INVENTORY.md)
> 参照：t3code `c2beadbc`（`C:\Tools\Crew-fixes\spatial-studio-ui\Temp\t3code`，只读）；盘点由 Codex `gpt-6.2-sol` 只读完成，关键条目主控抽查。
> 依赖口径：按[总计划](REBUILD_PLAN_2026-11-27.md)决策日志，默认零依赖；本文不引入任何依赖或构建步骤（理由见第 8 节）。
> **第 2 版（同日）**：按 `gpt-6.2-astra` 只读评审修订，评审要点与逐条取舍见附录 A。主要变化：增量事件用新表（现有 `events` 是审计表）并补事务归属、库外副作用与续传规则；迁移路线重排为“先贯通一个领域再扩大、确认闭环先于 MCP 切换”；修正 PWA / 语音的安全上下文事实。

---

## 0. 结论

1. **底座不换。** Python 标准库 HTTP 服务 + SQLite WAL + 原生载体子进程 + 原生 JS 前端这套组合，对“单用户、本机主机、几台设备”的规模够用，而且零依赖带来的安装与升级稳定性是 Crew 的实际优势。问题不在 `BaseHTTPRequestHandler` 本身，而在**没有协议层**：路由是 600 行 if 链、动作没有登记、状态靠 3 秒全量轮询、写请求没有幂等、读接口里带写和全局锁。
2. **补一个协议层，分三件东西**：
   - **操作清单**（命令面）：所有改数据动作登记一次，HTTP / CLI / MCP 从它生成，写操作带 `request_id` 只执行一次（详见操作清单设计）。
   - **“快照 + 序号”事件流**（状态面）：工作区快照接口 + 按持久序号的 SSE 事件流，断线按序号补发，补不上回 `snapshot-required` 让客户端重拿快照；逐领域覆盖后替换 3 秒全量轮询。对话日志已经是“历史 + 按序号 SSE”（`web.py:2120–2158`），把同一模式推广到工作区状态。
   - **统一的用户输入请求**（交互面）：Codex 原生审批、CC 的提问 / 计划确认、AI 发起的高风险操作确认，统一成一种结构，界面按 `kind` 渲染，不按载体名分支。
3. **多端客户端共享一套前端与协议**：网页与桌面继续是 `frontend/`（桌面 `home*` 并入），手机先做 PWA（现有窄屏适配 + 远程配对 + 事件流），通知先走已有的飞书 / QQ 通道；原生外壳与 Web Push 列为两个明确的后续拍板点。语音先用手机端系统语音识别 + 文本协议，不在主机处理音频。
4. **迁移按 5 个阶段走，每步可单独合入、可验证**（第 7 节）：拆分 → 读路径去副作用并冻结处理函数契约 → 先贯通一个领域的“命令 + 回执 + 事件 + 快照”再扩大 → 人类身份与设备边界、确认闭环、再切 MCP → 迁移全部真实调用者后删旧路径、做手机端。

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
| **零依赖 Hook 垫片** | `crew_hook.py` 用系统 Python 接入 CC 生命周期与守卫，不要求用户装任何东西；只需把会话身份从环境变量改为按载体 payload 的 `session_id` 查库 |
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
| MCP | 自有 `/mcp`，每会话随机令牌存哈希，绑定线程与 provider session；`tool_search` 后 `list_changed`；Codex 侧把少数工具 `omit_tools_from: ["deferred"]` 直出（`McpSessionRegistry.ts:125–133`、`McpToolExposure.ts:56–80`、`CodexAdapterV2.ts:1747–1761`） | Bash 调 CLI + Codex 动态工具 | **借鉴令牌、`crew_search` 加载与 Codex 直出做法**；本会话实测 Codex 不认 `list_changed`、两种载体都原生延迟加载，暴露模式在 R5 内 A/B 决定（操作清单设计 5.2、第 12 节） |
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

- **第一步（零依赖，R7 原型）**：手机端用系统 / 浏览器语音识别把话转成文字，走现有 `chat.send`；回复用浏览器朗读（`speechSynthesis`）。主机不碰音频。注意 Web Speech 规范把 `SpeechRecognition` 标为 `SecureContext`（`speechSynthesis` 没有此要求），所以识别在局域网 `http://IP` 下不可用，需要 HTTPS 或在手机本机 `localhost` 场景下验证（见第 8 节拍板点 2）；t3code 的手机端用原生 Apple Speech / ML Kit 规避了这个限制。
- **第二步（全双工，视 R7 结论）**：手机直接连实时语音模型服务，主机只签发短效凭据并把转写结果作为普通输入写入会话，主机仍不处理音频流。浏览器能做一次转写和朗读，不代表能做到可打断、回声消除、锁屏持续收音的全双工，这些需要 R7 实测。
- **只有在必须由主机中转音频时**才需要服务端 WebSocket。`qq.py` / `lark.py` 已有标准库 WebSocket **客户端**分帧代码，但服务端还要处理分片、掩码校验、控制帧、大小限制、慢客户端背压、关闭与重连，工作量不应低估；到那一步时再比较成熟实现与自维护成本，不在本阶段承诺零依赖实现。

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

PWA 需要的 `manifest.json` 与 Service Worker 只做“可添加到主屏 + 离线显示‘已断开’页”，不做离线缓存业务数据（避免旧数据冒充在线，借鉴 t3code 的约束）。Service Worker 注册要求安全上下文，局域网 `http://IP` 下不可用；“添加到主屏启动”本身在各浏览器 / 系统上的行为不同（并不都依赖 Service Worker），需按识别、朗读、Service Worker、主屏启动四项分别在目标设备上验证并记录。完整 PWA（含 Web Push）需要 HTTPS，列为拍板点（第 8 节）。

---

## 6. 借鉴 t3code 的五个机制（落地规格）

### 6.1 命令带 ID 只执行一次

- 所有写操作参数必含 `request_id`（客户端生成 UUID）。`op_requests` 表主键 `(caller_key, request_id)`，记录参数哈希、状态（`running` / `done` / `rejected`）、结果、所产生事件的最后序号。
- 重复到达：参数相同 → 返回原结果 + `replayed: true`；参数不同 → `409 request_id_reused`；仍在执行 → `status: running`。被拒绝过的命令重放拒绝原因（t3code 同此）。
- 写成功的响应带 `seq`（该命令产生的最后一个事件序号），客户端据此判断“我的修改是否已在当前快照里”，不用再拉一次全量。

### 6.2 快照 + 序号增量推送

- **新增独立的增量表** `change_log(seq INTEGER PRIMARY KEY AUTOINCREMENT, at, scope, type, entity, data JSON)` 与水位表 `change_log_state(scope, pruned_through)`。**不复用**现有 `events` 表：它是审计日志（`id/ts/project/session_id/kind/detail`，`schema.sql:536–544`），待办、通知、恢复、摘要逻辑都在读它（`board.py:52,876,911`、`todo.py:248`、`notifications.py:647`、`channel_digest.py:240`），套用新的裁剪窗口会改变旧功能。`change_log` 保留最近 10,000 条或 24 小时（初值），裁剪时推进 `pruned_through`。
- **事务归属**：现有连接 `isolation_level=None`（`store.py:23`），`board.transaction()` 自己 `BEGIN IMMEDIATE` / `commit`（`board.py:25–33`），`goal_actions.dispatch`、个人助手入口会另开连接，HTTP 的 `write_lock` 不等于 SQLite 事务。所以不能简单地“命令执行器外层包一个事务”（会嵌套事务或跨连接等锁）。做法：命令执行器拥有连接并开启事务，处理函数在这条连接上完成同库业务写入，执行器在同一事务里写回执与 `change_log`，然后提交；**逐领域**把处理函数改为“接收调用方连接、不自行开事务”，先做一个领域贯通（第 7 节阶段 3），不一次铺开。
- **库外副作用**（追加会话日志 JSONL、启动进程、向模型投递输入、写文件）不能被 SQLite 回滚：采用“持久意图 + 发件箱”——事务内记录意图与回执（`running`），提交后执行，再记结果与对应事件；后台重启时对遗留意图按可查询的副作用对账，查不清的显示“待核实”（与操作清单设计 5.5 一致）。
- **后台状态要有生产者，读者不再采集**：进程状态、活动阶段、用量由实际变化处（进程轮询线程、会话日志写入点、后台用量扫描）产生事件；高频进度按实体合并，每实体每秒最多发一条并保证最后一次更新送达；结束、删除、审批等不可丢事件单独落盘、不合并。生产者在短事务里更新投影与序号，**不得在写锁中调用完整 `state()` 构造事件**。
- **事件内容**：按实体给出新值（`{"type": "leader.updated", "entity": "<session_id>", "data": {...该 leader 在快照里的完整结构...}}`），删除给 `*.removed`；客户端按实体 ID 替换。不用 JSON Patch。
- **接口**：
  - `GET /api/snapshot?scope=workspace:<名>` → `{seq, data}`。`seq` 与 `data` 必须来自**同一个只读事务**（先在事务内读 `max(seq)` 再读投影），否则会漏掉中间提交。`data` 结构即现在 `/api/state`，只读、不加写锁。
  - `GET /api/events?scope=...&after=<seq>`（SSE，`id:` 为序号，支持 `Last-Event-ID`）：服务端循环“按 `seq > 游标` 从 `change_log` 读 → 发送 → 推进游标 → 无新数据时等通知或超时”，通知只负责唤醒，每次唤醒都重新查表，因此补历史与接实时之间不会漏事件。`after < pruned_through`、`after > 当前最大序号`、或该订阅可见的积压超过 1000 条时，只发一条 `snapshot-required` 后关闭（`after == pruned_through` 视为可续传）。空闲工作区也定期推进心跳。长连接期间每次唤醒检查远程设备是否已吊销（`remote_access.py:240–256`），已吊销即关闭。
  - 对话日志 `/api/chat/events` 保持现有格式与**它自己的序号**（会话日志序号与 `change_log` 序号不是同一序列），客户端分别保存两种游标，不做混合映射。
- **客户端**：拿快照 → 记 `seq` → 订阅 `after=seq` → 按实体合并。断线用最后序号续订；收到 `snapshot-required` 时：关闭旧订阅、取消旧重连定时器 → 重拿快照 → 以新 `seq` 建**一个**新订阅。重放与补发只恢复实体，不重播过去的音效与提醒（音效只对晚于本次连接建立时间的事件触发）。**重连只恢复订阅，绝不重发写请求**；写请求结果用 `request_id` 查询。连接断开期间界面显示“已断开”，不展示旧数据为在线状态。
- **切换时机**：在某领域的所有生产者都接入之前，前端对该领域继续使用原读取路径；全部领域覆盖后删除 3 秒轮询（`setInterval` 在 `app.js:1743` 的 `setup()` 里，`loadState` 在 `app.js:1364`），删除时不保留“SSE 不通就退回轮询”的隐式回退。

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

每一步：可单独合入集成分支；有明确的验证；不依赖后续步骤才能正常运行。（第 2 版按评审重排：确认闭环先于 MCP 切换；事件流先贯通一个领域再扩大。）

| 阶段 | 内容 | 依赖 | 验证 | 对应总计划 |
| --- | --- | --- | --- | --- |
| 1 | **锁定基线 + 纯搬移**：以[交互清单](INTERACTION_INVENTORY.md)锁定行为；按拆分边界做 W1 → W2a（先写路由契约测试）→ W2b → C1；A1 暂缓 | — | 拆分边界的核对脚本 + 全量测试失败差集与原因一致 + 交互清单抽查 | 阶段二 1–3 |
| 2 | **读路径去副作用 + 逐领域标注操作，冻结处理函数契约**：`state()` 不再调用 `scan_usage`、不拿写锁，扫描移到后台线程；GET 的副作用（文档刷新、建助手、标签补登记、用量扫描）改为显式写操作或后台步骤；同时逐领域确定每个处理函数是读还是写、加什么锁、属于哪个操作——这份契约同时供操作清单（阶段 4）与事件流（阶段 3）使用，由一个会话定稿 | 1 | 性能基线脚本：`/api/state` 耗时与并发写请求的写锁等待改前改后对比；相关 HTTP 测试 | 阶段二 5 + R5 前置 |
| 3 | **先贯通一个领域的“命令 + 回执 + 增量事件 + 快照”**（建议选交流区：写入少、事件清楚、R2 正好要改）：命令执行器拥有连接与事务、`op_requests` 回执、`change_log`、`/api/snapshot`、`/api/events`；该领域前端改为订阅；验证同连接提交、重复请求、崩溃恢复、快照与事件一致。然后逐领域扩大，全部覆盖后删除 3 秒轮询 | 2 | 断线续传 / `snapshot-required` / 不重发写请求的单测；两页面同时打开时操作互相实时可见的浏览器走查；空闲重连数、每客户端连接数、写锁等待、事件积压、慢客户端断开与退出耗时分别测量 | 阶段三“增量事件流” |
| 4 | **人类身份与设备边界 → 用户输入请求最小闭环 → MCP 与令牌切换**：先把 `device_browser` / `device_desktop` 的边界并入清单；再上线 `interaction_requests` 与 `request.respond`（Codex 审批、CC 提问 / 计划确认、AI 高风险确认），使 `risk=high` 有落点；然后上线 `/mcp`、令牌签发与吊销、CC / Codex 启动注入、hooks 按 payload 查库、生成 CLI，按操作清单设计 5.2 做暴露模式 A/B；新开、恢复、撤权、实际调用都通过后，同一交付里删除环境变量身份、Bash 调 `crew`、Codex 动态工具、`/api/assistant/native-action` | 2（清单骨架）；结果回告依赖 3 | 操作清单设计第 10 节覆盖测试；第 12 节实验在产品端点复跑；Haiku 5.5 + luna low 真实会话跑通核心工具与一次高风险确认；grep 旧路径为空 | 阶段三 R5、阶段四前置 |
| 5 | **迁移全部真实调用者 → 删除旧绑定 → 手机专项**：前端、桌面（含 IPC 与本机 worker）、CLI、下载链接、SSE 逐个迁到 `/api/op` 或登记为专用传输；旧 URL 绑定清零；`protocol.js` 由清单生成；桌面 `home*` 并入 `frontend`（UI 收尾主题之后）；手机 PWA（二维码配对、窄屏交互、飞书 / QQ 通知）；语音第一步（R7 已获准提前探索，与事件流无硬依赖） | 3、4 | 覆盖测试中旧绑定为 0；`smoke-workspaces.cjs`；三视口截图；375px 真机或模拟器按交互清单手机条目走查 | R7、阶段一 UI 收尾后续 |

阶段 2 的“读路径卫生”与“处理函数契约”放在一起，是因为两者都取决于“有副作用的 GET 拆成什么读写操作、锁和投影怎么变”，分给两个会话会各定一套。阶段 3 与阶段 4 可由不同会话并行（一个在 `change_log` 与前端数据层，一个在 `ops/`、令牌与载体启动），共享契约（事件信封、操作名、错误码）由主会话先定稿。

## 8. 依赖决策

| 候选 | 能解决什么 | 不引入的理由 / 代价对比 | 结论 |
| --- | --- | --- | --- |
| 异步 HTTP 框架（aiohttp、FastAPI/uvicorn） | 高并发、WebSocket | Crew 并发量小，线程模型够用；桌面直接使用用户系统里的 Python 3.11+（`desktop/src/python.cjs`），引入后要在用户的 Python 里 pip 安装并管理版本，升级多一层故障面 | 不引入 |
| WebSocket 库 | 双向推送、语音 | 推送单向，SSE 足够；语音第一步不经主机。若将来必须由主机中转音频，服务端 WebSocket 的分片、控制帧、背压、关闭处理工作量不小，届时比较成熟实现与自维护成本 | 现在不引入；到语音中转时重新评估 |
| TypeScript / 构建工具 | 协议类型、编辑器提示 | 引入构建步骤，失去“改完刷新即生效”；用 JSON Schema 生成 JSDoc 类型（`protocol.js`）可得到大部分好处 | 不引入 |
| 前端框架（React/Vue/Preact、lit） | 组件边界、局部更新 | 一次性重写 1.3 万行风险极高，交互零丢失门禁难守；事件驱动 + 按实体局部渲染可渐进获得局部更新 | 不引入；拆分后逐区改造 |
| Effect 等函数式运行时 | 类型化错误与资源管理 | 只对 TypeScript 栈有意义 | 不引入 |
| 标准 Web Push（需要 ECDSA P-256 的 VAPID 签名与消息加密，标准库没有） | 手机系统级推送 | iOS 主屏 Web App 已支持经用户交互申请 Web Push 并显示锁屏通知，所以“锁屏推送”不必然要求原生外壳；但自建要处理订阅生命周期、消息加密与 HTTPS。已有飞书 / QQ 主动通知可覆盖“离开时提醒”（前提是用户已配置） | **拍板点 1**：先用飞书 / QQ；用户要求系统推送时再评估 Web Push（及其依赖） |
| HTTPS（自签证书 / Tailscale 证书） | Service Worker、语音识别（`SpeechRecognition` 要求安全上下文） | 证书分发与信任配置对用户有成本；局域网 `http://IP` 下这两项不可用 | **拍板点 2**：R7 原型先按识别、朗读、Service Worker、主屏启动四项在目标设备上分别验证；产品化前决定证书方案 |
| 原生手机外壳（Capacitor / Expo） | 后台录音、锁屏持续收音、可打断与回声处理、系统语音识别 | 新增一整套构建链与商店分发 | **拍板点 3**：在后台录音、锁屏持续输入、打断与回声处理有实际需求时再定 |

本次 R0 不引入任何依赖或构建步骤，按决策日志由主会话自行确认即可；拍板点 1–3 在相关主题（R7 语音、手机端）推进到那一步时再交用户。

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
- **事件节流参数**（每实体每秒一条、保留 10,000 条 / 24 小时、续传上限 1000 条）是初值，阶段 3 用真实工作区数据校准。
- **事务归属改造**是阶段 3 的主要工作量：现有处理函数自己开连接 / 事务的写法要逐领域改为“接收调用方连接”，先贯通一个领域再估算其余领域。
- **工具暴露模式**：施工基线按裁决 7；“权限即工具集”依赖 CC 与 Codex 当前的原生延迟加载行为（已用 60 个合成工具实测），需在 R5 按约 200 个真实操作 A/B 后才能决定（操作清单设计 5.2）。
- **手机端麦克风与 Service Worker 需要安全上下文**：局域网 HTTP 下不可用，影响语音第一步的可用范围（第 8 节拍板点 2）。
- 本文的 t3code 结论来自源码静态阅读（Codex 盘点 + 主控抽查 `McpToolExposure.ts`、`CodexAdapterV2.ts:1740–1761`），未运行 t3code。

---

## 附录 A：只读评审意见与取舍

**评审方式**：Codex `gpt-6.2-astra`（reasoning high，只读沙箱）评审基于提交 `5aa0406` 的三份文档与对应源码，任务书要求“找错和找更好的做法”，逐条给证据。评审原文 135 行，保存在本会话 `Temp/tasks/架构重审评审_盘点任务书_last.md`（被忽略），要点如下。评审未运行测试、服务或模型，也未重跑第 12 节实验。主控对“必须改”的关键条目逐一复核了源码（下表“核实”列）。

### A.1 必须改

| # | 评审意见 | 核实 | 取舍与落实 |
| --- | --- | --- | --- |
| 1 | W1 把 mixin 放进 `web_service/` 子包后，`update_reasons`、`connection_plan`（`from .operations import …`）与 `_project_conversations`（`from . import decision_briefs, decision_flow`）的函数内相对导入会解析到子包下，运行到该路径才报错；建议允许把 `.` 改成 `..` | 属实（`web.py:341,384,874`） | **采纳问题，改用另一种修法**：mixin 改为与 web.py 同层的平铺模块 `web_service_*.py`，函数内导入一字不改，纯搬移仍可被脚本证明；核对脚本新增“相对导入可解析”检查。已在 W1 施工中，当即通知主会话 |
| 2 | `_text` / `_optional_text` 必须一起进 `web_api/common.py` 并在核对命令中列入；`STATIC`、`MODEL_WORK`、端口常量、`_vscode_state_db` 应明写留在 web.py；旧模块绑定（`web.subprocess`、`web.Path`）不能裁 | 属实 | 采纳，写入拆分边界 1.1–1.3 与总则 2、3 |
| 3 | W2 的路由表表达不了现有分派：外层 `post` 直接处理 3 条管理路径；`/api/goals/agent` 仅 `report.preview` 走 A 段且绕开准入；B 段实际到 1376、正文一致性检查会改写 `project_name`；A 段“无外层锁”不等于无锁；末尾兜底需区分未匹配与已处理 | 属实（`web.py:1125–1147,1338–1382,1704–1731`） | 采纳：骨架照抄原控制流，条件分支与预处理留在骨架内，只把连续的按路径分支换成查表；用 `UNHANDLED` 回落；覆盖测试纳入外层直接处理集合 |
| 4 | `same_route_blocks.py` 不能证明路由等价（改外层条件、调换优先级、换锁都能通过），且只对旧树改名会误报；应验收“请求如何抵达处理函数” | 属实 | 采纳：脚本降为必要条件并修正改名；新增 W2a——在**未拆**代码上先写路由契约测试（正反例覆盖每个例外，间谍记录准入与加锁），W2b 拆完后测试一行不改必须通过 |
| 5 | C1：`clear_unstarted_member_bindings` 用到 `Journal`，放进 `chat_base` 会造成 `chat_base ↔ chat_journal` 互相依赖；各 mixin 需显式名字表；mixin 实为 8 个 | 属实（`chat.py:89–117`） | 采纳：该函数与 `Journal` 同放 `chat_journal`；拆分边界 2.2 列出各模块显式导入名字 |
| 6 | 附录 A 脚本只比函数体与装饰器，忽略参数、默认值、重复定义；Windows 命令把 `*.py` 原样交给 Python | 属实 | 采纳：脚本比较完整签名、检测重复定义；文档要求逐个列文件 |
| 7 | A1：`test_web_contract.py` 还两处直接读磁盘 `app.js`；头注释 + LF 外壳 + 归一化比较只证明规范化文本相同；建议阶段二保留物理 `app.js`，若需切分则“生成并提交” | 属实（`test_web_contract.py:906,1200`；工作树 CRLF） | **采纳**：阶段二不做 A1；保留 15 段切点原型供“生成并提交”备选；真模块化由事件流会话抽数据层时按 A2 规则做 |
| 8 | MCP 实验不足以改写用户裁决 7：60 个合成工具、点名调用的单轮对照不能代表约 200 个真实操作的发现率与误选；`tools/list` 从少数变全部是契约变更 | 结论合理 | **采纳**：施工基线回到裁决 7（核心 + `crew_search`，CC `list_changed`，Codex `crew_call`）；“权限即工具集”作为 R5 内 A/B 候选，两模式显式配置、复测后删除落选者（操作清单设计 0、5.2、5.4） |
| 9 | “令牌不进环境变量 → 子进程拿不到身份”的安全结论过度：配置文件与命令行对同用户可见；CLI 固定为 `human` 时，本机来源与 `X-Crew` 证明不了是用户本人 | 属实 | 采纳：新增威胁模型（操作清单设计 3.4），只承诺“防意外继承与误用”；守卫拦截 shell 直连 Crew API / 数据库作纵深；真正隔离列为后续议题（拍板第 6 条） |
| 10 | hooks 改每会话 `--settings` 会与插件 `hooks.json` 并行触发 | 属实 | 已在评审前自行改为“按载体 payload 的 `session_id` 查库”，与评审建议一致；不新增 settings |
| 11 | 身份迁移漏了注入点（`child_environment` 注入 `CREW_NAME/ROLE/GROUP/SPAWN_ID`）、CLI 读 `CREW_GOAL_ID`、派工脚本的展示关联；剥离代码应保留 | 属实 | 采纳：迁移步骤按“读取 / 注入 / 剥离 / 展示关联”四张清单验收（操作清单设计第 7 节） |
| 12 | 撤权“立即生效”与委派“不追溯”冲突；按操作名直接求交会丢掉执行专属能力；助手没有 `execution.spawn` 却说覆盖 leader | 属实 | 采纳：拆 `own_scopes` / `delegable_scopes`，每次调用检查令牌、在岗、祖先链与当前权限表，父会话停止时级联吊销（操作清单设计 3.3） |
| 13 | 权限表不能直接生成授权：浏览器（允许清单）与远程桌面（拒绝清单）不能合成一个 `device`；`group.close`、Git 助手停自己等与代码不符；执行侧文档列表有目标过滤；表内自相矛盾 | 属实 | 采纳：拆 `device_browser` / `device_desktop`；修正矛盾行；声明第 4 节只是摘要，每个操作的范围按现有入口行为写正反例测试校验，不用同一张表既生成实现又生成预期 |
| 14 | 同一 URL 按 `action` 分派多个操作，绑定需要（方法, 路径, 判别条件），准入在解析出具体操作后判定；旧前端没有 `request_id` | 属实 | 采纳：绑定键扩展；`admission` 改为按操作；旧绑定显式生成一次性 ID 并在覆盖测试里单独计数 |
| 15 | 现有 `events` 是审计表，不能套用新保留期；写入不都在同一连接 / 事务；库外副作用不能回滚；后台状态需要生产者；快照与续传有丢事件窗口；`snapshot-required` 需要客户端退出动作；会话日志序号与全局序号不同 | 属实（`schema.sql:536–544`、`store.py:23`、`board.py:25–33`） | 全部采纳：新表 `change_log`；执行器拥有连接与事务并逐领域改造；持久意图 + 发件箱；生产者模型；同一读事务取快照与序号；按 `seq > 游标` 循环读；客户端步骤与两种游标（第 6.2 节） |
| 16 | 迁移顺序：高风险 MCP 依赖确认闭环与结果通知；读路径卫生与清单骨架共享“有副作用 GET 怎么拆”的契约；事件流应先贯通一个领域；`setInterval` 与 `loadState` 不在同一片段 | 属实 | 采纳：路线重排为 5 个阶段（第 7 节） |
| 17 | 覆盖测试只扫 `api/fetch/post` 漏掉桌面 IPC 与本机 worker（如 `desktop:settings-save` 经运维 worker 直写）；预期集合不应由清单生成 | 属实（`desktop/src/main.cjs:538–546,657–670`） | 采纳：清单加 `ipc` 绑定；覆盖测试第 9、10 条 |
| 18 | MCP 协议：客户端发来的通知 / 响应应回 202 空体；应校验 `Origin`；`Mcp-Session-Id` 不宜用令牌哈希前缀冒充 | 属实（MCP 2025-06-18 Streamable HTTP） | 采纳：5.1 改为 202、校验 `Origin`、不发 `Mcp-Session-Id` |
| 19 | PWA / 语音事实：`SpeechRecognition` 要求安全上下文而 `speechSynthesis` 不要求；“局域网 HTTP 下添加到主屏只能是书签”说法不准确；iOS 主屏 Web App 支持 Web Push 与锁屏通知；服务端 WebSocket“约 150 行”低估 | 属实 | 采纳：修正第 5.4、5.5、8 节与拍板点 1–3 的前提 |

### A.2 建议改（全部采纳）

1. `message_id` 只是来源证据，不是对具体操作的同意 → `risk=high` 的确认绑定发起者、操作、目标、参数哈希与目标版本，一次性有效，执行前复查（操作清单设计 5.6）。
2. “只执行一次”对库外副作用缩小为“可查询、不盲目重发、结果不明可识别”，回执键用稳定调用身份而非令牌哈希（5.5）。
3. 不以一张截图替代交互门禁 → 拆分每步按交互清单抽查受影响入口（拆分边界 0.1）。
4. 协议测试与性能指标分开测（第 7 节阶段 3 的验证列）。
5. `STATIC['/goal-versions-ui.js']` 删除放进死代码清理步骤；`/remote/logout` 统一为非操作入口（操作清单设计 8.15）。

### A.3 未采纳或部分采纳

| 评审意见 | 处理 |
| --- | --- |
| W1 允许把三处相对导入的 `.` 改成 `..` | 改用同层平铺模块，避免体内改动，理由见 A.1 第 1 条 |
| “每步跑 Python 全量”与仓库日常“只跑相关测试”不一致，应由主会话在任务书明确 | 保留全量：web.py / chat.py 分别被 54 / 46 个测试模块直接导入，“相关测试”实际就是全量（拆分边界 0.1 已写明理由）；同时补上“比较失败原因文本”的要求。最终由主会话在任务书里确认 |
| 评审确认无误的部分（W1 可先做、W2 保留传输层门禁与业务准入两层、A1 对共享闭包的判断、读路径副作用 / 工具真实标注 / 断线不重发三项方向） | 维持原设计 |
