# Storage V2 领域数据字典

这是设计稿及可执行 SQLite DDL，不是现有正式库的自动升级脚本。`sqlite.sql` 定义 55 张领域表；先执行它，再执行 `local.sql`。云端 PostgreSQL 使用同一领域结构，服务端协调表另见 `cloud.sql`。关系业务表保存当前状态和明确需要保留的内容版本，不从事件日志重放整个产品状态。

## 身份、公共列和作用域

| 约定 | 含义及约束 |
|---|---|
| `id` | 规范小写 UUID 文本主键。DDL 检查长度、连字符及十六进制字符；创建者生成 UUID，不能拿旧整数、会话原生 ID、名称或路径充当新身份。 |
| `workspace_id` | 50 张工作区表均显式携带此列；根 `workspaces` 自身用 `id`。每张工作区表都有 `UNIQUE(workspace_id,id)`。 |
| 工作区关联 | 使用 `(workspace_id,关联列)` 复合 FK；需要更细归属时加 conversation、goal、artifact 等父 ID。例如答复选项必须属于同一题目快照，报告验收必须引用同一轮报告。 |
| 可变记录 M | 共 29 表：`revision>=1`、`created_at`、`updated_at`、可空 `deleted_at`。触发器禁止改变身份、工作区和创建时间，要求新 revision 大于旧值。工作区的 account 归属也不能普通 UPDATE。 |
| 不可变记录 I | 共 26 表：`revision=1`、`created_at`；**不设** `updated_at/deleted_at`。UPDATE 触发器实际拒绝修改。修正写新 UUID、显式 supersedes 或新版本；删除由 tombstone 遮罩及受控 purge 完成。 |
| 时间 | UTC ISO-8601 文本并以 `Z` 结尾。SQL 只检查基本外形，Repository 严格解析。设备时间用于显示、不能作为消息因果关系、同步水位或唯一顺序。 |
| 整数 | version、round、part index、token 数和 accepted sequence 是计数或顺序，不是跨设备实体身份。未知用量保留 NULL，不填零。 |
| JSON | 用于一个记录内的结构化文档、测量和原始来源注释。所有可执行、可导航的业务关系仍用真实 FK，不能把附件仅藏在 JSON ID/绝对路径里。 |

四张全局表是 `users/accounts/account_memberships/user_settings`。导出一个工作区时，仅沿实际 FK 向上收集必要的公开署名用户、所属账号及被引用的 membership；不能因为同属一个账号就把其它工作区、成员或 `user_settings` 全捞走。

`users` 只存公开历史署名（display_name、locale）。OAuth subject、登录会话、令牌、连接凭据不在这里。包内的账号和 membership 快照用于保留原始署名，**不能**通过导入创建云账号、授予他人权限或冒充当前登录者。云端绑定与当前账号校验、跨账号 fork 是独立流程。

## 账号、工作区和协作身份

| 表 | 类别 | 主要字段与关系 |
|---|---|---|
| `users` | M/global | 公开 `display_name/locale`，不承载认证身份。 |
| `accounts` | M/global | `kind=personal/team`、name；租户身份与 workspace 名称分离。 |
| `account_memberships` | M/global | account_id、user_id、role；`UNIQUE(account_id,user_id)`。角色为 owner/admin/member/viewer。 |
| `user_settings` | M/global | user_id、setting_key、value_json；仅允许列举过的非秘密个人偏好，默认不进工作区包。 |
| `workspaces` | M/root | account_id、name、description、status；固定 UUID，不保存本机 root 路径。`UNIQUE(account_id,id)` 支持账号边界 FK。 |
| `workspace_memberships` | M | account_id、user_id、role；FK 同时要求 workspace 和 account membership 属于该账号；一用户一工作区一条逻辑 membership。 |
| `agents` | M | name、kind、purpose、status。kind 是 assistant/git_administrator/system_administrator；这是业务身份，不是 Claude/Codex 进程。 |
| `groups` | M | name、leader_agent_id、description、status；leader FK 可延迟验证。 |
| `group_memberships` | M | group_id、agent_id、role=member/reviewer；组长由 groups.leader_agent_id 表达。更换执行工具不会更换 agent 或 group 身份。 |

