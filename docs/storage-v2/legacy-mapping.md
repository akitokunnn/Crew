# V1 → V2 迁移映射与无损边界

本文件是设计契约，不代表生产库已经迁移。V2 领域表以 [sqlite.sql](sqlite.sql) 为准，设备表以 [local.sql](local.sql) 为准，云端同步以 [sync-protocol.md](sync-protocol.md) 与 [cloud.sql](cloud.sql) 为准。

## 当前本地实现入口

生产模块是 `plugin/crew/storage_v2/legacy.py`，独立回归在 `plugin/tests/test_storage_legacy.py`。`inspect_source(source_home)` 只读发现；`migrate_workspace(repository, source_home, project, origin_id=None, workspace_id=None, identity_project=None, strict_source_check=False)` 创建或持续刷新同一工作区。显式 origin/workspace 允许外部来源升级复制到当前兼容 HOME 后继续保持身份；目录和显示名不参与重新分配主体 ID。`resolve_entity` / `resolve_goal_id` 通过最新批次的类型化主键摘要索引定位，且验证目标工作区，不扫描原文 blob。

实现有两个明确不同的存储层：完整原 SQLite、JSONL（含未知事件、重复帧、坏尾）、spawn 和原始文件保存在 `storage-v2/migration-vault`，不进入工作区导出闭包；领域库只携带明确投影的事实、可见完整消息/工具结果及已登记产物。portable `legacy_records` 的内容使用逐表字段白名单，源主键只留类型化摘要，未知 JSON、原生绑定、机器路径字段、凭据和执行权限不自动进入可导出 blob。未投影记录列为 `archive_only`，完整源仍在私有镜像中，不能把这一状态描述为全部旧字段都已成为可移植业务字段。私有档案也不能冒充另一个电脑的有效授权。

聊天按原消息/turn ID 聚合增量帧，完整 assistant snapshot 优先；工具调用与结果有独立关系。未完成消息继续增长时创建新消息并记录 `supersedes_message_id`，不修改不可变消息。保留原事件时间；缺时间的来源使用来源会话时间并留下缺失标记。原始帧始终留在私有档案。

刷新比较源与已提交批次的指纹；可变领域记录 CAS 更新，不可变正文/上下文/文档产生新版本。普通打开取得一致的来源快照，若快照后又有写入，回执标记 `changed_after_snapshot`，后续再刷新，不永久忽略新增历史。真正切换时调用方须持有旧引擎写入门，并使用 `strict_source_check=True`；检测变化则回滚，不能仅依赖迁移收据或文件 mtime。兼容层反向物化只能通过本机私有 `legacy_snapshot` 或明确的激活映射进行，导入来的只读包不能自动取得原生绑定。

文件按流式 hash 校验与原子暂存发布。只捕获显式登记文档和聊天附件，不递归扫描项目或 output 目录；JSON、Office、代码等正式登记产物按真实字节携带，不按扩展名静默丢弃。已知凭据文件会产生明确 omission/missing 诊断，不能声称完整导出。升级、刷新和查看均不会启动模型或发送历史通知。

## 旧结构基线与归档规则

基线：`plugin/crew/schema.sql` 在 2026-10-25 的 88 张 `CREATE TABLE IF NOT EXISTS` 表；文件 SHA-256 为 `17300c3929ad2cfd1dce2fdb3d39d0419b72a88eccf6ef886ccc58171434d125`。下面逐表覆盖全部 88 张，不把 SQLite 自带 `sqlite_sequence` 算作领域表。文件中另有 `DROP TABLE session_models`，历史源实际存在它时也必须在打开升级器之前保留。

归宿标记：**D** 是可同步/可携带的领域事实，仍受账号、用户和工作区作用域约束；工作区导出不能携带账号个人设置或云授权。**L** 是设备本地运行/配置；**C** 是可重建缓存；**A** 是原文审计归档。一行可以拆分到多个归宿；归为 L/C 不意味着原始记录可丢弃。

每个源先在本机私有迁移 vault 封存一致的 SQLite 只读快照和其 schema、原始 JSONL/文件字节及枚举清单。V1 `crew.db` 是多工作区全局库：整库快照、账号设置和无法判定归属的原文不得挂到某个工作区 artifact 下，或随该工作区导出。工作区的 `import_batches`、`legacy_records` 只关联已确认作用域的类型化行档案及对应原文 blob；逐旧记录保存源表、类型化主键、原始行 hash、原始内容位置、转换版本、目标类型/UUID 列表和字段处置。普通 JSON 不能无损表示 SQLite BLOB、64 位整数或浮点特殊值：行归档用带 SQLite storage class 的类型封装，整库原快照另外保留。未知字段、未知表、坏 JSON、未知消息类型均进入明确的 `unresolved`/`archive_only` 账本，不能静默跳过，也不能用新字段默认值伪造旧事实。静态加密及授权范围适用于这些敏感原文，不把它与另行版本化的离线包加密或端到端零知识承诺混为一谈。

