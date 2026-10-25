# Crew 开发指南

适用于本仓库及其子目录；更具体的子目录指南另行补充。先核实下面的现场，再判断版本和功能，不要把旧报告当作当前状态。

## 开工先确认

在你实际工作的仓库根目录执行：

```powershell
Get-Location
git status --short --branch
git log -3 --oneline
(Get-Content desktop/package.json -Raw | ConvertFrom-Json).version
```

- **当前检出的桌面版本**以 `desktop/package.json` 为准，同时核对 Git 提交与未提交改动。包版本不代表已发布、已合并或已部署；需要这些结论时，再核对远端、PR、产物和实际服务。
- `plugin/.claude-plugin/plugin.json` 是 Claude 插件的版本元数据，不能用它代替桌面版本。旧文件名、缓存、截图、聊天和历史交付报告也不是版本来源。
- **源码、构建产物、运行中服务分别核对。** 新 EXE 可能复用旧后台；查看该实例的工作目录、所选 home、服务登记及启动来源。未核实就写“未确认”，不要猜。
- 文档中的“当前”“尚未实施”“正式服务已停止”只代表写作当时。日期化审查、版本交付记录和原型都是历史材料；功能现状看当前代码、测试和隔离验收。

## 工作方式

- 这是现有产品的迭代：leader/成员关系、QQ、置顶、多工作区、拍板、人工待办、目标及文档等能力已有实现，先找现有链路再改，避免另起一套。
- 普通文件夹可作为工作区，不强制 Git，也不自动 `git init`。Git 相关能力才需要仓库。
- CC 与 Codex 都是 AI 载体，现有功能需考虑两者。模型与 effort 使用原生发现及用户设置，不把某台机器的清单写死。
- leader 优先自己推进，确有必要再开成员；减少无效汇报、重复上下文和模型唤醒。普通交流直接说话，用户要求的最终目标交付再生成报告。
- 错误保留真实原因和用户输入；不静默换路径、吞错或自动重做已经成功的操作。别为假想风险堆兼容分支。
- 测试只使用隔离 home、临时目录和假载体。未经用户明确安排，不停止或重启正式 Crew、不唤醒真实 AI、不写正式数据库，也不因开发而更新其源码。
- 多个 AI 可能共用机器和目录。开工先看 Git 状态；已有冲突或他人改动不得 reset、clean、覆盖或擅自解决。优先复用自己的干净 worktree，协调同一文件的编辑。
- 提交前看 diff，只提交本任务文件；提交信息用中文说明改了什么、为什么，不加署名行。PR、批准和合并遵循仓库规则及用户授权；不能为赶进度绕过分支保护。

## 代码入口

| 范围 | 入口 |
| --- | --- |
| 桌面窗口、IPC、标签、启动/复用后台 | `desktop/src/main.cjs`、`core.cjs`、`tabs.cjs` |
| 工作区首页、全局设置、网页 UI | `desktop/src/home*`、`plugin/crew/web/` |
| HTTP / CLI | `plugin/crew/web.py`、`cli.py` |
| 会话启动、停止、载体切换 | `chat.py`、`spawn.py`、`codex_runtime.py`、`runtime_switch.py`（均在 `plugin/crew/`） |
| 目标、轮次、动态上下文 | `plugin/crew/goals.py`、`goal_runtime.py`、`goal_context.py` |
| 工作区改名、迁移、可恢复移除 | `plugin/crew/workspace_settings.py`、`workspace_execution.py` |
| 数据与后台守护 | `plugin/crew/store.py`、`schema.sql`、`supervisor.py` |
| 按需运维对话与修复配置 | `plugin/crew/operations_chat.py`、`operations_settings.py`；桌面入口 `desktop/src/operations.cjs` |
| 桌面连接/程序来源与 HOME 迁移 | `desktop/src/launch-settings.cjs`、`plugin/crew/home_migration.py`；配置先检查再应用，启动设置在 HOME 外 |

不要直接修改 `desktop/dist/`、`node_modules/`、缓存中的后端或用户 home 来代替源码修复。

## 验证与交付

先跑与改动相关的验证；纯文档改动无需跑全套或重新打包。在测试进程中清理继承的 `CREW_SESSION_ID`、`CLAUDE_CODE_SESSION_ID`，避免把外层身份带入临时白板，不改用户的持久环境。需要完整验证时，在根目录执行：

```powershell
Push-Location plugin
python -m unittest discover -s tests -v
Pop-Location
node --test plugin/tests/test_*.js
npm --prefix desktop test
```

桌面隔离交互：`node desktop/scripts/smoke-workspaces.cjs`。构建、便携包验收、图标检查见 [desktop/README.md](desktop/README.md)；使用方式见 [README.md](README.md)。

交付说清改了什么、验证结果、尚未验证之处、commit/PR 与产物位置；优先讲最有价值的改动。改变版本号、入口或工作方式时，同步修正文档。指南不维护另一个“最新版本”数字。