名称可以相同，接口按 UUID 寻址。Repository 保证有效账号和工作区至少有一个 owner；DDL 只能保证 membership 指向有效父记录，不能靠单行 CHECK 保证集合中永远有 owner。

## 用户可读会话和工具记录

| 表 | 类别 | 主要字段与关系 |
|---|---|---|
| `conversations` | M | kind、title、可选 owner_agent_id/group_id/goal_id；会话独立于本机原生 thread。 |
| `messages` | I | conversation_id、reply_to_id、kind、author_kind、user/agent 作者 FK、author_name_snapshot、可选 supersedes_message_id。父回复与被替代消息必须属于同一 conversation。 |
| `message_positions` | I | conversation_id、message_id、accepted_sequence；云服务接受后事务分配，一会话内 sequence 唯一、一消息一个位置。只有服务端可生成，客户端不能提交伪造水位。 |
| `message_parts` | I | message_id、part_index、kind 与一种实际载荷。文字/Markdown/notice 使用 text_content；json 使用 json_content；图片/文件选择 blob 或 artifact version；工具部分使用 tool FK。CHECK 限制合法组合。 |
| `tool_calls` | I | conversation_id、message_id、tool_name、display_label、arguments_json/可选 arguments_blob_id。是可读调用事实，不能包含可恢复执行令牌。 |
| `tool_results` | I | 同会话 tool_call_id 与结果消息、result_index、outcome、result_text/blob/artifact version、is_truncated。结果内容至少存在一种载荷。 |

离线未接受的消息以 `reply_to_id` 因果边及 UUID 决定稳定的分支展示；接受后使用 `message_positions`，不按各设备 created_at 猜顺序。删除或修订消息不会重新利用原 UUID。历史原文里出现过的路径是引用文本，不是当前读写授权；实际文件读取必须通过 artifact/blob 和本机绑定。

## 目标、执行、报告和验收

| 表 | 类别 | 主要字段与关系 |
|---|---|---|
| `goals` | M | owner_agent_id、title、status、current_requirement_id/current_round_id、priority、manual_pause、reason。当前指针被复合 FK 限定在本 goal。 |
| `goal_dependencies` | M | goal_id、requires_goal_id、required_outcome；不允许自依赖。依赖图无环由 Repository 检查。 |
| `goal_requirements` | I | goal_id、version_no、request_markdown、acceptance_json、constraints_json、change_summary、作者。保留用户要求的每次确认版本。 |
| `goal_rounds` | M | goal_id、round_no、requirement_id、status/outcome/reason、opened_at/ended_at；轮次需求必须属于同一 goal。 |
| `goal_assignments` | M | goal_round_id、agent_id、role、scope_markdown、done_when_markdown、status；每轮同一 agent 一个逻辑指派。 |
| `executions` | M | agent_id、conversation_id、可选 goal_round_id/assignment_id、attempt_no、supersedes_execution_id、请求模型/effort、逻辑 status、开始结束与错误摘要。assignment 如存在，必须匹配同轮同 agent。 |
| `reports` | I | author_agent_id、可选 goal_round_id/execution_id、kind、title、summary、outcome、body_markdown、data_json、可选 HTML artifact version。统一阶段汇报和目标最终报告。 |
| `goal_acceptances` | I | goal_round_id、report_id、accepted、note_markdown、author_user_id、可选 supersedes_acceptance_id。保留用户验收事实，原 AI 报告不被覆写。 |

需求 JSON 是带版本契约的内嵌文档：acceptance 至少含 criterion key、title、required；constraints 是原文约束列表。key 如 `a1` 只是该需求文档内的标签，不是独立同步实体身份。

