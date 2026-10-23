# Crew 桌面版

原生 Electron 窗口、工作区首页和系统托盘。需要本机已有 Python 3、Git，以及实际要使用的 Claude Code / Codex；后端继续使用 Python 标准库。

```powershell
cd desktop
$env:electron_config_cache = Join-Path (Get-Location) '.cache/electron'
$env:ELECTRON_CACHE = $env:electron_config_cache
$env:ELECTRON_BUILDER_CACHE = Join-Path (Get-Location) '.cache/electron-builder'
npm install
npm start
```

首页可用系统文件夹对话框打开已有 Git 项目，子目录会解析到真实 Git 根目录。新建工作区先填写名称并选择保存位置，再创建一个新空目录和 `main` 分支 Git 仓库。已有目录不会被覆盖。工作区注册到 Crew 后，在原生窗口打开对应项目。

菜单「Crew → 工作区首页」或 `Ctrl+Shift+O` 返回首页。关闭窗口会隐藏到托盘，AI 和服务继续运行；托盘或菜单中的「退出 Crew」才退出应用。

启动时探测指定 home 的 `web.json`，健康的 Crew 服务直接复用。退出应用不关闭复用的服务。没有服务时，桌面版才启动自己的 `plugin/crew_desktop.py`，使用系统分配的端口，并通过专用 stdin 通道优雅关闭自己创建的后端。不会为了应用启动而重启当前服务。原有服务的版本由原服务决定。

```powershell
npm start -- --home C:\Temp\crew-desktop-private --port 0
npm test
npm run build
```

构建产物为 `dist/Crew-0.1.0-portable.exe`，可直接运行；`dist/win-unpacked/Crew.exe` 是未压缩版本。后端源码随应用放在 `resources/backend`，打包版会将后端按内容哈希保留到所选 home 的 `desktop/backend`，保证 portable 解包目录清理后 CLI 和守卫路径仍有效；Python 与 Git 使用本机安装。

`.npmrc` 将 npm 下载缓存放在本目录，以上环境变量将 Electron 和打包下载也限制在本目录。新的终端构建时同样配置缓存：

```powershell
$env:ELECTRON_CACHE = Join-Path (Get-Location) '.cache/electron'
$env:ELECTRON_BUILDER_CACHE = Join-Path (Get-Location) '.cache/electron-builder'
npm run build
```

隔离 smoke 测试会创建隐藏的真实 BrowserWindow、读取工作区首页、验证 renderer 没有 Node 权限、保存截图，并检查自己创建的后端正常退出。它必须显式使用隔离 home，拒绝正式 `47811` 端口，隔离 Claude/Codex/VS Code 数据源。不会开始模型轮次或接入现有会话。

```powershell
node scripts/smoke.cjs
node scripts/smoke.cjs dist/win-unpacked/Crew.exe
node scripts/smoke.cjs dist/Crew-0.1.0-portable.exe
node scripts/smoke.cjs --reuse
```

结果保存在 `smoke-output/source.json` / `packaged.json` / `portable.json` 及同名 PNG。单元测试另外覆盖已有服务复用、Git 根目录解析、新建仓库、已有文件保护与后端端口 0 / 优雅退出。

`--reuse` 会先创建一个完全隔离的测试服务，再检查真实应用退出后该服务仍存活；随后由测试服务的原始拥有者清理。不会复用生产 home 或正式服务。

Renderer 使用 `contextIsolation: true`、`nodeIntegration: false` 和 sandbox；IPC 仅接受自有工作区首页的主 frame，各 IPC 暴露独立函数。外部网页在系统浏览器打开，应用内导航限制在本机 Crew 服务。实现依据 Electron 官方的 [安全指南](https://www.electronjs.org/docs/latest/tutorial/security/)、[原生对话框](https://www.electronjs.org/docs/latest/api/dialog) 和 [Tray](https://www.electronjs.org/docs/latest/api/tray) 文档。