新表没有精确对应字段时，映射表中的说明是归档/建模要求，不能擅自把数据塞进近似字段就声明完成。转换器必须提供逐字段处置清单；目标 DDL 不支持的可读事实保留为归档附件及原始记录，列入导入报告。只有业务投影及其关联验证通过后才能声明该实体可用。

## 新主干增加的表

以下表与原设计的 88 张表一起纳入发现、快照及逐表回执。当前执行库的实际集合由设计验证从 `schema.sql` 读取，不再用固定数量判断覆盖。

| V1 表 | V2 归宿 | 必须保持或拆分的语义 |
|---|---|---|
| `ai_task_settings` | L；A | 新建 leader 等用途的大脑选择属于当前电脑的配置，不随单个工作区复制；完整源保留在本机私有快照。 |
| `leader_member_settings` | L；A | 原 HOME 恢复保留每组的大脑设置；可移植包不将旧电脑的选择变成执行授权，原字段留在私有档案。 |
| `maintenance_jobs` | L；A | 维护回执按工作区保留；原 HOME 恢复将未结束的维护标为中断，不重新执行压缩或清理。 |
| `maintenance_items` | L；A | 跟随所属维护作业及人员，保留结果与真实错误；未完成项恢复为中断。 |
| `workspace_cleanup_boundaries` | L；A | 原 HOME 恢复时重新映射频道、摘要及交接边界，防止旧记录成为新动态；便携历史保留归档事实，不复制运行边界。 |

## 全部旧表映射

### 身份、工作区、偏好与控制（22 表）

| V1 表 | V2 归宿 | 必须保持或拆分的语义 |
|---|---|---|
| `ai_settings` | D `user_settings`；L `local_secret_refs`；A | 模型偏好可迁移；API 地址/密钥先分类，凭据不随库恢复为可用授权。保留原 `ai_json`，未知 provider 配置归档。 |
| `execution_settings` | D `policies`；L `local_native_bindings`；A | portable 的是执行意图/默认偏好；`danger-full-access`、`bypassPermissions` 不能成为新机器既成授权。 |
| `projects` | D `workspaces`；L `local_workspace_locations`；A | `name` 是旧标识/显示别名，绝对 `root` 是设备位置，均不再作实体主键。原 `created_at` 保留。 |
| `project_themes` | D `user_settings`；A | 按用户+工作区保存主题及 `assigned_at`，不能变成所有协作者共享设置。 |
| `project_settings` | D `workspaces` / `user_settings`；A | `display_name`、`removed_at`、`revision` 分别保留；旧“从侧栏移除”不等同云端永久删除，也不能导入后自动重新展示。 |
| `project_directory_changes` | L `local_workspace_locations`；A | 完整保存旧/新 root、session_paths JSON 与 revision；新机器重映射位置，不把历史 root 当现行可执行目录。 |
| `groups` | D `groups`、`group_memberships`；A | 组 UUID 与 leader agent UUID 分离，保留关闭状态与 note，不以“组名=leader名”作身份约束。旧leader=NULL而V2要求leader时标unresolved，不造一名假leader通过FK。 |
| `sessions` | D `agents`、`conversations`、历史 `executions`；L `local_runtime_sessions`、`local_workspace_locations`；A | role/name/task 与原会话历史可读；cwd/worktree/branch/entrypoint/native permission/terminal/status 是设备或某次执行信息。`last_prompt/message` 截断摘要不能替代完整消息。 |
| `session_runtimes` | L `local_native_bindings`；D `native_archives`；A | native_id 只作某设备/工具/账号下的历史关联，不充当 agent、conversation 或 execution UUID。 |
| `session_runtime_bindings` | L `local_native_bindings`；D `native_archives`；A | 每工具原生 ID、model、effort、updated_at 全保留；新设备默认 `unbound`，不得拿旧 native_id 直接 resume。 |
| `session_options` | D `agents` / `policies`；L `local_native_bindings`；A | 保留模型/推理偏好与 set_at；目标机器工具能力另行验证，不静默换模型。 |
| `session_cards` | D `agents`、`goal_assignments` 的明确范围；A | scope/done_when 是人写下的职责；不能凭当前组关系反推历史 goal 归属。无精确字段时保留结构化归档。 |
| `session_controls` | D `agents` / `conversations` 的归档可见性与别名；A | `archived_status/note/ended_at` 全保留，removed 与归档分开。源移出状态不可因恢复 hook 复活。 |
| `session_stop_gates` | L `local_runtime_sessions` 的停止门；A | 原 stopped_by/time 是审计；所有导入执行默认禁止自动启动，不能重建为“已解除停止”。 |
| `leader_order` | D `user_settings`；A | 以 agent UUID 列表保留该用户的工作区顺序，不按重命名后名称排序。 |
| `leader_priority` | D `user_settings`；A | 保存原 0..2 顺序与作用域；缺失/归档 agent 不压缩后覆盖原顺序，显示可以过滤。 |
| `read_cursors` | L `local_view_cursors`；A | 旧 seq 需经“原日志+序号→message UUID”映射；不能当 DB 全局 message 序号。当前设计视为设备阅读位置，原值随来源档案保留；不假设用户阅读状态漫游已经实现。水位不等同运行消费游标。 |
| `rule_reads` | L 工具/规则缓存；A `legacy_records` | 原 digest/read_at 只证明旧设备读过旧守则；新版本规则变更后必须重新确认，不迁成永久已读授权。 |
| `runtime_switches` | D 原 handoff/context；L `local_native_bindings`；A | 完整保留 preview/result JSON、revision、过期和 attempted/accepted/consumed 消息关联；一次性配置票据失效，不在导入时执行切换。 |
| `trust_settings` | D `policies` 的惰性旧工作区意图；已证实leader投影进入 `trust_settings`；A | V2信任设置为leader作用域，旧workspace设置不能直接套进去。以已有 `leader_trust_settings` 为准，不扩散给迁移后新建leader；只有已存在的明确迁移快照可生成leader级投影，不能重跑seed。 |
| `leader_trust_settings` | D `trust_settings`；L 授权就绪状态；A | 按原 leader agent+workspace 保存目标与 enabled；新设备重新绑定凭据/执行权限前不能因 enabled 自主干活。 |
| `trust_leader_migrations` | A `legacy_records` / `import_batches` | 这是旧转换是否应用的证据，不能在 V2 重放旧 SQL 去给更多 leader 授权。 |

