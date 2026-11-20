# 给 Mac 开发 AI 的同步说明（2026-11-20）

这份说明写给在 Mac 上继续开发 Crew 的 AI。Windows 这边这两天做了两件会影响你的事：删除了两个功能，并精简了测试。下面依次说明你需要知道什么、要做什么。

## 1. 先合并 live

在你的工作分支里执行 `git merge live`。live 里与你相关的提交：

| 提交 | 内容 |
| --- | --- |
| `0a8bb49` | 删除“交给 Codex 桌面 / 接回”和“在 VS Code 中继续”（你在 `a74de0f` 已合过并去掉了交接标记） |
| `0e37574` | 测试精简：删掉不用的冒烟脚本和一次性测试，修过时断言，修设置页面板重挂载问题，见第 3 节 |

## 2. 已删除的功能，不要加回来

用户 2026-11-19 拍板在**所有平台**删除，Mac 版也不再做：

- 后端：`native_handoff.py`、`codex_desktop.py`、`codex_history.py`、`open_session.py`；接口 `/api/open`、`/api/open/continue`、`/api/session/native-handoff`、`/api/session/native-return`；CLI `crew open`。
- 前端：在 VS Code 打开 / 转到 VS Code 里聊 / 在 Codex 桌面继续 / 回到 Crew 的按钮、弹窗和只读提示；`/api/access/context` 不再返回 `native_handoff`、`vscode_continue`。
- 桌面外壳：`CrewCodexBridge.cs`、编译脚本和打包步骤。打包已不需要 C# 编译器。
- 保留的部分：Codex 会话与原生线程的绑定（线程 ID、所在 CODEX_HOME、线程被其他 Codex 客户端占用时的提示）在 `plugin/crew/codex_binding.py`，`native_session_controls` 表继续作为绑定记录；识别“会话正开在 VS Code 或终端里”的逻辑也保留。

## 3. 测试现在的样子

用户要求测试“尽可能只留必要的”。规则：

- **单元测试全部保留**，它们是主要的安全网：`plugin/tests/test_*.py`、`plugin/tests/test_*.js`、`desktop/test/*.test.cjs`。
- **冒烟脚本只留 4 个**，都在仓库根目录执行，使用隔离 HOME 和假载体：

| 脚本 | 用途 | 谁在跑 |
| --- | --- | --- |
| `node desktop/scripts/smoke-workspaces.cjs` | 桌面工作区交互 | Windows CI（带 `CREW_TEST_SHOW_WINDOW=1`，窗口要可见才能截图） |
| `node plugin/tests/smoke_conversation_routing.cjs` | 对话与任务路由 | Windows CI |
| `node desktop/scripts/smoke.cjs` | 源码或安装包启动 | 发版流程、Mac 构建（`mac-build.yml`） |
| `node desktop/scripts/smoke-remote-backend.cjs` | 桌面客户端连局域网主机 | 你在做的“后台可选”功能的端到端检查 |

- 其余冒烟脚本、基准脚本、Playwright 脚本和它们专用的夹具都删了（连同下面那个手动测试共 66 个文件），包括你 11-19 改过的 `smoke-launch-settings.cjs`、`smoke-remote-access.cjs`；需要的话可从 git 历史找回。以后靠单元测试加 `prove-it` 真实走查，**不再为单个改动新增一次性冒烟脚本**。这台 Windows 机器没有装 Playwright，也不要再写依赖 Playwright 的脚本。
- 默认永远不跑的手动测试 `test_codex_windows_paths.py` 也删了。

Windows 上的全量结果（2026-11-20，修完后）：

| 套件 | 结果 |
| --- | --- |
| Python `python -m unittest discover -s tests`（在 `plugin` 下） | 2600 项；原有 4 项失败已修，11 项按平台跳过 |
| 网页 `node --test plugin/tests/test_*.js` | 275 项全部通过 |
| 桌面 `npm --prefix desktop test` | 226 项全部通过 |
| 保留的 4 个冒烟脚本 | 全部通过 |

4 项失败（推送前的 `59f7567` 也做了同样的修正，两边已合并为同一写法），其中 3 项和你直接相关：

- `test_web_ports.py` 的 3 项：你在 `3181037` 里让 `web.Server` 自己重写了 `server_bind`（跳过 macOS 上 35 秒的反向域名查询），而这几个测试打桩的是父类 `ThreadingHTTPServer.server_bind`，被子类方法绕过了。产品代码没问题，已改成打桩 `web.Server.server_bind`。以后再改服务器类时注意这几个测试打桩的位置。
- `test_shell.py` 的用量接口测试：接口新增了 `claude_weeks`（用量页“按周”），预期字段集已补上。

一个由冒烟脚本揪出来的产品 bug，已修：

- 设置页（`plugin/crew/web/settings-ui.js`）整体快照加载完成时会重新渲染当前分类。“音效”面板自己读写数据，却不在原来写死的“自管分类”名单（connections / storage / prompts）里，于是会被重新挂载；如果这时正在保存音效，旧实例丢掉“音效已保存”，新实例还把已保存的改动显示成“未保存”。`smoke-workspaces.cjs` 因此大约三成概率卡在“native sound preferences saved”。现在改为：注册了面板、且不是 `backend`（它用整体快照）的分类都视为自管数据，快照加载完不再重挂载它们；音效、局域网访问、会话库、外观、工具更新一并受益。回归断言在 `test_settings_ui.js`（对 connections / sounds / remote 都检查，旧代码会失败）。

冒烟脚本的两处修正：

- `smoke-workspaces.cjs` 本地运行要设 `CREW_TEST_SHOW_WINDOW=1`（CI 已设），窗口不可见时截图会失败。音效保存那一步超时时会输出面板当时的状态文字，方便定位。
- `smoke-remote-backend.cjs`（你写的）：设置窗口宽度后改为允许 ±1 像素。Windows 这台电脑屏幕缩放 200%，内容区设 1220 时实际是 1219，原来要求精确相等会超时。

## 4. 你在 Mac 上要做的

1. 合并 live 后，在 Mac 上跑一遍全量（跑测试前去掉 `CLAUDE_CODE_SESSION_ID`、`CLAUDECODE`、`CLAUDE_CODE_ENTRYPOINT`、`CREW_SESSION_ID`，否则一批测试会把外层会话当成调用方而误报）：

   ```bash
   cd plugin && python -m unittest discover -s tests && cd ..
   node --test plugin/tests/test_*.js
   npm --prefix desktop test
   node desktop/scripts/smoke.cjs
   node desktop/scripts/smoke-remote-backend.cjs
   ```

2. Mac 特有的失败，修好后照常走 `scripts/live.ps1 merge`（或你那边等价的流程）合入 live；确实只能在 Mac 上暂时跳过的，像 `test_workspace_settings.py` 那样用 `skipIf` 写清原因并记为已知问题。
3. 写测试时注意：
   - 快捷键提示用 `window.CrewBrowser.keyboardShortcut()`（Mac 显示 ⌘，Windows 显示 Ctrl），断言不要写死 `Ctrl+`。
   - 窗口尺寸断言容忍 ±1 像素，高分屏会有取整误差。
   - 新增核心流程的单测模块记得加进 `plugin/tests/ci_core.txt`。
   - 需要新的冒烟脚本前先确认现有 4 个和单元测试确实覆盖不到，并在 AGENTS.md“验证与交付”里登记，否则不要加。
