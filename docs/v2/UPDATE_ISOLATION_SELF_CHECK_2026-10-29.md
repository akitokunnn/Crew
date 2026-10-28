# V2 更新与 V1 并存检查

检查对象为 `rebuild/v2` 工作树的未提交源码。桌面源码版本为 `0.3.0`；这不是发布或部署声明。

## 已修复的实际问题

- 原线上查询访问仓库最新 Release 和 `main`，会把 V1 当作 V2 更新来源。现在只访问 `rebuild/v2`，固定到读取到的提交，再核对桌面 `appId=local.crew.desktop.v2`；未满足条件时保留错误，不回退 V1。
- 原桌面同步会执行共享的 `claude plugin update crew@crew --scope user`。V2 托管 CC 会话已经通过自己的 `--plugin-dir` 加载插件；桌面事务现只同步 V2 HOME 的后台和命令入口。旧 `plugin-update` helper 命令明确拒绝，不运行 Claude。
- 更新工具读取后端根目录 `crew-product.json` 的 `generation: 2`。缺少标记作为 V1 识别，不能切换到该来源。
- 检查及切换前拒绝用户的默认 V1 `.crew` 目录，也拒绝已有 `runtime.json` 指向 V1 的自定义 HOME。这一检查发生在停止后台之前。桌面 helper 显式传递所选 HOME 参数及环境变量。

## 横向检查

- CC 总对话、Codex 总对话和目标执行均显式传递所选 `CREW_HOME`；生成的 HOME 命令入口也显式绑定它。未修改任何正式 HOME。
- 桌面构建资源来源为当前树 `plugin/`，新产品标记会随资源打包。产品 AppId、程序名及产物名与 V1 分离，由主控维护。
- 默认 HOME 改为 `.crew-v2`、桌面启动及启动设置的后端/HOME 代际校验由其他协作任务维护；它们需要由主控统一复验。
- 在线更新入口文字和打开链接由主控维护；V2 尚未配置独立 Release 频道。

## 实际验证

- `node --test desktop/test/crew-latest.test.cjs desktop/test/crew-updates.test.cjs desktop/test/update-shutdown.test.cjs`：26 项通过，0.94 秒。使用假网络、假后台、临时 helper 进程，覆盖拒绝 V1 来源与 HOME、无共享插件更新、事务关闭和读取取消。
- 清除进程继承的 `CREW_SESSION_ID`、`CLAUDE_CODE_SESSION_ID` 后，在 `plugin/` 执行 `python -m unittest test_crew_update -v`：22 项通过，8.77 秒。覆盖临时 HOME、真实 SQLite 备份、运行门禁、来源校验、显式拒绝共享插件更新及 V2 切换。
- `git diff --check` 通过。

未联网验证远端最新内容，未启动真实 AI，未更新/停止正式 Crew，未安装或更新任何共享插件。构建产物与完整桌面验收交主控完成。