### 消息、频道、通知与人工待办（20 表）

| V1 表 | V2 归宿 | 必须保持或拆分的语义 |
|---|---|---|
| `peer_messages` | D `peer_messages`、`messages`；L `local_outbox` / `local_outbox_attempts`；A | body/发送与接收 agent、时间、原 delivery/error 保留；已有送达不重发，旧未送达作为历史待确认，不自动新投递。 |
| `workspace_comments` | D `comments`；A | 原作者与工作区保持，不用当前登录用户冒充原作者。 |
| `channel_messages` | D `channels`、`channel_posts`；A | 保留 speaker_name 的当时快照，`user` sentinel 映射明确的旧用户 actor，不当作 session_id。 |
| `channel_mentions` | D `channel_mentions`；L 通知关联；A | message_id→channel_post UUID、session_id→agent UUID、notice_id→notice 映射分开，不以当前名字重新解析收件人。 |
| `channel_reads` | L `local_view_cursors`；A | reader=user 或特定 agent 的作用域不同；message_id 是旧 channel_messages PK，不是 chat seq。只可映射用户阅读视图，agent消费水位作为旧执行证据归档，不冒充人类已读。 |
| `channel_digests` | D `channel_digests`；A | 摘要本身、精确 upto post UUID、message_count、manual/auto、runtime/model/cost 全留；导入后不触发自动摘要。 |
| `leader_notices` | D `messages` 的历史系统通知；L `local_outbox`；A | web/qq/lark/crew 来源与 sent_at 保留；恢复不是一次新通知，源 pending 进入历史未处理状态。 |
| `notification_messages` | L `local_outbox` / `local_outbox_attempts`；A | QQ 外部 message_id/ref_idx/owner、kind、正文、状态、error 全归档；不是可跨账号重用的授权或未发任务。 |
| `notification_inbound` | A `legacy_records`；已证实的来源关联进入D | QQ event_id去重历史不变成业务主键，不携带旧连接活跃消费位点；不能复用存放云端commit的 `local_sync_inbox`。新provider收件需要独立去重语义。 |
| `notification_replies` | D `messages` / 已证实的 `decision_answers`；L 收件回执；A | 必须保留 reply message_id、text、status、after_seq；只有已验证的关联可生成答复，after_seq按旧日志解释。 |
| `lark_notification_messages` | L `local_outbox` / `local_outbox_attempts`；A | 同上，但 provider=lark，不能与 QQ 的相同外部 ID 合并。 |
| `lark_notification_inbound` | A `legacy_records`；已证实的来源关联进入D | 连同provider/account namespace保留；导入不能回放webhook，也不把provider event_id当作cloud commit ID。 |
| `lark_notification_replies` | D `messages` / 已证实的 `decision_answers`；L 收件回执；A | Lark 与 QQ 原文、外部 ID、交付状态分别保留；缺映射不猜题目。 |
| `attention_resolutions` | D `user_settings` 的已读/已处理事实；A | item_id 编码的 report/decision/todo 标识按类型映射，不能简单复制整数或任意字符串替换。 |
| `todos` | D `todos`；A | title/steps/verification/blocking/status/note/resolved_by/各时间/version 全保留；完成/取消状态不得重开。 |
| `todo_backfills` | L 本地迁移/投递回执；A | 是一次性 backfill 已安排的证据，notice_id按通知映射；不能在新机补造或重发旧待办。 |
| `decisions` | D `decisions`、`decision_options`、`decision_answers`；A | 原 question/options/recommendation/answer/answered_by/时间完整保留；options坏JSON或未知扩展字段原样归档，不能改成空数组。 |
| `decision_briefs` | D `decisions` / `decision_options`；A | title/context/why_user/recommendation_reason/impact/options_json 全保留，重复选项用稳定索引UUID保持顺序。 |
| `decision_holds` | D `decisions` 的挂起状态；A | 原 held_at 保留；挂起与已答/已处理是独立维度，不能丢掉催办暂停语义。 |
| `decision_reviews` | L `local_outbox` / 本地待处理审查；A | 保留 queued_at和notice关联；历史排队不能自动成为新执行命令。 |