report 的 data_json 保存验收结果、证据说明、examples、取舍、未验证项和投入观察；关联文件仍必须有 artifact_links。Repository 检查其 document_schema_version 和字段结构；状态机、完成证据、当轮有效性和用户权限不能只靠 SQL 枚举判断。

换电脑恢复这些记录，不会自动续跑 execution。启动任务还需要当前登录权限、有效设备执行租约及原生绑定；历史 running/queued 状态不是新电脑的运行许可。

## 文件、内容寻址和原生归档

| 表 | 类别 | 主要字段与关系 |
|---|---|---|
| `blob_objects` | M | workspace_id、sha256、size_bytes、mime_type、state=declared/verified/quarantined、verified_at。`UNIQUE(workspace_id,sha256)`；digest 与 size 的修改被触发器拒绝。 |
| `artifacts` | M | name、kind、description、current_version_id、pinned；描述逻辑交付物而不是某台机器的文件路径。 |
| `artifact_versions` | I | artifact_id、version_no、blob_id、display_filename、mime_type、summary、previous_version_id、作者；current/previous 指针都必须属于本 artifact。 |
| `artifact_links` | I | artifact_version_id、relation 与八种显式目标 FK：goal、round、execution、message、report、context version、decision、todo；CHECK 要求恰好一个目标。 |
| `native_archives` | I | 可选 agent/conversation/execution、artifact_version_id、archive_format、format_version、capabilities_json；resume_guarantee 固定为 none。 |

物理 blob key 是 `workspace UUID/sha256`，首版不跨工作区去重，避免内容猜测、授权侧信道和引用计数跨租户耦合。DDL 只验证摘要外形和元数据，云端必须实际流式核对字节长度与 SHA-256 后，才能把状态设为 verified 并接受新的引用。外键存在不等于文件已经上传。

本机 SQLite 可以暂存 declared 的离线附件意图；完整导出和云端已提交关系必须满足其引用 blob 全部 verified、字节可取得。manifest 标明是否包含可选 native archive，以及每个 archive 的能力（例如 readable_transcript）；不能把某个原生 CLI 恰好可 resume 当成 Crew 跨机恢复保证。

## 上下文版本和实际应用

| 表 | 类别 | 主要字段与关系 |
|---|---|---|
| `context_items` | M | scope=workspace/agent/goal、owner_agent_id/goal_id、kind、title、current_version_id、protected、state；CHECK 约束 scope 的合法归属。 |
| `context_versions` | I | context_item_id、version_no、text_content 或 blob_id、change_reason、作者；一种实际载荷。 |
| `context_version_sources` | I | context_version_id、relation=source/depends_on/corrects，与 message/report/artifact version/source context version 中恰好一种真实 FK。 |
| `context_manifests` | I | execution_id、可选 requirement_id、summary_markdown、character_count、可空 estimated_tokens；记录准备交付的准确材料。 |
| `context_manifest_entries` | I | manifest_id、context_version_id、position；同 manifest 位置和版本唯一。 |
| `context_applications` | I | manifest_id、execution_id、applied_character_count；复合 FK 保证 manifest 属于该 execution，另行记录真正应用成功的事实。 |

“材料已整理”和“原生执行已收到”是两件事。字符数不是 token 数，未知 token 不做零值估算。过期或被更正的材料保留原版本与来源，当前指针/状态决定默认选择。

## 拍板和人工待办

