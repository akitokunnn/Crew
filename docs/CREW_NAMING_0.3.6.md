# Crew 0.3.6 名称调整

按用户要求，产品统一叫 Crew，以版本号区分交付。0.3.6 包含 0.3.5 两边已汇合的 ZeroTier、QQ/Lark 迁移与报告预览功能。

- 应用名、窗口、助手页面标题、安装器、快捷方式和卸载入口统一为 Crew。
- 产物为 `Crew-0.3.6-portable.exe`、`Crew-0.3.6-setup.exe` 和 `Crew-0.3.6-win-x64.zip`，ZIP 中运行 `Crew.exe`。
- 更新页面及新建记录提示去掉 V2；通知测试文案统一为 Crew。本轮不重复发送真实测试提醒。
- 沿用安装 appId、`.crew-v2` 数据目录与原启动设置目录；不移动用户历史，不改变数据库代际、工作区身份和开发分支。

验证日志在 `Temp/crew-naming-0.3.6/`。桌面测试 192 项、前端 234 项、Python 更新/身份/存储接续/连接迁移与提醒测试 64 项通过。命名规则用旧产品模块在内存中反证：`Crew V2` 与用户要求的 `Crew` 不符时真实断言失败；当前源码通过。

实际原生窗口验收使用隔离 HOME、真实后台与假载体，无真实 AI，也没有改动正式数据库或安装。隐藏子窗口截图初次因 Windows 显示表面不可用而失败，原日志保留；使用测试脚本已有的可见窗口模式复验，避免用空白截图冒充界面。

`CREW_TEST_SHOW_WINDOW=1 node desktop/scripts/smoke-workspaces.cjs` 通过 47 项，控制台非预期错误为空，后台已退出；截图在 `desktop/smoke-output/workspaces/01-workspaces.png`，原生标题和主页均为 Crew。该流程还验证旧文件夹选择全新记录、原身份和文件保留。变更后的命名元数据测试、更新兼容测试与界面测试相互补充。

安装包仅构建与检查，不覆盖用户当前安装。构建、原生交互与最终包验收结果以对应日志及 `desktop/smoke-output/` 为证据。

最终构建通过；`Crew.exe` 和便携包均通过隔离启动、截图与所属后台退出验证，三个 EXE 图标验证通过。Windows 文件元数据 ProductName 和 FileDescription 均为 Crew，版本 0.3.6.0。`verify-bundle.cjs` 校验 260 个包内文件与源码一致，清单摘要 `e4e24ad5a78d3a7b684a0c6ddf65cbf7d040edd2c7c3ca27e089c8da3e5883a2`；产物 SHA-256 在 `Temp/crew-naming-0.3.6/artifacts.json`。