### 文档、资源、Git、交接、报告与用量（25 表）

| V1 表 | V2 归宿 | 必须保持或拆分的语义 |
|---|---|---|
| `documents` | D `artifacts/artifact_versions/artifact_links`；L 文件位置映射；A | 保留原提交者、leader、group、path/path_key、title/summary/kind、AI与用户重要性、source/source_seq及时间。用户pin与AI建议分开；缺原始文件不伪造version内容；历史登记时间不等于本次导出的文件字节生成时间。 |
| `document_scans` | C 本地索引游标；A | source/offset/size/mtime_ns/inode/error/scanned_at全归档；reports offset与journal byte offset分类，重建扫描不沿用旧设备inode或旧序号。 |
| `resources` | D `policies` 的资源定义；L `local_resources`；A | project+name作用域、kind/description/guard_regex全保留；device/dir/app路径需要本机绑定，不能在新机占用同名资源。 |
| `leases` | L `local_resource_leases`；A | purpose/overwrites/taken/until/released/by/note全部保留；旧开放租约导入为失效历史，不等于新设备执行租约或云端run fence。 |
| `claims` | L `local_file_claims`；A | 原pattern与note和释放时间保留，重新映射路径后重新取得占用，不携带活跃文件锁。 |
| `git_management` | D `policies`；L Git仓库/工具绑定；A | base/stable/branch_rules及管理员agent保持；导入不启用git管理员、不提交或push，须确认新checkout。 |
| `git_merge_requests` | D `git_requests`；A | branch/description/status/pr_number/note/时间保持；provider remote identity需独立校验，merging状态不能自动续merge。 |
| `git_ci_state` | C 本地远端状态缓存；D `git_results` 的历史观察；A | subject/run_key/sha/status/notified作为当时观察保留；新机重新取远端状态，不能把历史notified当发送授权。 |
| `git_result_handoffs` | D `git_results`、`handoffs` 的关联；A | handoff、原提交agent、git管理员分别映射，不能仅以当前管理员覆盖原接收者。 |
| `handoffs` | D `handoffs`；A | from_session→agent、body原文和to_target原路由保持；组名/通配符只记录原意，不按新成员集合重新广播。 |
| `handoff_deliveries` | D `handoff_deliveries`；L 投递回执；A | 精确(handoff,receiving agent)一次性事实保持；未知收件人保留未解析，不能标成所有当前成员已收到。 |
| `reports` | D `reports`；A | 原body与作者、时间不改写；报告不是成功验收或已经执行的证据。 |
| `events` | A `legacy_records`+原快照；可映射的事实并入D | 原kind/ts/project/session/detail完整保留；按kind解释detail中的引用，原审计事件不重新执行，也不因字段未知删掉。 |
| `usage_files` | C 本地扫描游标；A | path/size/mtime/offset/scanned_at是旧设备扫描状态；新机重建，不直接套在不同字节的日志上。 |
| `claude_sessions` | D `native_archives` / 历史conversation来源；L `local_native_bindings`；A | 原project_dir/cwd/entrypoint/version/title/first-last保留，孤立原生会话不自动收编成agent。 |
| `claude_requests` | D `usage_observations`；A | request_key、agent_id/type、ts/model/effort、全部token/cache/thinking/web/service_tier/speed完整保留；去重须含provider+来源域，不能按时间近似合并。 |
| `claude_prompts` | D `usage_observations` / 消息来源；A | uuid/session/kind/chars/ts保持；chars计数不能还原prompt原文，原文须从实际归档提取。 |
| `claude_tool_calls` | D `tool_calls` / `usage_observations`；A | 这里仅有名称/时间/ID，不可伪造arguments或result；确切内容从原日志关联，缺失标明。 |
| `claude_compactions` | D `context_manifests` / 历史观察；A | 只记录压缩发生的uuid/ts时，不捏造摘要文本或“原生上下文可恢复”。 |
| `codex_sessions` | D `native_archives`、`usage_observations`；L `local_native_bindings`；A | rollout_id/path和source/originator/CLI/model/effort/sandbox/全部token/first_user_text保持；路径本地，摘要不是完整对话。 |
| `codex_runs` | D 历史 `executions` / `usage_observations`；L 目录绑定；A | run_id、dispatching原生会话、task_file/workdir、sandbox/model/effort/start/end/exit/rollout关联完整；并非所有run都有Crew agent，未解析者不假造归属。 |
| `codex_rate_limits` | C 本地额度缓存；D `usage_observations` 的历史观察；A | rollout+ts及plan/used/window/reset/credits保持；历史额度不能作为新机器/新账号实时额度。 |
| `trust_decisions` | D `decision_answers` 的自主决定来源；A | 使用agent作者及原授权policy/snapshot，保留reason与recorded_at，不伪装成人类亲自答复；无法证明授权者时保留未知，不以当前用户补造。信任记录不是新设备通用授权。 |
| `decision_leader_scopes` | D `decisions.owner_leader_agent_id`；A | NULL是未知原归属，永久保持未知；不能按迁移时或当前group推算。 |
| `trust_schema_migrations` | A `legacy_records` / `import_batches` | 保留旧迁移标记及applied_at；不作为V2 schema版本，不执行其旧信任迁移副作用。 |