| 表 | 类别 | 主要字段与关系 |
|---|---|---|
| `decisions` | M | requester_agent_id、可空 owner_leader_agent_id、goal_round_id、current_version_id、blocking、status、held_at、expires_at。这里只存生命周期与当前版本指针，不复制问题正文。 |
| `decision_versions` | I | decision_id、version_no、title、question_markdown、context_markdown、why_user、recommendation_reason、impact_markdown、明确作者。完整冻结当时的问题。 |
| `decision_options` | I | decision_id、decision_version_id、option_key、label、consequences_markdown、recommended；选项归属具体题目版本。 |
| `decision_answers` | I | decision_id、decision_version_id、答复时 decision_revision、可选 option_id、answer_markdown、user/agent 作者、可选 authorization_policy_id、authorization_snapshot_json。 |
| `todos` | M | requester_agent_id、可选 assigned_user_id/goal_round_id、title、steps_markdown、verification_markdown、blocking、status、resolution_note/resolved_at。 |

decision 的状态 revision 与问题 version 不是同一计数：挂起/发布会推进 CAS revision，但不创造新问题；改正文必须创建新的 decision_version。答复必须同时匹配题目版本和提交时 base revision。云端验证后才改变 lifecycle；旧版本答复不能唤醒已结束目标。

owner_leader_agent_id 保留历史固定归属，NULL 表示旧数据无法确定，不能按 requester 当前分组反推。自主授权答复的作者是 agent，不能伪造成用户；authorization_snapshot_json 是当时策略的不可变证据，仍不能代替服务端当次授权检查。

## 内部消息、公共交流和规则

| 表 | 类别 | 主要字段与关系 |
|---|---|---|
| `peer_messages` | M | sender_agent_id、target_agent_id、message_id、可选 goal_round_id、requires_reply、status、accepted_at/error_summary；正文在不可变 message 中。 |
| `handoffs` | M | sender、agent/group/broadcast 中恰好一种目标、goal_round_id、body_markdown、status。 |
| `handoff_deliveries` | I | handoff_id、recipient_agent_id、message_id；同接收人不重复记已接受交接。 |
| `channels` | M | name、conversation_id、description；一个公共交流区对应一个 conversation。 |
| `channel_posts` | I | channel_id、conversation_id、message_id；复合 FK 要求 message 和 channel 的 conversation 一致。 |
| `channel_mentions` | I | post_id、target_agent_id；结构化点名，不以正文字符串判断任务授权。 |
| `channel_digests` | I | channel_id、through_post_id、previous_digest_id、body_markdown、可选 execution_id、source_message_count；前卡及边界必须属于同 channel。 |
| `comments` | M | 明确 user/agent 作者、parent_comment_id、body_markdown，与 agent/group/goal/artifact/report/message 中恰好一种目标 FK。 |
| `trust_settings` | M | leader_agent_id、enabled、goal_markdown、author_user_id；业务决策授权，不代表 shell 或设备权限。 |
| `policies` | M | policy_key、title、body_markdown、rules_json、author_user_id；可同步非秘密工作规则。 |
| `git_requests` | M | requester/administrator、goal_round_id、title、description、ref/commit、status；本机 checkout 路径不在此表。 |
| `git_results` | I | request_id、author_agent_id、outcome、summary、result_commit、PR URL、checks_json；保留实际检查结果。 |
| `usage_observations` | I | source_kind/source_observation_key、可选 agent/execution/message、model、observed_at、可空 token 和 cost/currency、measurement_json。 |

usage 的去重键是来源命名空间内的原始观察事件摘要，不是会重复上报的累计 token 总数，也不是原生 session ID。聚合由查询推导。cost_microunits 使用整数避免浮点金额累计误差；单位与 currency 同时明确。API rate-limit 水位、扫描字节游标和连接状态属于设备缓存。

## 迁移来源、删除及导出边界

| 表 | 类别 | 主要字段与关系 |
|---|---|---|
| `import_batches` | M | legacy_origin_id、source_format/source_schema_version、archive_artifact_version_id、transform_version、status、verified_at、verification_summary。 |
| `legacy_records` | I | import_batch_id、source_table、带类型的 source_key_json、source_row_sha256、raw_blob_id、disposition、mappings_json、notes。 |
| `entity_tombstones` | M | entity_type、entity_id、target_revision、state=active/restored、target_deleted_at、operation_id、reason；一目标一个逻辑删除遮罩。 |

