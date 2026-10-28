# Crew 正式前端接入记录

本轮由前端 AI 负责 `plugin/crew/web/`，后端 AI 负责领域、HTTP、桌面桥接及最终集成。前端分支为 `codex/crew-formal-frontend`，起点 `4294977`；本记录不代表正式服务已经升级。共享字段以 `CREW_API_CONTRACT.md` 为准。

## 启动与体验

在包含本轮前端的仓库根目录执行，无需安装前端依赖或构建：

```powershell
node plugin/crew/web/dev-server.cjs --port 4320
```

打开 `http://127.0.0.1:4320/?data=mock`。这是明确标注的合成数据，状态只保存在浏览器 `crew.synthetic.frontend.v1` 下，不启动真实 AI，也不调用正式 Crew。可带 `scenario=offline|conflict|unknown|empty` 体验失败、冲突、未知回执和空状态。

真实接口使用显式本机代理：

```powershell
node plugin/crew/web/dev-server.cjs --port 4321 --backend http://127.0.0.1:后端独立端口
```

打开 `http://127.0.0.1:4321/`，**不带 `data=mock`**。开发服务只绑定 loopback，代理只允许显式 loopback 后端，保留流式响应、认证 Cookie 和文件字节。未配置后端返回真实 503，不回退样例。生产由 Crew 的 AssetCatalog 发布同一份 HTML/JS/CSS，无需此开发代理。

隔离后端启动见后端负责的 `CREW_BACKEND_INTEGRATION.md`。不能拿正式 HOME 或正式端口开发。

路径与操作：

- 首页 → 任务 → 资料 → 成员 → 点击成员 → 顶部任务／leader 返回 → 资料中的成果与文档。
- `#home?project=名称` 为工作区首页；已有 `?project=名称` 链接也进入该工作区。
- `#task?taskId=ID&project=主工作区&sessionId=成员ID` 为任务／成员直达。
- 首页“开始聊”先建立稳定 discussion，再以固定 message_id 发送原话；这不授权施工。“授权开始任务”是另一项明确操作。
- 置顶常驻导航；Git 带工作区身份。关系总览、总会话、公共频道、用量额度、待收编、完整工具／渠道／数据设置继续通过原功能页访问，有返回工作台入口。
- 手机使用相同 URL。`mobile-preview.html` 只供开发服务检查 390px 内嵌视口，不是生产入口。

## 功能所有者

| 文件 | 职责 |
| --- | --- |
| `workbench.js` / `workbench.css` | 全局导航、首页投影、路由、连接与作用域组合 |
| `workbench-ui.js` | 少量呈现工具、按 real/mock 隔离的草稿与回执缓存 |
| `conversation-entry.js` | 创建／关联任务和首条原话的两个独立回执；未知结果复用身份 |
| `task-workspace.js` / `.css` | 任务控制、成员来历、详情编辑、按需资料及阅读布局 |
| `task-attention.js` | 组合现有拍板、TODO、原生审批；细节留在原任务 |
| `task-deliverables.js` / `.css` | 持续报告、附件、阶段版本、恢复预览与冲突 |
| `rich-documents.js` | 通过本地 Mermaid 严格模式增强图示／图表，失败保留原文 |
| `personal-workspace.js` | 一张个人清单、标签／归属、任务关联、完成证据与归档恢复 |
| `scoped-settings.js` / `.css` | 三级配置、来源、Crew 提示词、历史及明确执行的加载证据 |
| `remote-settings.js` / `.css` | 真实远程配置、登录会话与退出；不声称已部署公网 |
| `crew-client.js` | JSON、流、字节、资源 URL、桌面能力的统一调用层 |
| `crew-mock.js` / `dev-server.cjs` | 开发合成数据和显式本机代理，均不创建平行生产后端 |

继续复用现有 `CrewChat`、UI actions/renderer、attention/native approval、Git、存储、用量等模块。`app.js` 保留原功能页的组合职责，通过 `legacy=1` 显式挂载；不是删除既有能力。窗口原生 confirm 已从新任务控制中移除，使用可取消的 DOM 弹窗，避免阻塞整个桌面宿主。

## 接口接线

下表是客户端已接线范围。后端是否 verified 仍以共享契约为准，不能把客户端单元验证当成所有后端业务已验收。