### 目标、执行、上下文与验收（21 表）

| V1 表 | V2 归宿 | 必须保持或拆分的语义 |
|---|---|---|
| `goals` | D `goals`、`goal_dependencies`；A | 原request/status/requirement/current_round/priority/dependencies/manual_pause/reason/version/时间全部映射；依赖显式边不能只塞入摘要。历史终态不改，活跃态导入为暂停。 |
| `goal_requirements` | D `goal_requirements`；A | version/request/acceptance/constraints/change_text/actor/source/时间保持；列表顺序和未知JSON字段保留原文。 |
| `goal_rounds` | D `goal_rounds`；A | (goal,number)复合身份保持，requirement版本与status/outcome/reason/时间保持；同为第1轮不意味着同一个实体。 |
| `goal_assignments` | D `goal_assignments`；A | goal+agent及task/status/时间保持，released不自动变assigned。 |
| `goal_executions` | D `executions`；L `local_runtime_sessions`；A | goal/round/agent/generation/runtime/model/effort/requirement/状态/错误/时间保持；新设备新执行另建execution并记录接续关系，不冒用旧执行。 |
| `goal_execution_bindings` | L `local_native_bindings`；D `native_archives`；A | execution→原生会话关联的历史可携带，活绑定不可跨设备复制。 |
| `goal_reports` | D `reports`；A | 原data_json与HTML都封存，outcome/version/requirement/round精确保持；HTML导入只读展示，不执行内联脚本。 |
| `goal_context_items` | D `context_items`；A | workspace/goal/leader的原scope组合、kind/version/state/protected/时间保持；NULL project不靠路径猜。 |
| `goal_context_versions` | D `context_versions`、`context_version_sources`；A | text、source、depends、state、actor、reason及版本保持；unknown dependency不能消失或改为“已满足”。 |
| `goal_context_manifests` | D `context_manifests`、`context_manifest_entries`；A | 原manifest_json和实际注入text均保留；绑定原execution/round/requirement/generation，不能只保存生成模板。 |
| `goal_context_applied` | D `context_manifest_entries`、已证实的 `context_applications`；L runtime消费状态；A | material_key/version/manifest/applied_at保持；旧逐material记录不能凭空变成“整份manifest完整注入”的应用或编造character_count。只有原日志能证明时生成application，其余归档；“旧execution已注入”不等于新execution已应用。 |
| `goal_execution_reads` | L 原执行消费游标；A | read_seq原值归档，新执行从新消息账本建立水位，不能跳过换机后尚未注入的信息。 |
| `goal_message_meta` | D `messages` / `peer_messages` 的任务关联；L `local_outbox` / attempts；A | goal/round/requirement/source-target execution/kind/correlation/causation/idempotency/reply/executable/status/attempts/时间/error全保持；旧可执行标记不重新入队。 |
| `goal_control_revisions` | D `goals` 的控制revision；A | 原revision进入并发/来源账本，不回退或与内容版本混为一谈。 |
| `goal_ops_pauses` | D `goals` 的暂停原因；L 原运行恢复票据；A | incident/token JSON/创建/恢复时间完整保存；旧token不在新设备消费。 |
| `goal_round_finishes` | D `reports` / `goal_rounds` 的结束记录；A | 精确保留report、outcome、pending/finalized/superseded、actor、execution、requested/finalized；报告存在不自动判定finalized。 |
| `goal_resumption_deliveries` | L `local_outbox_attempts`；D 原恢复消息来源；A | incident/execution/message/accepted_at保持；新机器不得把旧accepted当作本次命令已完成或需重发。 |
| `goal_links` | D 明确类型的目标关联；A | kind决定item_id所指decision/todo/handoff；goal/round/requirement/execution/delivered_version全部保留，不能用无类型多态整数FK。 |
| `goal_cancellations` | D `goals` 的取消事实；A | 原round/requested_at/actor保持；取消优先于旧运行状态，恢复包导入不能解除。 |
| `goal_documents` | D `artifact_links`；A | 明确的goal-document-execution关系精确迁移；旧库没有此表时保持缺省“未登记”，不能根据leader或文件名批量归属。 |
| `goal_acceptances` | D `goal_acceptances`；A | goal/round/report/accepted/note/actor/时间保持；completed报告不等于人已验收，未记录验收不能补“通过”。 |

