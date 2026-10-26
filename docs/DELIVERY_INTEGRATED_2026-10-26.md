# Crew 0.2.22 完全整合交付

日期：2026-10-26。桌面、随包后台与 Claude 插件元数据均为 0.2.22。

## 交付行为

- 接入独立本地历史库、工作区永久身份、打开时选择旧历史、完整 `.crewpack` 导入导出和只读历史页；独立副本可明确绑定本机目录后接续，执行保持停止。
- 项目侧栏增加“项目资料”；目标自己的交付与资料继续保留。复用 CC/Codex 的 `crew_context` 增加有范围、有字数限制、按版本跳过已读内容的历史检索，另可生成 Codex / VS Code 交接材料。
- 保留主干的目录重定位、组员大脑默认值、通知渠道偏好、存储清理、运维、跨工作区动态与生命周期优化；包含 PR #33 的中文输入组合保护和启动不再全组盘点。
- 旧历史包预览改用 `/api/workspace-data/`，与现有 `/api/storage/` 清理预览区分。只读历史不探测代码目录，也不轮询执行动态；已开始的数据操作完成后才退出自有后台。
- 恢复适配新版设置及维护表：全局设置不随单个工作区恢复；未结束维护标为中断，当前清理边界和历史维护回执的范围一起映射到新记录。移除旧的启动批量自动恢复函数，由用户在打开时选择来源。

## 现场与范围

原目录 `C:\Tools\Crew` 位于 `codex/crew-update`，HEAD `b159fc5`，桌面元数据 0.2.7。盘点取得 39 个修改文件、53 个未跟踪文件，共 92 个文件。整合实施目录独立于原目录，从 `b95cf53`（0.2.21）开始，并汇合 `ddd0e50`（PR #33）及 `bf24003`（PR #34）。

原目录的 92 个文件均保留；收尾逐一核对 SHA-256 与开工快照一致。`department-maintenance` / `codex/ime-input-stability` 只读核实，开工时为干净工作树，未作改动。本轮没有更改其它 AI 的工作树、分支、正式 HOME 或正式服务。

快照与逐文件摘要在实施目录的 `Temp/integration/manifest.json`、`working/`、`base/`、`main/` 和 `proposed/`，属于忽略的本地证据。初始三方比较中 5 个文件已经逐字等于主干，另 4 个修改在三方合并后无剩余差异；其余逐项处理，不以旧目录覆盖新主干。

## 验证

| 验证 | 实际结果 |
|---|---|
| Python 全量 `unittest discover` | 2002 项：1995 通过、7 跳过，663.2 秒；跳过为 6 项 Windows 符号链接权限和 1 项 opt-in 零模型原生沙箱夹具 |
| 清理历史范围补丁后的恢复/迁移/HTTP 专项 | 42 项通过 |
| 网页 Node 单元测试 | 184 项通过 |
| 桌面 Node 单元测试 | 166 项通过 |
| 存储设计及领域覆盖 | 分别 18、8 项通过 |
| 原生 Electron 工作区及项目资料 | 41 项通过；真实登记、读取、标记及来源对话草稿保留 |
| Electron 历史页 | 20 项通过；205 人/会话、215 份报告、120 条消息分页，保存文件和附件真实字节及第三个独立 HOME 恢复 |
| 存储清理交互 | 7 项通过 |
| 设置及输入法 | 设置 renderer 验收通过；真实 Chromium 组合输入跨轮询、中文无拼音残留、仅显式确认提交 3 项通过 |
| 最终打包 | ZIP、portable、NSIS setup 均生成，三份 EXE 的原生图标资源验证通过 |
| 成品与源码 | 213 个桌面文件、后台文件和随包 skill 逐字核对，交付目录副本同样通过；汇总摘要 `dbba30f1297cc6c3c597d8a16fc6896562a954848db7cb5a432b54fff1f4bd2c` |
| 最终成品隔离启动与退出 | 目录版、portable、ZIP 解压版各冷/热 HOME，共 6 次；真实版本 0.2.22、自有后台退出、注册文件移除、假载体守卫均通过，模型启动数 0 |

全量回归启动后补齐了维护回执内的范围映射，该小补丁随后单独复跑上述 42 项，不把此前全套称作对该补丁重新跑过。汇合最新主干的 merge commit 与已验收实现提交的内容树相同。新 DDL 保留原始字节及末尾空行，避免无意义地改变数据库 schema 摘要；其余空白和最终 diff 已检查。PR 当前没有上报 CI 检查，以上均为实际本地验证。

所有运行测试使用临时目录、隔离 HOME 与假载体；没有执行真实模型轮次或修改正式数据库。安装器未在用户系统安装，正式运行实例未替换。云端 PostgreSQL、账号、同步、执行租约以及跨工具原生会话互转未实现；本轮提供的是本地历史及可读交接材料。

## 版本、提交与可用产物

