# Crew 持续任务工作台交付记录

2026-10-28 · 桌面/插件源码 0.2.42

后端、已认可的正式前端和桌面接入通过 [PR #62](https://github.com/akitokunnn/Crew/pull/62) 正常合并，主干提交 `4eda2d3af90e536d7e3e08bdfc875057e39b1749`。已验证主干包含后端和前端全部交付提交，完整树与通过 CI 和打包验收的产品提交 `7902b03` 一致。前端独立工作树及原始提交保留；未升级正式运行实例。

## 交付内容

新工作台提供稳定任务、直接讨论、任务级授权与模式、持久调度、成员对话、停止/恢复、待处理与个人清单。设置按全局→工作区→AI 继承，模型和 effort 依据原生选择与发现；Crew 提示词可编辑并保留版本/应用证据。持续报告、真实附件、普通目录及 Git 文件版本恢复、垃圾箱和 crewpack 导出导入沿用现有存储与执行基础。

桌面默认进入真实服务的新工作台，关系总览、Git、用量、渠道、历史及本机管理仍可达。Ctrl+T 返回工作台，Ctrl+Shift+O 打开工作区管理。正常退出界面后后台继续运行，显式“停止后台与全部执行并退出”才结束后台。远程登录、会话撤销、来源与文件归属检查已经接入。

联调修复了任务详情完整表单重复追加原文、载体切换携带旧模型/effort、按模型名猜测原生能力、角色默认值被前端覆盖，以及桌面取消跳转时提前销毁页面的问题。

## 实际验证

[Windows CI 40658508929](https://github.com/akitokunnn/Crew/actions/runs/40658508929) 的两个任务通过，验证产品提交 `7902b03`：

| 范围 | 结果 |
| --- | --- |
| Python 全套 | 2372 项，417.622 秒，1 项平台条件跳过 |
| 网页 / 桌面单元 | 382 / 183 项通过 |
| 实际 Electron 工作区 / 旧会话路由 | 45 / 8 项通过 |
| 真实 HTTP + Electron 新工作台 | 15 项通过，含讨论/SSE、草稿、模式不授权、详情编辑、报告附件与实际 390×844 视口 |
| 最终包来源 | 321 文件逐一匹配；目录、portable、setup 图标检查通过 |
| 目录与 portable | 实际加载真实新工作台；实际窗口正常退出后后台存活、同实例重连，再由测试只停止自有后台 |

全部测试使用隔离 HOME、临时项目与假载体。CC/Codex 原生行为由各自协议夹具验证；未唤醒真实 AI、连接正式渠道账号或使用正式私人数据库。390px 是真实浏览器视口，未冒称手机硬件/软键盘或整夜运行验收。未在用户系统安装 setup。

逐项能力、组件测试与专项未实测边界见[执行记录](CREW_IMPLEMENTATION_EXECUTION_2026-10-28.md)和[前端交接](CREW_FRONTEND_INTEGRATION.md)。历史失败及修复保留，不把旧失败运行记作通过。

## 可体验产物

文件位于 `C:\Tools\Crew\desktop\dist`。日常使用可解压 ZIP 后运行 `Crew.exe`，也可使用 portable 或 setup。新界面默认会复用所选 HOME 的已登记健康后台，旧后台的版本不会因打开新 EXE 自动变化。

需要独立体验新包时，使用全新 HOME，避免复用正式后台：

```powershell
$crewPreviewHome = Join-Path $env:TEMP ('crew-preview-' + [guid]::NewGuid().ToString('N'))
& 'C:\Tools\Crew\desktop\dist\win-unpacked\Crew.exe' --home $crewPreviewHome --port 0 --isolated-sources
```

此命令不会由交付过程自动执行；添加工作区和授权开始任务仍由体验者明确操作。

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `Crew-0.2.42-win-x64.zip` | 172737114 | `70ba2b4450a7ef3b850b8af788d2b40a46374b360943b3fc03500709c5bcd3a3` |
| `Crew-0.2.42-portable.exe` | 111222892 | `f710167403bc5ee83ee174830dfd72831a25e46100b7756eb7dfccf0969cd2bb` |
| `Crew-0.2.42-setup.exe` | 111442549 | `6aba670bb0e6d6aa86c674dd92e95810ba6ac6208ef6ec7548bddd4cb268fca9` |

包内 source digest：`aaf994ed01461a106f8d7c0f0faa2d8c3c57b6df40106d1ba3c61836ab32711a`。证据位于 `desktop/smoke-output/bundle-source.json`、`{packaged,portable}.json/png`、`background-{packaged,portable}.json` 与 `task-workbench/`。构建日志为 `Temp/full-implementation/build-final-after-lifecycle.log`。

## 唯一剩余外部操作

用户提供并绑定自己的 Cloudflare/Tunnel 账号与域名，再进行实际公网 HTTPS 访问验收。代码、配置入口、配置检查、部署步骤及隔离反向代理已完成，见[部署说明](CREW_BACKEND_INTEGRATION.md)；当前未上线，也没有假公网链接。正式 Crew、正式数据库和用户 HOME 未升级。

一处旧自有测试目录的删除被自动审批以 “blocked by policy” 拒绝，目录原样保留，未绕过限制；详情见执行记录。