上四组实际行数按旧表名集合验证，章节数量标签仅便于阅读，不替代机器对照。额外数据库对象处理：`sqlite_sequence` 归档且不复制为新UUID分配器；未知历史表/触发器/视图/index完整留在源快照并列入账本；已存在的 `session_models` 先归档其所有字段，再尝试同 `session_options` 的精确迁移，禁止让现有 `DROP TABLE` 先执行。

## 数据库之外的全部文件

| V1 来源 | V2 归宿 | 导出/恢复规则 |
|---|---|---|
| `HOME/crew.db`、WAL中的已提交事务 | D 可移植领域投影；A 本机私有原库snapshot及按scope切片的行档案 | 用SQLite backup API获取一致源，不能只复制主db漏WAL；保存`sqlite_master`/user_version/应用版本及快照hash，原库不执行升级SQL。整库快照不进单工作区包，不能只靠UI隐藏其他工作区。 |
| `HOME/chat/<session_id>.jsonl` 与 `<execution_id>.jsonl` | D `conversations/messages/message_parts/tool_calls/tool_results`；A 原始文件blob | 按UTF-8原字节保留BOM/换行/重复/截断尾。逐行账本保存offset/length/hash/seq；未知行类型、tool中间事件、partial输出均不得抛弃。重复native事件与Crew journal可建立来源关系，不可凭文字相同去重。 |
| `HOME/chat/files/<id>/**` 图片、附件 | D `blob_objects`、`artifacts/artifact_versions`、`message_parts` | manifest列出每个附件，校验字节数与hash，引用必须落到verified blob；无法读取/缺失附件使“完整”校验失败，不能给空图占位后声称成功。 |
| `HOME/spawn/<id>.json/.md` | D 任务书artifact版本及来源；L 设备启动配置；A 全部原字节 | 原task/name/group/cwd/model/runtime/agent/md/created_at都保留；结构化绝对路径单独映射，原文不全局字符串替换。恢复不会执行task brief。 |
| `HOME/codex_runs.jsonl`、`claude_runs.jsonl` | D 历史execution/usage；A 原文blob | 不仅复制DB统计；已封存log与可规范化记录均须进入manifest，秘密识别与加密规则同原对话。 |
| `HOME/qq.json`、`lark.json`、`notify.json`、`lark-notify.json` | L `local_secret_refs`、设备连接设置；D 非秘密偏好；A 脱敏来源报告 | token/app_secret/owner binding/binding_code不进入常规完整包。按字段列出排除与需重认证项，不把“完整”宣称为携带身份凭据；需要灾备秘密时须另行明示的密钥保险库流程。原明文秘密不偷偷留入普通raw archive。 |
| `HOME/runtime.json`、bin入口、plugin安装路径、desktop backend设置、autostart、service/runtime locks、PID/端口文件 | L `local_devices/local_runtime_sessions`；C 可重建；A 非秘密配置快照 | 不作为新机可运行配置恢复；OS路径、端口、PID、锁文件不拷贝为活动状态。插件版本仅作兼容性输入。 |
| `HOME/prices.json`、`blacklist.txt`、CLI更新偏好 | D 可移植偏好/带版本计价配置；L 机器特有更新策略；A | 保留价格来源/版本与黑名单原文，不能将旧价当现价或启用旧设备自动更新。 |
| `HOME/claude-limits.json`及额度/扫描缓存 | C 设备缓存；D 历史usage观察；A 非秘密历史 | 新机/新账号重新查询额度；保留原始观察时间，不展示为当前剩余额度。 |
| `HOME/desktop/ui` 的Local/Session Storage与草稿/图片 | L `local_drafts`；如用户选择迁草稿则导出显式draft artifact | 常规领域库不含Electron草稿；必须列明可选纳入/排除。带图片草稿需一并捕获，不能仅复制浏览器原点绑定的LevelDB冒充跨版本可恢复格式。 |
| `~/.claude/projects/**`、原生subagent文件及 `~/.codex/sessions/**`、archived histories | D `native_archives`→加密blob；L `local_native_bindings` | 导出所选工作区可证明关联的原生历史，连同工具/格式/模型/来源版本与关联清单。默认只读历史；可兼容resume必须另经本机工具验证。原生登录文件、OS密钥链、刷新token不携带。不能为了“完整”打包其他工作区全部原生历史。 |
| VS Code `state.vscdb`/hiddenSessionIds、原生进程索引 | L 导入来源和设备可见性；A 最小必要的来源记录 | 不打包整个编辑器profile/其他项目状态；Crew自己的归档事实映射后保留。 |
| 项目工作目录、git worktree下的文档/截图/CSV/HTML/output/data等交付物 | D `artifacts/artifact_versions/artifact_links`＋导出snapshot | 以逻辑root+规范化相对路径纳入真正字节快照，不能只保存documents.path。导出策略逐项列入/排除，Git忽略不代表产物可丢。敏感.env/私钥/凭据使用明示排除策略。 |
| Git已跟踪源码、未提交/未跟踪代码、所需commit对象与本地分支 | D 项目snapshot artifact与版本来源 | 完整执行接续包需捕获所需commit对象（bundle或已验证snapshot）、dirty diff、未跟踪必要文件与依赖锁；仅记SHA在新机可能无法获取。不同worktree分开记录，不能只copy `.git` 指针。 |
| 旧 `HOME/backups/**` 与旧版本HOME | A 按选择登记为历史恢复源 | 不递归把所有backup嵌套到每次包；登记已有快照及来源hash，选择纳入则去重。旧安装与旧库保留直到切换验证与保留期满足，不能自动删除。 |