| 范围 | 使用接口 |
| --- | --- |
| 首页与导航 | projects、tasks、tasks/attention、tasks/pin；全局 state 提供主题目录 |
| 任务 | task、tasks/create/update/mode/start/resume/stop/archive/trash/restore |
| 对话 | chat/history、chat/events、chat/send、chat/interrupt、tasks/schedule；成员 affects_task 与 dialogue/work 分开 |
| 原任务处理 | 沿用 answer、decision/*、todo/resolve 与原生 permission 接口 |
| 个人事项 | personal/items、capture、update、items/link、items/chat、todos/update、todos/complete |
| 助手 | assistant/open/send；固定助手所在 project 作为 history/SSE anchor |
| 配置与提示词 | runtimes、settings/effective、settings/scope、prompts、save/reset/history/applied |
| 成果 | tasks/report、attachments/attachment、versions/preview/restore、version-file；保留历史 HTML 报告与任务文档入口 |
| 会话与远程 | auth/session/login/logout、remote、remote/configure；已有本机后台设置入口保留 |
| 原有功能 | 原接口经统一 client 调用，继承各功能模块的原生错误和能力边界 |

任何真实错误均保留实际原因。409 不重基用户草稿；未知发送、上传和完成回执不自动重试，也不换身份假装成功。新配置不会因保存而启动或重启 AI。停止当前回复与停止整个任务使用不同接口。

## 实际验证

本轮浏览器通过 `4321 → 2957` 代理，连接从后端主工作区启动的真实 HTTP、临时数据库和假 Claude 载体；该实例启动时源码版本为 **0.2.41**。后端后续的版本更新不改变这个已运行测试实例的证据。

已实际操作并观察到：

- 首页读取列表、直接对话创建同一 discussion、发送原话、流式假载体回复；普通讨论未变成执行授权。
- manual → automatic 保存后仍为 discussion；单独授权开始才运行。任务停止回执最终显示 stopped。
- 提示词原文读取、保存到真实全局范围、版本与来源更新；未冒认当前执行已加载。
- 持续报告真实保存、宽阅读、表格、Mermaid 流程图和柱状图本地渲染。
- 同一 TODO 关联两个任务；上传真实文本附件并完成反馈；完成记录保留文字与认证附件入口，手动停止任务仍是 stopped。
- 普通文件夹的真实版本预览：未登记的后续文件修改被阻止覆盖；登记同任务后续版本后，明确预览再恢复 v0.1，磁盘字节确实回到 v0.1，且没有生成 `.git`。
- 样例的首页 → 任务 → 成员 → leader → 成果，leader/成员草稿各自保留；手机资料展开和提示词页面保持单栏。

自动验证：

```powershell
node --test plugin/tests/test_*.js
```

标准 Node 入口 **358/358 通过**。新增功能自带契约测试通过 `test_frontend_workbench.js` 进入标准入口；涵盖真实失败不降级、SSE 回放／取消、两种停止、排队、回执身份、编辑冲突、上传复用、设置继承和路由卸载。静态资源发布测试：

```powershell
Push-Location plugin
python -m unittest discover -s tests -p test_web_assets.py -v
Pop-Location
```

AssetCatalog 10 项中 9 项通过，1 项 Windows 符号链接权限条件跳过。未用正式数据库、正式原生历史或 QQ/Lark 账号测试；未启动真实 Claude/Codex。

## 集成交接

1. 后端负责人保留本分支全部前端文件，按共享契约与最终后端提交联合回归，再正常合并、构建。前端 AI 不替代其最终集成职责，也未修改桌面来源与桥接。
2. 桌面首页／WebContentsView 需能进入服务根 URL；核心网页不需要 preload。打开电脑目录没有真实桥接时提供路径，不伪装已打开。
3. Codex 与成员的实际原生执行、QQ/Lark 投递、桌面退出后续跑、真实远程 HTTPS 登录／SSE／文件，以及最终 EXE 仍由整体集成验收；本轮浏览器假载体主要覆盖 Claude 协议，不宣称真实双载体联机或整夜运行通过。
4. 公网域名、账号、Tunnel 凭证没有在本前端任务中配置，远程页面明确显示未验证部署。不要把本机开发代理当公网方案。
5. 隔离夹具旧版将工作区放在 HOME 内，会被正确的版本文件 guard 拒绝。本轮另外注册 HOME 外临时普通文件夹完成版本验收；问题已反馈后端修夹具，未放宽路径保护。

当前可体验服务均为本任务自己的开发进程，不等于正式 Crew 已更新。正式实例保持原状。
