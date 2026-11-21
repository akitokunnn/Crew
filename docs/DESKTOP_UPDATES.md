# Crew 桌面更新

## 获取和更新

正式版本发布在 [GitHub Releases](https://github.com/akitokunnn/Crew/releases)，每个 Release 包含：

- `Crew-<版本>-setup.exe`：Windows 安装版，按用户安装，提供开始菜单和桌面快捷方式。
- `Crew-<版本>-win-x64.zip`：Windows 目录版，解压到独立目录后运行 `Crew.exe`。
- `Crew-<版本>-portable.exe`：Windows 单文件便携版，运行时先解包；用户数据仍使用已有 HOME。
- `Crew-<版本>-mac-arm64.dmg`、`Crew-<版本>-mac-arm64.zip`：Apple 芯片 Mac 版（只有临时签名，没有 Apple 开发者签名）。
- `SHA256SUMS.txt`：以上全部包的 SHA-256 校验清单。

### 在 Crew 里更新

「工具与更新」的 Crew 卡片在启动时和每 6 小时查询一次最新**正式** Release（不含预发布和草稿），也可以随时点「检查」。有新版本时点「下载更新」，或勾选「有新版本时自动下载」（默认关闭）。下载只接受本仓库 Release 的下载地址及 GitHub 的附件域名，大小必须与发布记录一致，SHA-256 必须与同一 Release 的 `SHA256SUMS.txt` 一致，否则丢弃。下载保存在 `HOME/desktop/updates/<版本>/`，每一步写入 `HOME/desktop/logs/crew-self-update.jsonl`。

| 安装形态 | 下载校验之后 |
| --- | --- |
| Mac，`Crew.app` 所在目录可写 | 下载 zip，用 `ditto` 解开，检查 `codesign --verify --deep --strict`、包标识 `local.crew.desktop` 和版本号。点「重启并更新」：退出 Crew，独立的助手脚本把旧包改名为 `Crew.app.previous`、把新包放回原位置并打开。新版本连上后台后报告就绪，助手再删除旧包；3 分钟内没有报告就绪，就结束新版本、换回旧包重新打开。 |
| Mac，所在位置不可写（仍在 dmg 里、被系统隔离运行等） | 下载 dmg，点「打开 dmg」后手动把 Crew 拖到「应用程序」覆盖。 |
| Windows 安装版 | 点「重启并更新」：退出 Crew 后静默运行 `setup.exe /S --updated --force-run`，装回原目录并自动打开；安装器失败时重新打开原版本，安装包保留。 |
| Windows ZIP、便携版 | 只下载校验，点「在文件夹中显示」后手动替换：ZIP 解压到新目录运行，便携版替换原文件。 |

「重启并更新」只在所有会话、目标执行和运维修复都空闲时进行；有工作在跑时列出是哪些，不提供强制更新。空闲时先关闭新任务入口并备份数据库，再退出。连接局域网主机时只更新本机客户端，不检查主机上的会话。后台来源为「自定义」时，自定义后台不随 Crew 更新。数据目录、启动设置和局域网设备授权都不变。

0.3.15 起数据目录由 `~/.crew-v2` 改为 `~/.crew`、安装身份改为 `local.crew.desktop`：Windows 会把它当作新程序，需先在“应用”中卸载旧版 Crew（不删数据）再安装，旧数据用“连接与目录 → 复制现有数据”同一套 HOME 复制流程（`crew/home_migration.py`，会改写库内 HOME 路径）一次性迁移，原目录原样保留。启动后在“工具与更新”核对桌面及实际后台版本；Windows 必要时使用“后台与插件”卡片的“统一后台”应用当前包内源码。

## 维护者发布流程

1. 从本地 `live` 集成分支（与 `main` 对齐后）建立发布分支，更新 `desktop/package.json`、`desktop/package-lock.json` 的项目版本及 `plugin/.claude-plugin/plugin.json`，同步使用说明。
2. 在 `desktop` 执行 `npm ci`、`npm test`、`npm run build`，生成安装版、便携版与 ZIP。使用本目录的 Electron / electron-builder 缓存，不改依赖版本。
3. 执行 `node desktop/scripts/verify-bundle.cjs` 和 `verify-icons.cjs`；对目录程序、便携包及 ZIP 解压后的程序运行 `smoke.cjs`，使用隔离 HOME 和假载体核对真实启动、截图及所属后台退出。生成三个 Windows 包的 SHA-256 清单。
4. 检查差异，提交版本变更并通过 PR 合入 `main`，发版时让 CI 跑完并通过。核对最终源码与包内文件一致后，以合并提交创建 `v<版本>` 标签及正式 GitHub Release，**创建时一次附上**三个 Windows 包与校验清单（例如 `gh release create v<版本> <文件…>`）。
5. Release 发布后，`.github/workflows/mac-build.yml` 自动在 macOS 构建机核对标签与 `desktop/package.json` 版本一致、跑测试、打 arm64 的 dmg 和 zip、校验签名并冒烟，再上传到同一个 Release，并对全部 `Crew-*` 包重新生成 `SHA256SUMS.txt`。在 Actions 页确认这次运行成功；失败时 Mac 用户会看到「这个发布还没有适合本机的安装包」，修好后对该 Release 重新运行即可。
6. 回读 Release 的标签、提交、附件名称与大小，并核对上传资产的 SHA-256。发布不等于已更新用户安装：用户在「工具与更新」里自己下载和重启，不会被自动停止。

## 历史更新机制说明

0.2.6 基于当前 0.2.5 主干保留设置、工作区等功能，在「工具与更新」页增加真实线上版本检查。点击「检查 Crew」或页面顶部「检查更新」，分别显示当前桌面、最新正式发布、主干最新版本及查询时间。数据直接查询官方 GitHub 仓库，不使用本机版本冒充线上最新。

线上查询区分 GitHub Release 和主干源码：没有正式 Release 时显示「尚无正式发布包」，主干仍显示其实际版本；本机版本高于主干时标明含未发布更改。网络失败、超时、限流及无效返回会显示真实错误；不会把本机版本当成最新。可打开固定的官方发布页或当前主干提交查看详情。查询不会下载或执行远端代码。

「本机可应用版本」是当前桌面自带版本。「统一后台与插件」手动将后台、命令入口和已安装插件统一到此版本，不是线上下载安装按钮（Crew 本身的下载安装见上文「在 Crew 里更新」）。页面的「Claude/Codex 空闲时自动更新」只管理两个模型 CLI。

统一时关闭新任务入口，确认整个 Crew HOME 空闲，再用 SQLite backup API 保存数据库并检查完整性。任务或摘要仍在运行时等待，可取消；外部已收编成员仍存活时需先结束会话。过程核验插件实际来源、优雅停止自有后台、切换命令入口，并在原端口启动和验证新后台；已有工作区标签与草稿保留，备份路径显示在卡片中。

借用其他窗口后台、旧后台不支持准备协议、插件实际来源与当前桌面不同，会显示阻塞原因和处理步骤。停止超时不会启动第二个后台；失败保留备份并显示真实原因，不自动回退旧版本。已有 Claude 会话需重载插件或重开；新会话与成员启动仅初始化缺失入口，主动统一或显式安装才切换既有来源。

针对性测试：`cd desktop; node --test test/crew-updates.test.cjs test/crew-latest.test.cjs test/crew-self-update.test.cjs`。

2026-10-25 本机交付验证：真实公开 API 查询返回主干 0.2.5、无正式 Release；实际试用窗口显示本机 0.2.6 含未发布更改。桌面全部 86 项与更新关键 19 项通过，后端相关回归 186 通过、1 平台条件跳过，真实隔离更新和便携版启动通过。打包后端摘要及桌面 22 个源文件与验收源码一致。正式数据库备份后核对 SHA-256、数量和完整性未变化；正式任务未启动。桌面快捷方式及本机插件、CLI 已同步 0.2.6。
