# Codex 原生接续：开源实现核验与复用边界

核验日期：2026-10-30。Crew 核验基线为 `rebuild/v2`、`e69b64d`、桌面 `0.3.2`；当时原生接续修复尚有未提交改动。本报告记录所列固定提交的静态源码核验，不代表安装、运行或发布了第三方产品，也不代表此后的仓库现状。

## 结论与当前选择

确有开源代码让官方 Codex 桌面与其他客户端连接同一个 app-server。不能说“开源产品都只有自己的界面”，也不能因某项目写了 resume 就认为它支持官方桌面往返。

本次实际发现分成三种：

1. Harnss、Happy 启动自己的独立 stdio app-server，恢复持久化 thread。这不解决另一个官方桌面进程仍持有 writer 的情况。
2. Cotal、Happier 共用后台并接入官方 Codex **终端 TUI**，不是官方桌面。Happier 所核验共享实现还明确排除 Windows。
3. codex-local-remote 确实连接 Windows 官方桌面；codex-desktop-linux 确实改造桌面后台连接；codex-web 则改造官方桌面前端供浏览器使用。它们有可复用组件，但没有直接满足 Crew 的逐成员/目标身份、guard、动态工具唯一处理者及指令刷新要求。

因此此次继续采用 Crew 的独立 stdio 载体；桌面接续的修复也不引入跨 Crew 会话共享的模型执行后台。后续若改为共享后台，应先解决已经实测的工具请求广播与已加载 thread 的 resume overrides 不生效问题，不能仅凭“两个客户端都 resume 成功”判断完成。

## Crew 已采用什么，尚未采用什么

| 范围 | 实际来源与状态 |
| --- | --- |
| Codex 启动、初始化、thread/start、thread/resume、turn/start、中断、压缩、审批及工具事件 | `plugin/crew/codex_client.py`、`codex_runtime.py`、`codex_events.py` 对接 Codex 原生 app-server 协议。使用同一种公开 API 不等于复制了 Harnss/Happy 的客户端代码。 |
| 原生 thread 与 Crew 总会话/目标执行绑定、交接状态、历史同步 | Crew 自有 `native_handoff.py`、`codex_history.py`、`native_home.py` 等领域接口。第三方项目没有直接替换这些规则。 |
| Windows 桌面启动适配器 | 本轮 `desktop/native/CrewCodexBridge.cs` 与对应 Python 交接接口属于 Crew 自行实现；方案经隔离原生探测形成。构建集成不意味着代理功能已通过真实桌面验收，最终证据见对应交付记录。 |
| 下面列举的第三方代码 | 本轮仅源码研究，未将其实现复制进 Crew，也未安装其后台。不能将报告里的“可复用”写成“已复用”。未来复制或移植时，另记源文件、固定提交、修改和许可证归属。 |

