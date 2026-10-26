# 本地工作区数据与接续

Crew 接入独立的本地历史库，位于 `<CREW_HOME>/storage-v2/crew.sqlite3`。领域结构有 55 张表；本机目录、设备、导入映射和执行绑定另放在 `local_*` 表。文件内容按工作区及 SHA-256 保存于 `storage-v2/objects/`。工作目录中 `.crew/workspace.json` 只保存永久工作区 ID 和格式版本。桌面版本以 `desktop/package.json` 为准。

HTTP 接口使用 `/api/workspace-data/`（发现、打开、导出、包预览、导入、接续、历史和交接材料）；`/api/storage/` 继续用于存储占用和清理，两种预览保持独立。隔离历史界面验收在仓库根目录执行 `node desktop/scripts/smoke-history.cjs`，结果位于 `desktop/smoke-output/history/`。

## 使用

- 打开已有目录时先检查当前数据及旧版 Crew 数据。发现历史后选择「升级并接续」「仅查看历史」或取消；多份历史需要选择来源。启动应用本身不再默默替用户选择来源。
- 新库可直接读取时按原身份打开；目录有身份标识但本机没有历史时，提示导入，避免把它登记成空工作区。目录移动只需重新绑定位置，历史记录与文件不随代码目录搬来搬去。
- 首页每个工作区有「导出」，工作区菜单也有导出入口。`.crewpack` 包含单个工作区领域库、必要公开作者资料和实际产出文件字节；不包含本机运行绑定、凭据、全局私有快照及未登记的整个代码目录。
- 首页「导入工作区包」先验证包，再选择恢复原身份的只读历史，或复制为新工作区。原身份恢复不会覆盖已有历史，也不会取得原电脑的执行权限；独立副本可以显式绑定本机目录后接续。
- 工作区菜单的「生成 Codex / VS Code 交接材料」生成 `handoff.json`、`handoff.md` 及能校验的报告文件。复制提示后，在目标工具中读取材料即可建立接续上下文。原始要求和约束单独保留，必要片段有长度限制；交接材料不是完整备份。

## 动态上下文与 token

复用现有 `crew_context`，增加 `retrieve` 动作，不新增一组数据库工具，也不将数据库结构或整个历史自动写进提示词。例：

```json
{"action":"retrieve","data":{"query":"数据库迁移","limit_chars":3000,"known_versions":{}}}
```

宿主校验 Crew 人员和目标归属，将旧目标 ID 映射到同一工作区的新目标身份。检索只返回本目标和适用的共享材料；找不到映射会报错，不退化成全工作区查询。`limit_chars` 上限 6000，包含片段来源和版本的 JSON 字符数，不冒充精确模型 token 数。下一次可传回结果中的 `versions`，跳过已经完整读取且未变化的材料；截断片段不会被标成完整已读。

数据库、校验、迁移、导入、导出和交接文件生成均由程序执行，模型调用数为零。模型主动读取工具结果时，工具请求及实际返回的内容照常占用上下文。真实 token 数取决于模型和内容，不能由数据库大小推算固定百分比。

## 运行边界

现有执行引擎仍使用 `crew.db` 的兼容接口，保证原任务和原生工具运行逻辑继续工作。打开、导出，以及按需检索发现新目标、人员或要求版本变化时，迁移适配层生成或增量更新已选择来源的可移植历史；检索不会自行选择旧实例或启动执行。这里还没有把所有旧执行 SQL 改写为新 Repository API；新库不是一个可以让两个引擎同时随意修改的第二运行库。

旧数据经只读 SQLite backup 获取一致快照；完整原始数据库和原始原生日志只保存在本机私有 migration vault。可移植领域数据以及逐行迁移回执保存在新库；无法规范化的旧表明确标为 archive_only，完整原始记录留在本机快照。登记资料、报告和可见聊天正文进入可移植包；用于执行的本机路径绑定、认证凭据、原生会话令牌及执行权限不会跟随导入。历史正文中的旧路径保留为历史事实，接续以用户新选择的本机目录为准。

激活前保存双库备份及 fsync 操作 intent。启动 worker 前先检查未完成的本机激活：一侧完整时核对停止状态和内容指纹后修复当前工作区的映射与回执；未提交核心数据时仅撤销本次本机 metadata 并重试。其它工作区的新写入保留；证据不符或出现新原生绑定时停止自动恢复。已验证确定性的 partial commit 场景，尚未进行真实断电故障注入。

执行进程不属于便携历史。相同工具且本机仍有兼容原生记录时，可使用工具自身的恢复能力；跨工具、跨电脑使用交接材料建立新执行。现有 Claude→VS Code 原生接续入口保留；Codex App/IDE 的原生会话一键互转尚未接入，不能把生成交接文件称作已经搬迁了原会话。

云服务、账号登录、增量云同步及跨设备执行租约暂未实现。云端 SQL 与同步协议仍是后续设计；本轮不会建立任何云资源或后台上传。

## 数据 CLI

在 `plugin` 可导入的 Python 环境中，可运行以下纯数据命令：

```text
python -m crew.storage_v2 --home <HOME> list
python -m crew.storage_v2 --home <HOME> migrate --source <旧HOME> --project <旧工作区名>
python -m crew.storage_v2 --home <HOME> export <workspace UUID> <输出.crewpack>
python -m crew.storage_v2 preview <输入.crewpack>
python -m crew.storage_v2 --home <HOME> import <输入.crewpack> --mode restore_readonly
python -m crew.storage_v2 --home <HOME> context <workspace UUID> --query <关键词>
python -m crew.storage_v2 --home <HOME> handoff <workspace UUID> <新的输出目录>
```

导入、导出前会校验关系及真实文件摘要。包来自不可信来源时，只读取允许的行并按应用自带 DDL 重建，绝不执行包内触发器或 SQL。失败不覆盖源；目标数据库导入前有备份。导入操作有持久回执，重试不会重复复制；需要再次建立独立副本时可显式提供新的操作 ID。