legacy_origin_id 是对旧数据来源登记的稳定 UUID，**不是**变化中的 snapshot hash。重复导入同一来源的旧整数主键，通过来源 UUID＋表名＋typed primary key 得到稳定映射；源快照 digest 只用于完整性核对。

archive_artifact_version_id **只能**指本工作区切出的原始表行、日志原字节和文件档案。旧 crew.db 同时含多个工作区和全局设置，整库一致备份必须留在本机私有迁移 vault，不能挂到某个工作区的 artifact 后随包导出。NULL/未知归属在独立恢复容器中保留，不能为了满足 FK 自动猜成当前项目。

source_key_json 示例是 `[{"column":"id","type":"integer","value":17}]`；journal 原始来源可用文件标识和原 seq。mappings_json 记录目标表名、UUID 及字段处置，属于迁移审计注释，不能被程序当作无 FK 的业务导航或授权来源。无法规范转换的工具记录和未知字段保留原字节及明确 archive_only/unresolved disposition。

tombstone 是关系删除控制，不是把全产品做事件溯源。entity_type 使用固定的领域表注册表；Repository 检查目标确在该 workspace，处理依赖遮罩、撤销窗口和 purge 闭包。此多态控制记录没有伪造的通用实体 FK。正常读取要同时尊重 mutable.deleted_at 与 active tombstone，不能把不可变理解成“用户永远不能删”。

不可变 UPDATE 被 SQLite 触发器拒绝；DELETE 故意留给显式、受控维护通道。部署时必须使云端普通运行角色没有直接硬删除权限；本领域 DDL 没有创建或授予数据库登录角色，不能把角色隔离说成已部署。本机数据库 owner 不是安全边界。永久清除需要按业务依赖处理内容/引用、blob 引用及备份保留政策，不能简单取消 UI 显示。已 purge 后的旧设备或离线包必须先只读隔离并核对服务端 epoch，防止重新上传已删除内容；恢复或 fork 需显式处理。

## SQL 与 Repository 的责任边界

| DDL 可以实际保证 | Repository／云服务必须另外保证 |
|---|---|
| UUID 外形、主键、workspace 复合 FK、必要父归属、JSON 可解析、基本枚举和载荷组合。 | 认证、当次 account/workspace 角色、租户授权、JSON 文档结构和内容尺寸。 |
| 不可变 UPDATE 拒绝；可变身份不变、revision 单调前进；同 scope 去重与 ordinal 唯一。 | `UPDATE ... WHERE id=? AND workspace_id=? AND revision=:base_revision` 的 CAS、状态机、云端每次接受加一、操作去重；不可变重发只允许同 UUID 同规范内容。 |
| blob 的 workspace 归属、digest/size 不可改、artifact/version/typed link 关联。 | 实际 hash/size 验证、verified 引用门槛、上传提交、下载权限、配额和垃圾回收。 |
| 可延迟 FK 允许一事务建立 goal/current requirement 或 artifact/current version 的环。 | 至少一个 owner、依赖图无环、分组业务角色、当前指针、跨多行聚合和生命周期一致性。 |
| 比较精确的 message/工具/round/decision 归属。 | server-only accepted sequence、stale answer 拒绝、运行租约/fence、换机不自动执行、旧设备 epoch 隔离。 |

同步 operation/outbox 用于“哪次写入是否被接受及如何重试”；audit 记录“谁因何改变了什么”。两者不能互相冒充，更不能把客户端报送的 audit 文字当成授权。领域表是业务权威状态，审计与同步协议由独立文件定义。

本机目录、PID、CLI native ID、令牌、运行锁、扫描游标、待发送重试和文件借用等，只放 `local_*` 表或 OS secret store。导出器应列举领域表与引用 blob，并验证 FK/摘要；不能把整个 home、SQLite 文件目录或未知 JSON 附件打包后称为“完整工作区导出”。