官方协议入口：[openai/codex app-server](https://github.com/openai/codex/tree/main/codex-rs/app-server)。本链接用于说明 API 来源，不把浮动 main 当作本机实际 CLI 的精确版本证据；本机能力以实际二进制生成的 schema 和隔离探测为准。

## 可直接抽取复用的组件：codex-local-remote

- 仓库：[wlyaaaaa/codex-local-remote](https://github.com/wlyaaaaa/codex-local-remote)
- 核验提交：`94f1cfadfbba97d3cfd2b21c73fba0104ccf2cf6`
- [LICENSE](https://github.com/wlyaaaaa/codex-local-remote/blob/94f1cfadfbba97d3cfd2b21c73fba0104ccf2cf6/LICENSE)：MIT，复制或分发实质部分须保留版权与许可。

实际链路是受管 Windows 官方桌面 → broker → 认证 Codex app-server，另有 sidecar 连接同一后台，不是只启动自己的聊天界面。

- [runtime.ts 468–483](https://github.com/wlyaaaaa/codex-local-remote/blob/94f1cfadfbba97d3cfd2b21c73fba0104ccf2cf6/apps/broker/src/runtime.ts#L468-L483)：启动 loopback WebSocket app-server，并启用 capability-token 认证。
- [runtime.ts 603–618](https://github.com/wlyaaaaa/codex-local-remote/blob/94f1cfadfbba97d3cfd2b21c73fba0104ccf2cf6/apps/broker/src/runtime.ts#L603-L618)：每个下游连接建立带 Bearer 的上游连接。
- [Launch-CodexWithRemote.ps1 5293–5324](https://github.com/wlyaaaaa/codex-local-remote/blob/94f1cfadfbba97d3cfd2b21c73fba0104ccf2cf6/scripts/windows/Launch-CodexWithRemote.ps1#L5293-L5324)：通过 CreateProcess 语义，把 `CODEX_APP_SERVER_WS_URL` 限定在启动的官方桌面进程。其 launch nonce 与 broker readiness 用来核实目标进程确实连接。
- [thread-lifecycle.ts 34–85](https://github.com/wlyaaaaa/codex-local-remote/blob/94f1cfadfbba97d3cfd2b21c73fba0104ccf2cf6/apps/broker/src/thread-lifecycle.ts#L34-L85)：独立 `ThreadLifecycleArbiter` 序列化各连接的 turn/start，并保留检查中、已预留、活动和未知状态。这个组件最适合以后抽取移植。

不能整搬成 Crew 共享方案的具体原因：

- [coordinator.ts 1031–1161](https://github.com/wlyaaaaa/codex-local-remote/blob/94f1cfadfbba97d3cfd2b21c73fba0104ccf2cf6/apps/broker/src/coordinator.ts#L1031-L1161)最终直接把上游 frame 转给客户端，没有为 Crew 动态工具选择唯一执行者。这里的 `ownerConnectionId` 用于 turn/归档预留，不等于工具请求处理权。
- [sidecar/runtime.ts 404–405](https://github.com/wlyaaaaa/codex-local-remote/blob/94f1cfadfbba97d3cfd2b21c73fba0104ccf2cf6/apps/sidecar/src/runtime.ts#L404-L405)把 serverRequest 交给审批协调器；[approvals.ts 50–57](https://github.com/wlyaaaaa/codex-local-remote/blob/94f1cfadfbba97d3cfd2b21c73fba0104ccf2cf6/packages/domain/src/approvals.ts#L50-L57)对无法投影成审批的请求立即 reject。接入 Crew 的 `item/tool/call` 广播时，另一个客户端可能先拒绝本应由 Crew 执行的工具。
- 所核验链路不提供 Crew 的 session/goal/execution 环境和 hook 身份，也没有解决已加载 thread 的配置刷新。不能用其“桌面远程控制”宣传替代这些验证。

## 仅借鉴

### Cotal：共享后台与官方终端

- 仓库：[Cotal-AI/Cotal](https://github.com/Cotal-AI/Cotal)
- 提交：`55a53e8d7b9ebf4ac8ff7b230e1d6f7ba649704f`
- [LICENSE](https://github.com/Cotal-AI/Cotal/blob/55a53e8d7b9ebf4ac8ff7b230e1d6f7ba649704f/LICENSE)：Apache-2.0；再分发时附许可证、保留归属，修改须标明，并保留适用的 NOTICE 内容。
- [app-server.ts 294–330](https://github.com/Cotal-AI/Cotal/blob/55a53e8d7b9ebf4ac8ff7b230e1d6f7ba649704f/extensions/connector-codex/src/app-server.ts#L294-L330)启动认证共享 WS；[tui.ts 35–53](https://github.com/Cotal-AI/Cotal/blob/55a53e8d7b9ebf4ac8ff7b230e1d6f7ba649704f/extensions/connector-codex/src/tui.ts#L35-L53)执行 `codex resume --remote ... threadId`，接回官方 TUI。
- [app-server.ts 704–765](https://github.com/Cotal-AI/Cotal/blob/55a53e8d7b9ebf4ac8ff7b230e1d6f7ba649704f/extensions/connector-codex/src/app-server.ts#L704-L765)及后续事件处理可参考精确 turn ID 归属、乱序完成事件和有限历史；但工具被放到独立 MCP，server request 处理器直接拒绝审批/不支持的请求，不是 Crew 所需的跨客户端工具归属实现。

### Happier：共享后台与官方终端，Windows 不适用

- 仓库：[happier-dev/happier](https://github.com/happier-dev/happier)
- 提交：`10ff24b140e45f323e956e8b22a1b26a77a1824a`
- [LICENCE](https://github.com/happier-dev/happier/blob/10ff24b140e45f323e956e8b22a1b26a77a1824a/LICENCE)：MIT。
- [createCodexSharedAttachArgs.ts 7–12](https://github.com/happier-dev/happier/blob/10ff24b140e45f323e956e8b22a1b26a77a1824a/apps/cli/src/backends/codex/localControl/createCodexSharedAttachArgs.ts#L7-L12)生成原生 TUI 的 `--remote ... resume ID` 参数。
- [resolveCodexSharedControlSupport.ts 43–47](https://github.com/happier-dev/happier/blob/10ff24b140e45f323e956e8b22a1b26a77a1824a/apps/cli/src/backends/codex/localControl/resolveCodexSharedControlSupport.ts#L43-L47)明确拒绝 `win32`；不能写成已解决 Windows 官方桌面接回。
- 可借鉴 [createCodexSharedAppServer.ts](https://github.com/happier-dev/happier/blob/10ff24b140e45f323e956e8b22a1b26a77a1824a/apps/cli/src/backends/codex/appServer/createCodexSharedAppServer.ts#L91-L181)的私有 socket、同二进制探测、ready 与失败清理，以及 [createSharedProviderLocalControl.ts](https://github.com/happier-dev/happier/blob/10ff24b140e45f323e956e8b22a1b26a77a1824a/apps/cli/src/agent/localControl/createSharedProviderLocalControl.ts#L70-L144)的串行 attach/detach 和身份复核。

### codex-desktop-linux：确实共享官方桌面后台，但依赖 Linux 和桌面补丁

- 仓库：[ilysenko/codex-desktop-linux](https://github.com/ilysenko/codex-desktop-linux)
- 提交：`f9b973bc03ea60fc971bd227948541fe84bce635`
- [LICENSE](https://github.com/ilysenko/codex-desktop-linux/blob/f9b973bc03ea60fc971bd227948541fe84bce635/LICENSE)：MIT。
- [patch.js 151–156](https://github.com/ilysenko/codex-desktop-linux/blob/f9b973bc03ea60fc971bd227948541fe84bce635/linux-features/shared-app-server-socket/patch.js#L151-L156)修改桌面 transport，启动单一 `app-server --listen unix://...`，各连接经原生 `app-server proxy --sock` 接入。
- [attached-cli.sh 390–391](https://github.com/ilysenko/codex-desktop-linux/blob/f9b973bc03ea60fc971bd227948541fe84bce635/linux-features/shared-app-server-socket/attached-cli.sh#L390-L391)让 CLI 接同一 socket。权限、进程登记和清理依赖 Linux `/proc`、Unix socket 与文件身份。
- 桌面 dispose 会终止 authority，不是“Crew 退出而原生桌面继续”的通用生命周期。可参考身份登记，不能直接作为 Windows Crew 的实现。

### codex-web：改造官方前端，不能照搬简单代理

- 仓库：[0xcaff/codex-web](https://github.com/0xcaff/codex-web)
- 提交：`2424e8548db3eebeb49eef5057ef750688bb680b`
- [package.json 26](https://github.com/0xcaff/codex-web/blob/2424e8548db3eebeb49eef5057ef750688bb680b/package.json#L26)声明 MIT，但所核验树没有独立 LICENSE 文件。这也不授予被补丁处理的官方桌面代码额外许可。
- [ARCHITECTURE.md](https://github.com/0xcaff/codex-web/blob/2424e8548db3eebeb49eef5057ef750688bb680b/ARCHITECTURE.md#L30-L63)及 [app-host-json-rpc.patch](https://github.com/0xcaff/codex-web/blob/2424e8548db3eebeb49eef5057ef750688bb680b/patches/app-host-json-rpc.patch)展示了打包前端、Electron shim 与 IPC 改造，属于改造官方应用，不是普通外部 API 客户端。
- [codex_remote_proxy](https://github.com/0xcaff/codex-web/blob/2424e8548db3eebeb49eef5057ef750688bb680b/scripts/codex_remote_proxy)用 websocat 转发 stdio 与 Unix WS，但丢弃 `-c` 配置，只接受 app-server，不支持启动版本检查，没有认证和工具归属过滤。不能替代 Crew 的 Windows 桌面代理。

## 不解决当前桌面 writer 问题

### Harnss

- 仓库：[OpenSource03/harnss](https://github.com/OpenSource03/harnss)
- 提交：`dc1dfd8a33caa46a1eefcfe9e14697b27ac4c33d`
- [LICENSE](https://github.com/OpenSource03/harnss/blob/dc1dfd8a33caa46a1eefcfe9e14697b27ac4c33d/LICENSE)：MIT。
- [codex-sessions.ts 689–736](https://github.com/OpenSource03/harnss/blob/dc1dfd8a33caa46a1eefcfe9e14697b27ac4c33d/electron/src/ipc/codex-sessions.ts#L689-L736)明确为恢复启动新的 `codex app-server`，通过 stdio `thread/resume`；[codex-rpc.ts 126–134](https://github.com/OpenSource03/harnss/blob/dc1dfd8a33caa46a1eefcfe9e14697b27ac4c33d/shared/lib/codex-rpc.ts#L126-L134)销毁时终止自有进程。
- 这是可研究的多载体桌面产品实现，但该恢复链路不是连接官方桌面现有 owner。

### Happy

- 仓库：[slopus/happy](https://github.com/slopus/happy)
- 提交：`ee1eb59039c0898c248855b1e1d38749008b863c`
- [LICENSE](https://github.com/slopus/happy/blob/ee1eb59039c0898c248855b1e1d38749008b863c/LICENSE)：MIT。
- [codexAppServerClient.ts 608–650](https://github.com/slopus/happy/blob/ee1eb59039c0898c248855b1e1d38749008b863c/packages/happy-cli/src/codex/codexAppServerClient.ts#L608-L650)启动独立 `app-server --listen stdio://`；[707–764](https://github.com/slopus/happy/blob/ee1eb59039c0898c248855b1e1d38749008b863c/packages/happy-cli/src/codex/codexAppServerClient.ts#L707-L764)断开时终止自有进程；[821–860](https://github.com/slopus/happy/blob/ee1eb59039c0898c248855b1e1d38749008b863c/packages/happy-cli/src/codex/codexAppServerClient.ts#L821-L860)恢复持久化 thread。
- Happy 自身客户端远程控制不等于官方桌面同 thread 接回，不能用来绕过另一个后台持有 writer 的事实。

## 核验范围与限制

阅读了上述项目实际核心源码与实际许可文件（codex-web 仅有 package 声明，已单列），没有运行这些第三方项目，没有调用真实 AI，没有改正式 HOME、安装源码或启动服务。许可证说明是本次分发条件摘记，不是对整个依赖树的法律审查。

源文件缓存与 GitHub 树/SHA 元数据位于忽略目录 `Temp/v2-github-continuity-research/`；Happier 来源文件位于 `Temp/v2-desktop-research-2026-10-30/happier-dev__happier/`。公开链接以固定提交为准，缓存不作为交付依赖。搜索并非穷举整个 GitHub；未核验项目不据此判定不支持。