文件级安全限制：不跟随越出声明逻辑root的symlink/junction；路径需拒绝绝对路径注入、`..`、Windows设备名和ADS；大小写/Unicode归一化冲突必须预检报告。相同blob内容可去重，但不同原始文件名、来源和版本关系不能去重掉。完整包校验必须包括所有被引用blob和原始档案，不能仅对DB做`integrity_check`。

## 确定性身份与不可猜测的旧关联

新agent/conversation/execution/workspace各自独立UUID。建议映射键为 `UUIDv5(import_namespace, legacy_origin_id || target_entity_type || source_table || typed_canonical_PK)`；复合PK必须带类型、字段名和长度编码，不能用有歧义的字符串拼接。旧已有UUID也记录映射，不直接等同新Crew主体ID。`legacy_origin_id` 标识来源谱系，不使用路径、显示名、native_id或某次snapshot hash作为谱系身份；snapshot追加后hash变化必须沿同一origin建立新批次，而不是再造一套实体。

V1没有origin字段：首次登记在目标账本/导出manifest分配origin，并记录封存基线与后续指纹，不改源库。不能从内容相似或路径相同“证明”同源；来源搬移/复制后无法确认谱系的导入进入待选择状态，由用户选择接续既有origin或明确fork。fork分配新workspace与映射namespace，并保留forked-from来源，不共享活跃执行或运行租约。

必须单独处理的引用：

- `documents.source_seq` 的report PK与journal seq是两种来源；`document_scans`的reports offset是report PK，journal offset是byte；`read_cursors`/`goal_execution_reads`又是不同journal水位。
- 所有复合FK整组匹配，例如goal+round；不能用“round=1出现在已选集合”吸入其他工作区。
- `events.detail` 按kind转换decision/report/document/notice/handoff/lease/git ID；delivery.answers/handoffs数组及notification item_id前缀按类型映射。raw JSON与原文同时保留，禁止递归替换所有名为id的整数。
- `goal_links.kind`、channel speaker/reader的user sentinel、group与`*`路由、provider外部ID均须显式解释。无法解析的旧关系不能用当前成员/当前登录用户补造。
- project=NULL、unknown leader=NULL、未关联原生session保持未解析范围，进入账号级来源档案与unresolved账本，不能为了新FK伪造其工作区/作者。
- 原日志若确实包含多工作区或工作区转换前的未归属历史，不能只依据session当前project就把整段原日志塞进该工作区包。按已证实来源切片，保留原byte-range/hash与整份原文件的私有vault指针；不确定部分留在私有未解析档案，导入回执明确提示。单工作区export只携带其可证明归属的原文。

## 可崩溃恢复的迁移与单入口切换