- 实现提交：`08a192b`；汇合最新主干且内容树不变的提交：`5d68b65`。
- 整合 PR：[akitokunnn/Crew#35](https://github.com/akitokunnn/Crew/pull/35)，正常走受保护主干的 PR 合并。
- 交付根目录：`C:\Tools\Crew\Temp\deliveries\complete-integration-0.2.22`。
- 日常运行：`Crew-0.2.22-win-x64/Crew.exe`；可分发 ZIP：`Crew-0.2.22-win-x64.zip`；另含 `Crew-0.2.22-portable.exe`、`Crew-0.2.22-setup.exe` 和校验清单 `artifact-manifest.json`。
- 证据复制到交付目录的 `validation/`：完整日志、逐文件处置快照、源包比对、界面截图及六次成品启动记录。复制后的文件摘要与构建输出一致。

最终成品 renderer 就绪实测：目录版冷/热 HOME 为 1.90 / 1.31 秒，ZIP 解压版为 1.76 / 1.28 秒；单文件 portable 为 7.70 / 7.05 秒，其中 Electron 创建前约 5.7–5.8 秒包含每次解包。ZIP 一次解压约 1.98 秒。此结果不是清空操作系统磁盘缓存后的基准。


## 原目录逐文件处置

“保留主干”表示原功能已经合入或被后续实现扩展；该文件如另有本轮必要改动，仍按最终 diff 验证。“移植并适配”保留独立行为，并按现有调用链完成兼容。历史云端 SQL / 协议只作为设计文档入库，不作为已实现功能。

| 原文件 | 处置 |
|---|---|
| `README.md` | 旧修改已在主干；补充本轮使用入口 |
| `desktop/README.md` | 按实际打开流程重写说明；不保留启动自动恢复旧说法 |
| `desktop/package-lock.json` | 旧版本号不移植；发布元数据统一为 0.2.22 |
| `desktop/package.json` | 旧版本号不移植；发布元数据统一为 0.2.22 |
| `desktop/src/core.cjs` | 移植独立差异并适配新主干 |
| `desktop/src/home-settings.js` | 保留主干；旧补丁已合并或被新版扩展 |
| `desktop/src/home.html` | 移植独立差异并适配新主干 |
| `desktop/src/home.js` | 移植独立差异并适配新主干 |
| `desktop/src/main.cjs` | 移植独立差异并适配新主干 |
| `desktop/src/preload.cjs` | 移植独立差异并适配新主干 |
| `desktop/test/core.test.cjs` | 移植独立差异并适配新主干 |
| `desktop/test/settings-smoke.cjs` | 保留主干；旧补丁已合并或被新版扩展 |
| `docs/QQ.md` | 保留主干；旧补丁已合并或被新版扩展 |
| `docs/REMOTE_CHANNELS.md` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/.claude-plugin/plugin.json` | 旧版本号不移植；发布元数据统一为 0.2.22 |
| `plugin/crew/channel_digest.py` | 移植独立差异并适配新主干 |
| `plugin/crew/cli.py` | 移植独立差异并适配新主干 |
| `plugin/crew/codex_tools.py` | 移植独立差异并适配新主干 |
| `plugin/crew/goal_actions.py` | 移植独立差异并适配新主干 |
| `plugin/crew/notifications.py` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew/schema.sql` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew/store.py` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew/web.py` | 移植独立差异并适配新主干 |
| `plugin/crew/web/app.js` | 移植独立差异并适配新主干 |
| `plugin/crew/web/chat.js` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew/web/documents.css` | 移植独立差异并适配新主干 |
| `plugin/crew/web/documents.js` | 移植独立差异并适配新主干 |
| `plugin/crew/web/index.html` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew/web/settings-ui.css` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew/web/settings-ui.js` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew_desktop.py` | 移植独立差异并适配新主干 |
| `plugin/skills/leader/SKILL.md` | 移植独立差异并适配新主干 |
| `plugin/skills/member/SKILL.md` | 移植独立差异并适配新主干 |
| `plugin/tests/test_channel_digest.py` | 移植独立差异并适配新主干 |
| `plugin/tests/test_documents_ui.js` | 移植独立差异并适配新主干 |
| `plugin/tests/test_goal_notifications.py` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/tests/test_native_approvals.py` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/tests/test_notifications.py` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/tests/test_settings_ui.js` | 保留主干；旧补丁已合并或被新版扩展 |
| `AGENTS.md` | 保留主干；旧补丁已合并或被新版扩展 |
| `CLAUDE.md` | 保留主干；旧补丁已合并或被新版扩展 |
| `desktop/src/storage.cjs` | 移植独立实现并完成回归 |
| `desktop/test/storage.test.cjs` | 移植独立实现并完成回归 |
| `docs/storage-v2/README.md` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/cloud.sql` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/data-dictionary.md` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/export-format.md` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/generate_postgres.py` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/implementation.md` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/legacy-mapping.md` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/local.sql` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/package.schema.json` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/postgres-domain.sql` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/reference_export.py` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/security.sql` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/sqlite.sql` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/sync-protocol.md` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/validation.md` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/verify_design.py` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/verify_domain_coverage.py` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `docs/storage-v2/verify_sql_contract.py` | 纳入设计及本地实现文档；补齐新版表映射、历史标记及验证边界 |
| `plugin/crew/connections.py` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew/lark.py` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew/storage_service.py` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/__init__.py` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/__main__.py` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/activation.py` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/context.py` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/database.py` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/domain.sql` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/legacy.py` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/local.sql` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/objects.py` | 移植独立实现并完成回归 |
| `plugin/crew/storage_v2/packages.py` | 移植独立实现并完成回归 |
| `plugin/crew/web/connections-ui.js` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/crew/web/storage-history.css` | 移植独立实现并完成回归 |
| `plugin/crew/web/storage-history.html` | 移植独立实现并完成回归 |
| `plugin/crew/web/storage-history.js` | 移植独立实现并完成回归 |
| `plugin/crew/workspace_recovery.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_connections.py` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/tests/test_lark.py` | 保留主干；旧补丁已合并或被新版扩展 |
| `plugin/tests/test_storage_context_bridge.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_storage_http.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_storage_legacy.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_storage_service.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_storage_v2_activation.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_storage_v2_context.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_storage_v2_database.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_storage_v2_objects.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_storage_v2_packages.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_workspace_recovery.py` | 移植独立实现并完成回归 |
| `plugin/tests/test_workspace_recovery_startup.py` | 移植独立实现并完成回归 |