1. **发现与冻结来源。** 读源schema/version及origin候选，获取能约束旧CLI/后台的排他写入栅栏；无法阻止旧写入则仅允许preview/stage。封存一致SQLite snapshot、原始消息/附件清单、native和项目快照。记录DB逻辑high-water/提交状态以及每个文件hash/size/尾部边界；SQLite文件mtime不能单独证明源未变化。
2. **明确格式门槛。** 新领域/本地/云schema各有显式format/schema version及min_reader/min_writer。旧写入者看到cutover barrier必须拒绝写；不能先用现有store.connect跑旧schema迁移再取快照。未知较新格式拒绝降级写入。
3. **Stage。** 只写新staging库与暂存blob，以batch阶段、manifest hash和幂等entity mapping记录进度。上传blob未校验之前不发布引用它的领域commit。provider命令、通知outbox、自动摘要/重试/恢复均保持禁用。
4. **校验。** 源88表及额外表逐行/字段账本计数；raw消息字节、全部行类型和附件可回溯；全部blob digest/size、FK与跨workspace作用域、typed mapping唯一性、snapshot路径冲突及原生/产物覆盖完整。`archive_only/unresolved/missing/excluded`须在回执中分开列出，缺少必需blob时不能宣告完整。
5. **再次检查源。** Cutover前重新确认源high-water和文件清单未变。变化则使本批次不可提交，按同origin重取snapshot/增量重建并重新校验；已有旧迁移收据不使未来新增数据永久被忽略。旧写入者未栅栏时不能进入下一步。
6. **单入口切换。** 先持久写校验通过的commit与恢复记录，再原子切换唯一catalog pointer；新入口启动必须能区分`staged/verified/cutover/complete`，崩溃后继续或回退到旧指针，不半旧半新运行。跨文件系统rename不能假称原子；在同一稳定home内落指针/事务日志。
7. **验证与保留。** 新库只读展示旧历史，所有旧活跃执行暂停、旧投递不补发、原生绑定unbound。用户显式继续才建立新设备execution/runner fence。旧源保留，删除需独立保留策略；迁移成功不等于可清空来源。

## 独立架构评审与关键反例

这些是设计的验收条件，不把未来功能误报成已经存在：

| 反例 | 必须得到的结果 |
|---|---|
| 新机恢复“已完成”的目标，但原生CLI未安装、未登录、工作区未映射 | 历史和附件能读；运行不可用原因明确。安装、重新登录、校验能力/路径后，以新execution接续；不是复制native_id后宣称原进程续跑。 |
| 源电脑还在线，目标电脑要求接续同一任务 | 云run lease/fencing token拒绝双写；旧runner重连不得提交过期fence输出。完全离线无法取得全局独占，不自动执行会外发/修改的任务。 |
| D盘变成macOS路径，两个worktree有未提交改动，文档绝对链接指向旧根 | 显式逻辑root映射、snapshot恢复与冲突核对；原文链接可读历史且提供映射解析，不全局替换消息正文，不覆盖新机已有文件。 |
| 克隆Git仓库或复制含manifest的目录 | 识别实例冲突，用户选独立fork或接续；repo remote/同UUID不足以自动合并历史和执行。 |
| DB导出成功，但一张图片、CSV产物或Git所需commit未上传 | 包不标complete。partial上传可续传，重试去重；manifest最终commit发布前blob全部verified。 |
| 上传后进程崩溃，产生孤儿blob；删除DB引用后GC与旧导出包并存 | staged blob有expiry；verified blob依真实引用/导出快照/retention hold而非上传时间GC，未完成上传不可被当成已引用完整对象。 |
| 声称端到端加密，又要求服务端按所有对话正文任意SQL检索 | 明确选择客户端解密检索，或另行选择可被服务端解密的托管模式；列出明文索引元数据泄露范围，不能同时承诺零知识和正文服务器查询。 |
| 设备离线删除记录，另一设备上传旧状态，或导入删除前备份 | tombstone与deletion epoch优先，显式实体重建必须新UUID；已知workspace先对齐删除账本，不能用旧snapshot覆盖。 |
| 全新离线电脑只有古老备份，云端实际上早已删除部分记录 | 无法凭旧包知道后来删除：只读隔离/待同步；联网对齐删除账本或用户显式fork后才可成为现行库。协议必须承认这个信息边界。 |
| 在DB切换、blob移动、迁移提交任一步突然断电；旧源随后又追加一条消息 | 重启回到单一明确版本；同origin幂等重试，无重复实体/通知；追加被变更检测发现，不能因为已有receipt跳过。 |
| 原始JSONL有未知type、重复tool delta、无换行尾、坏JSON、超大行 | 原始字节全部归档，解析投影与未解析行分开计数；不得删行后声称“完整历史”。 |
| 不同workspace都叫“股市预警”、两组都叫“架构师PM”、两目标都是第1轮 | 独立UUID和workspace复合约束保持隔离；不以名字、轮号、provider native_id串联。 |
| 单工作区包带有legacy raw archive，而原V1库包含其他项目和账号设置 | 包中只出现本工作区已确认的typed行档案及原文切片；整库SQLite、未归属native日志和私密配置仍在私有vault，不得因artifact闭包一起导出。 |
