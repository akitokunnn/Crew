# Storage v2：同步、执行权与完整导出协议

状态：设计提案。本文及同目录 DDL 不会修改现有 Crew 数据、启动服务或部署云端。SQLite 领域结构见 [sqlite.sql](sqlite.sql)，本机扩展见 [local.sql](local.sql)，云协调扩展见 [cloud.sql](cloud.sql)。云端还需应用与领域等价的 PostgreSQL DDL；SQL 解析通过不等于已经完成服务器验证。

## 1. 三层存储与唯一写入入口

| 层 | 保存内容 | 权威边界 |
|---|---|---|
| 本地 SQLite | 工作区领域实体、已接受状态的影子、本机待同步编辑、outbox 和机器绑定 | 离线可读；未获云端接受的编辑明确标“未同步” |
| PostgreSQL | 领域实体的已接受状态、权限、操作收据、连续提交日志、执行租约 | 同步工作区的协调权威，不把设备 ID 当账号 |
| 对象存储 | 聊天附件、原生历史归档、报告附件、产物版本、快照等 blob | 不可变内容；对象地址由工作区与内容摘要确定 |

每次业务写入都通过一个 Repository。本地编辑在**同一 SQLite 事务**中保存 `local_pending_mutations`、递增 `local_entity_sync_state.local_generation`、插入一个 `local_outbox` 操作并递增设备计数；**领域表只保存已接受基线**。Repository 查询把 pending proposal 叠加展示，不把本机编辑计数冒充服务端 revision。接受后的完整 commit 到达时，领域行、pending 状态、inbox 和 cursor 再在同一事务物化；任一步失败全部回滚。纯本地工作区可以由本地 authority 接受修改，但已绑定云端的工作区不能断网后悄悄降级成本地权威。JSONL 可以是只读兼容导出，不能再是另一份聊天或执行主存；审计事件也不能代替业务表反演状态。

本机文件目录、PID、原生工具定位、设备占用、密钥引用、窗口草稿和运行租约留在 `local_*`。根目录不参与实体身份。`workspace_id` 和领域实体 UUID 才是同步/导出的稳定键。

`local_workspace_locations` 保留路径历史，但同设备同一规范 `path_key` 只能有一条未退休绑定。目录改名更新位置映射；克隆副本默认创建新工作区；两者都不需要通过搬动数据库来决定身份。

## 2. 身份、账号和权限

1. 服务验证会话凭据，通过 `cloud_auth_identities` / `cloud_auth_sessions` 获得真实用户；`device_id` 仅表示经登记的同步来源与运行归属。
2. 每次推送、拉取、分页、快照读取、blob 上传完成、下载授权、执行租约获取/续租、远程命令认领及通知发送，都验证当前账号成员资格和当前工作区权限。
3. `account_memberships` 和 `workspace_memberships` 是唯一权限权威；只读成员不能写入、执行或更改成员。一般成员可编辑内容，授权变更与清除需对应管理权限。领域包内的用户和成员行只是历史署名，不能给接收者授予账号权限、恢复被撤销成员或发出邀请。
4. 本地用户可读到自己的离线数据库，因此本地 SQL 触发器用于防应用误写，不构成抵御本机所有者的安全边界。

权限与提交的竞态必须在服务器事务内解决。统一锁序为认证/账号成员守卫、工作区 sequencer、工作区成员、固定顺序的实体与 blob 行；权限变更也遵守此序。权限撤销不能与一笔已经取得成员共享锁的提交交错成“校验通过后任意写”。幂等重试同样先检查**现在**的权限；被撤销者不能借旧 operation ID 取回历史响应正文。

`cloud.sql` 与 [security.sql](security.sql) 的 SELECT RLS 是纵深防御，非完整授权实现。后者对全部明确列出的工作区域表及 workspaces 根表应用相同权限条件；全局署名/账号/设置与云控制表默认拒绝普通 reader，只有审核过的投影/API 返回最小必要字段。认证服务验证凭据，并把 request、真实 session、actor、account 和 membership revision 关联审计后，才设置事务级 `crew.auth_session_id`、`crew.actor_user_id`、`crew.actor_account_id`；读 helper 再检查会话归属、撤销、到期和当前成员关系。终端用户没有数据库连接，也不能自行设置该上下文，`SET LOCAL` 本身不是认证。领域 mutation、blob gate、管理员权限、租约验证和跨表一致性仍需受信任的服务器 committer 执行。部署使用专用数据库的 public schema，检查 DB 角色、函数所有权、`search_path` 和 RLS 覆盖；不能把表所有者或 `BYPASSRLS` 角色当受策略限制的读角色。安全 DDL 不创建部署账号，grant 模板不会实际执行。

## 3. 操作封包与本地编辑

一个 operation 对应一次工作区内的原子业务事务，不能跨多个工作区：

```json
{
  "protocol_version": 1,
  "operation_id": "稳定 UUID，发送超时后保持不变",
  "workspace_id": "工作区 UUID",
  "device_id": "登记设备 UUID",
  "authority_epoch": "4",
  "observed_deletion_epoch": "12",
  "changes": [
    {"entity_kind": "todos", "entity_id": "实体 UUID", "kind": "put", "base_revision": "7", "body": {}}
  ],
  "blob_refs": [{"blob_id": "blob UUID", "sha256": "64位小写摘要", "size_bytes": "128"}]
}
```

实际服务只接受固定 schema 和 `entity_kind` 注册表，不能接受客户端 SQL 或任意表名。UUID 统一规范化成小写文本。封包采用 [RFC 8785 JCS](https://www.rfc-editor.org/rfc/rfc8785.html)，`request_sha256` 是 JCS UTF-8 字节的 SHA-256，不能用 `json.dumps(sort_keys=True)` 冒充 JCS。

所有可能达到 64 位的 revision、seq、epoch、size、fencing token 和相应计数，在 operation、commit、cursor 与 lease 的 wire JSON 中使用**规范十进制字符串**：非负整数匹配 `0|[1-9][0-9]*`，拒绝正号、前导零、指数、小数、负值及超过数据库有符号64位上限的值。只有有安全上界的 schema/protocol 小版本使用 JSON 整数。这样不会由 JavaScript 的 `2^53` 精度限制破坏 CAS/摘要。领域 row JSON 中同类 BIGINT 字段亦按 schema 编码为字符串；数据库存储仍为整数。

任意需要精确表示的十进制数按领域 schema 使用字符串，或将原始数据存为 blob。输入拒绝 NaN、Infinity、重复对象键与孤立 surrogate；保持原始 Unicode，不自行做未约定的归一化。JCS 属性排序与数值序列化规则必须由真实 JCS 实现满足。包 manifest 的有配额文件大小字段另遵循包版本声明的安全整数上限，不与 operation wire 类型混淆。`operation_id` 重试时既不能换 body，也不能变换原作者；新修改需要新 operation ID。

可变实体采用 `base_revision` CAS：创建要求 revision 0 且 UUID 从未存在；修改/删除/恢复要求与服务器当前 revision 精确相等；接受后 revision 由服务端递增。设备时间不能决定覆盖顺序。连续离线修改同一实体须保留依赖链；前一操作冲突后，后续依赖操作暂停，不能把旧 base 自动替换成服务器 revision 再盲写。

不可变聊天消息、报告、产物版本、工具结果等按 UUID 去重：同 UUID 同 canonical body 返回原结果；同 UUID 不同 body 返回 `ID_BODY_MISMATCH`。不能 `ON CONFLICT DO NOTHING` 后声称成功。不可变内容的删除通过 `entity_tombstones` 表达，不能改写原正文。

领域表只保存已接受状态；本机尚未接受的正文、删除、消息创建等保存在 `local_pending_mutations`。Repository overlay 呈现当前编辑，独立 `local_generation` 追踪本机变化；`local_entity_sync_state.accepted_json` 只作 rebase/断点缓存，不是另一份业务权威。拉取更新领域基线时比较 pending base，保留 proposed body 并产生 `local_conflicts`。成功收据先将 proposal 标记 `accepted_waiting_pull`，只有其完整 commit 按连续水位物化后才移除 overlay/标 materialized；新 pending 消息亦此时一次插入不可变领域表。不会出现“本机把领域 revision 改成3，而服务接受/拒绝后要写回3或回退2”的问题。默认不采用“最后写入获胜”；冲突界面可选择保留服务器、以新操作重新提交编辑，或分叉副本，不能悄悄丢弃本地文字。

聊天先有稳定消息 UUID 与 `reply_to` 因果关系。离线未接受消息可以在本机作为“待同步”展示，但不能伪造最终序号；`message_positions` 由服务端在接受事务内赋予会话序号。此序号用于会话展示，**不是工作区同步水位**。

## 4. 服务器原子提交与幂等收据

接受流程由一个 PostgreSQL 事务完成：

1. 验证认证、设备登记、账号及工作区权限；取得守卫锁和工作区 sequencer 行锁，并重新核对权限、authority epoch、删除状态和最低写入协议。
2. 查询 `(workspace_id, operation_id)`。若已存在，必须匹配请求摘要和原作者；匹配则返回原收据，不重复产生领域写入或日志。
3. 对尚无收据的新操作，要求 `observed_deletion_epoch` 等于当前删除水位，否则返回 `REBASE_REQUIRED`。该值只能来自已经连续应用的提交/快照，不能从“看见最新 head”直接抄入。再验证整批 CAS、不可变内容摘要、同工作区复合外键、目标状态约束、必要执行租约和**所有引用 blob 已 verified**。成员权限与服务端控制的序号/租约字段不得由普通客户端伪造。
4. 任一验证失败，整批不改领域实体。保存确定性的 conflict/rejected 收据；业务数据不允许部分接受。可用内部 savepoint 捕获约束失败后保存拒绝收据。
5. 成功时改领域行，事务内把 `committed_through_seq` 加一，插入一条 `cloud_commits` 与全部 `cloud_change_items`，写 accepted operation 收据；检查 `change_count`、ordinal 连续性及 commit 摘要后一起 commit。

事务提交成功但响应丢失时，客户端用原封包重试即可取得原收据。接收成功响应和推进下载游标是不同动作：操作被接受不意味着客户端已经看到了此前所有提交。

不用 `BIGSERIAL` / `nextval()` 作为无遗漏水位。数据库 sequence 的变化不随事务回滚，允许空洞；本方案锁住工作区计数行，在同一事务内分配并保存提交，失败时计数一起回滚。提交顺序取决于该锁与事务提交，而非设备时间或全局序号的最大值。[PostgreSQL 事务隔离文档](https://www.postgresql.org/docs/current/transaction-iso.html)说明了 sequence 与普通事务状态的区别。

## 5. 拉取分页、崩溃和快照

拉取先得到 `(workspace_id, authority_epoch, until_seq, snapshot_generation)` 的固定阅读边界。页 token 绑定这些字段、上一个完整提交边界和协议版本；每页重新授权。服务只返回完整提交；大内容必须 blob 化，并限制一次 operation 的行数和 inline 字节数。若传输需要拆分一个大提交，先在 inbox/staging 重组并验证全部块，不能推进应用游标。

客户端只按连续提交 `N+1`、`N+2` 应用。每个完整 commit 的 inbox 去重标记、领域变更、accepted shadow、冲突记录和 `applied_through_seq` 都在**同一个 SQLite 事务**中写入。收到 `N+2` 而缺 `N+1` 时缓存或重拉；不能取已收到记录的 `max(server_seq)` 当“全部同步完成”。页返回的 `until_seq` 也不是已经应用的水位。

| 故障点 | 正确恢复 |
|---|---|
| 本地编辑事务前/后崩溃 | pending proposal、generation 与 outbox 同事务，要么都存在要么都不存在；领域基线不伪造 revision |
| 推送 commit 后响应丢失 | 保持 operation ID 与 body 重试，取得原收据 |
| 下载页中断 | 从最后完整 commit 的 cursor 重拉 |
| SQLite 应用中崩溃 | 领域与 cursor 同时回滚，安全重放 |
| blob 只下载一部分 | 不标 verified，继续传输或重下后校验 hash/size |
| cursor 早于 `retained_after_seq` | 返回 `SNAPSHOT_REQUIRED`，禁止跳到当前最大序号 |
| authority epoch 不同 | 停止自动上传/执行，重建已接受快照，再显式处理本地编辑 |

服务器生成固定事务快照与 manifest，在其确切 `through_seq` 上记录行数、schema、内容摘要和完整 blob inventory。客户端下载到 staging，验证所有数据和对象，保留本地未发送编辑后原子替换已接受基线，再从 `through_seq+1` 拉取。blob 字节可延迟下载用于普通浏览，但缺字节必须明确标出，不能标“完整可离线”。

SQLite 快照必须使用数据库一致读/备份 API，不能直接拷贝运行中主 `.db` 文件而忽略 WAL。[SQLite Online Backup API](https://sqlite.org/backup.html)提供数据库快照机制；若要生成可移植包，仍需从一致快照投影允许的领域表，不能把含 `local_*` 的整库直接导出。

## 6. 删除、过期设备与恢复边界

普通删除接受后，同时生成 `entity_tombstones` 和同步 delete 记录，并推进工作区 deletion epoch；每个 commit 带它提交后的删除水位。删除标记有工作区、实体类型/UUID、target revision、operation UUID；同 UUID 的离线旧 put 不能重新创建已删除对象。显式恢复必须有权限、匹配当前删除 revision，并生成新的 CAS 操作。不可变表不允许 UPDATE；已授权的保留期后物理 purge 通过专用维护通道执行 DELETE，不经过普通 Repository 编辑 API。

实体正文、详细 tombstone 或旧操作收据被压缩后，`cloud_entity_graves` 仍保留最小的类型/UUID/最后 revision/删除 epoch，不留正文，继续拒绝同 UUID 的普通 create。不能仅删掉 tombstone 再把“查不到行”解释为从未存在。经授权的显式恢复按 CAS 处理此 grave；全工作区清除后由 workspace grave 阻止重用整个作用域。包中保留的领域 tombstones 用于离线过滤；它们不是凭据，也不能证明是云端最新删除状态。

有效设备提交已应用水位。日志压缩只能越过有效设备都已确认的完整提交边界，并有可用快照；长期离线设备不能无限阻止清理，达到账号策略的 inactive-device 窗口后标记 expired，再联网必须重建快照并重新验证 membership、epoch 与 tombstones。

**未知的新设备全离线只拿旧导出包，无法知道云端后来发生的删除或权限撤销。** 这种导入默认 quarantine/只读；联网核对后才接续现行工作区。用户也可明确“创建独立副本”，生成新 workspace/entity UUID。不能承诺离线绝对防复活，也不能把旧 snapshot 内容直接转换为全部新 upsert 上传。已知工作区的旧包不能覆盖较新云端，必须逐项 CAS/冲突处理或分叉。

工作区删除立即增加 deletion epoch、禁止新写入和租约、撤销当前租约与下载授权。内容清理后保留最小 `cloud_workspace_graves`（不含名称、消息、成员、路径或凭据）拒绝该 UUID 被旧缓存重新注册。其不带内容的防重放记录与内容 retention 分别披露。

## 7. blob 完整性、pin 和并发 GC

`blob_objects` 的作用域是工作区：`(workspace_id, blob_id)` 复合引用，`UNIQUE(workspace_id, sha256)`，物理 key 为 `workspace_id/sha256`。v2 暂不做跨工作区物理去重，避免 hash 知情者绕过对象授权。

上传分为声明、取得有期限上传通道、分块写 staging、完成校验和原子发布。服务端读取完成对象或使用可信存储提供的完整 SHA-256 证明，验证实际 hash、size 与 workspace 绑定，才把领域 blob 状态变为 `verified`。ETag、仅客户端声称的 checksum、multipart 还未完成均不足以引用。相同 blob UUID 不同 hash/size 必须拒绝。

业务 commit 在同一服务器事务中锁定全部 blob 元数据并验证 verified 与工作区归属，再建立实体引用；普通客户端不能直接把状态写成 verified。对象完成而 DB commit 失败会留下无引用对象，按宽限期回收；DB 尚有引用而对象未完成不能被接受。

生成/下载完整快照、导出和 blob 传输期间，先 pin **传递闭包中的每个 blob**，包括快照数据库、manifest 与各附件。`cloud_blob_pins` 绑定 owner 与期限；过期前需续期，失去 pin 则作业停止完成声明并重新验证。`local_blob_pins` 为本机作业的持久 pin，只有 owner 作业终止并核验后显式释放，不靠设备墙钟自动释放。缓存计数不能是另一个权威 pin 账本。

GC 必须同时排除领域有效引用、tombstone/backup 保留引用、有效 snapshot/export/transfer pin、未结束上传和 retention hold；检查与标记候选之间要有数据库事务/对象版本保护。对象删除后元数据不能仍显示 verified 可用。崩溃作业的 pin 由恢复程序核对作业状态后清理，不能因为 PID 消失就推断导出已经成功。

## 8. 同步历史不等于获得执行权

云端对每个 agent、每个 goal 各维护单设备 lease slot。goal lease 可被同一设备上的多个成员执行共享，但其他设备不能同时接管该 goal；agent 另有自己的独占 lease。运行一个目标内执行需要两份有效且归属同一设备的 lease。

获取/续租在服务器事务内锁住对应 `cloud_run_fences`，检查成员权限、目标状态和当前 holder，使用服务器时间判过期。新所有者使 fencing token 严格前进；释放、撤销、设备撤销和 authority epoch 变化都使旧证明失效。证明包含工作区、scope、资源 UUID、设备、epoch、token、lease revision 和有效期。客户端重启后旧进程 monotonic deadline 不可复用，必须联网重新核验。这里描述云同步工作区的执行权；纯本地 authority 是另一种明确模式，不能被当作断网抢占云端 lease 的后门。

启动 native 任务、每次发起有副作用工具、持久化该执行的权威状态，以及工具代理转发请求前，都检查当前权限和 lease/epoch。普通离线状态允许阅读、编辑、草稿与排队，不能启动这些执行。聊天复制、历史下载、收到远程命令都不能自行获取执行授权。备份恢复切换权威时，必须从未被旧备份回滚的协调控制面分配新 epoch 并使旧租约失效，不能照搬快照中的旧 lease。

断网时工具代理立即拒绝新动作，本地 watchdog 在失去在线验证/心跳后请求中断，并在保守 monotonic deadline 前终止可控 native 进程组，记录 stopped 或 unknown。**已经运行的 shell、脱离进程组的子进程、用户独立终端、已发出的外部请求，无法靠数据库租约绝对撤回。** fencing 能拒绝受控入口和旧执行继续写权威状态，但不能证明外部副作用绝对只发生一次。接管显示“上次执行结果待核实”，需要核对真实产物/外部状态后重试，不能自动重放不确定的支付、发布或消息发送。

## 9. 手机号/消息渠道远程命令与通知

手机应用使用正常账号认证。QQ/Lark 等回调先验证 provider 签名与时效，再经 `cloud_remote_identities` 找到已绑定用户，随后重新检查其工作区权限。provider 账号/机器人命名空间、event ID 与 body hash 一起写 `cloud_provider_inbox`；它与本机 `local_sync_inbox` 的同步 commit 是不同概念。同 provider event ID 不同 body 不能当成功重复消息。

远程命令使用稳定 command UUID 与 client operation UUID 去重，认领时绑定当前设备 agent lease，并验证目标、需求 revision、有效期和人工暂停状态。服务端可保证一个逻辑命令记录不重复创建；设备处理和外部效果仍是至少一次传递。`cloud_command_receipts` 按 command/lease/stage 去重，区分 received/started/completed/unknown；同键异 body 拒绝，收到回执不等于执行成功。命令最终状态的转换仍检查当前认领 lease 并做 CAS，旧设备迟到回执不能覆盖新归属。已经记录 started 后失联视为不确定，不能因为没有 completed 就自动再跑；用户核实后需新明确重试命令。崩溃于副作用之后、completed 收据之前时，不自动把 unknown 当 failed 再执行。

通知以 `(workspace, recipient, channel, logical_event_id)` 建唯一 job，逐次记录 provider idempotency key、message ID 和 receipt。每次投递前再次检查权限及订阅；仅恢复历史不会创建新通知 job。provider 支持幂等时复用其键；不支持或回执丢失时可能重复，状态为 unknown。不能声称第三方通知 exactly-once。恢复旧收件档案不激活历史队列，不把旧 provider event ID 当新服务器 seq。

## 10. 完整 export、换电脑和 import

导出是自包含状态包，而非依赖服务端剩余日志的变化流：

```text
workspace-export/
  manifest.json
  data.sqlite                   # 领域快照；不含 local_*、cloud_* 或密钥
  blobs/sha256/<sha256>          # inventory 中每一份必需内容
```

manifest 至少记录格式/schema/min-reader/min-writer、workspace UUID、source authority epoch、准确的 accepted cursor/deletion epoch、快照摘要、每表行数、完整 blob 清单及 SHA-256/size、导出完整性与是否有未同步编辑、签名/来源信息。对齐 [export-format.md](export-format.md)：包 `consistency` 为 `complete` / `metadata_only`，源状态另由 `source.has_pending_changes` 表达，不混为同一枚举。manifest 的摘要不自我递归，包签名在封装层覆盖 manifest 摘要。签名验证来源完整性，不授予成员权限。

“完整”必须包含范围内已保存的全部领域历史、报告正文、产物版本、上下文、旧数据导入映射和可携带原生历史归档的必需字节。任何必需 blob 缺失/hash 不符都不能标完成；可另提供明确的轻量包，但不能称完整导出。任意 Git 工作树及未被收录的项目文件不自动包含；换机继续开发需要另行克隆/复制代码，已收录产物按包恢复。原生历史归档可跨机查看，不保证原工具 session 能跨机 resume；本机重新验证工具版本、账号、路径后才能绑定，必要时用新 execution 接续。

存在未同步编辑时，默认提示“同步后导出”或“导出含未同步编辑的离线副本”。后者在受信 staging 中把领域基线与 Repository overlay **物化为新的快照**，不能直接拷贝基线库漏掉编辑。manifest 用 `source.kind=local` 与 `has_pending_changes=true` 标明；accepted cursor 只描述已接受基线，不能冒充全部内容已被云端接受。它不携带机器 outbox，不声称能够重建原离线操作链，也不自动重放。首版先只读 quarantine 或显式 fork；联网合入时逐项比较当前云状态，经用户处理后创建新 CAS 操作。未发送窗口草稿默认排除；用户可显式把内容保存成领域记录后再导出。

| 导入方式 | ID 与权限 | 行为 |
|---|---|---|
| 新设备接续现有工作区 | 保留 workspace/entity UUID | 先 quarantine；联网授权并核对 epoch、删除状态和当前快照；旧包不得覆盖云端 |
| 恢复独立灾备 | 仅授权恢复流程保留 UUID | 恢复到 staging、核验完整性；云端权威恢复分配新 epoch；不能自动恢复租约 |
| 创建独立副本 | 新 workspace UUID，并重映射全部领域 UUID/FK、JSON 中已注册类型引用及 provenance | 无外部账号权限、native 绑定或通知动作；同工作区去重的 blob 在新作用域重新声明/上传 |
| 已知工作区合入旧包 | 保留可证明同源 UUID | 逐项比较与 CAS/冲突；绝不把 snapshot 逐行当新创建，也不按设备时间覆盖 |

复制 fork 必须维护完整 ID 映射。保留 `import_batches` / `legacy_records` 的来源原文与同源映射；不能用设备路径、导出时间或 snapshot hash 作为永久原始身份。外部 URL 和纯文本提及不能自动冒充结构化引用；无法安全重写的引用在导入预览中列出。

### 纯本地工作区首次上云：专用 bootstrap

首次上云是一次**受控的权威切换**，不是把本地已接受历史转换成普通 CAS create 再逐条重放：

1. 本机停止并确认本地独占执行已退出，冻结该工作区的本地 authority 写入；核验领域一致快照、完整 blob 闭包及尚未处理的 pending 编辑。存在未同步 proposal 时先处理或明确保存为离线副本，不能把它们冒充已接受历史。
2. 当前登录用户选择其确有创建权限的云账号。服务端验证现行账号权限并预检 UUID：只允许初始化一个**全新的云工作区**。本地 account ID、成员行、路径、包签名或 workspace UUID 都不能证明云端所有权；若该 UUID 已存在或命中 workspace grave，不允许以 bootstrap 覆盖、认领或复活。
3. 服务端在 staging 验证完整快照、业务 UUID/复合 FK、不可变内容摘要和每个 blob 的工作区作用域、真实 hash/size。符合条件时保留业务实体 UUID、已校验的本地 revision 和历史署名作为初始 seed；不伪造一次次历史 CAS。账号归属与当前 ACL 由服务端按登录账号重新建立/归一，历史署名不能变成登录身份或成员授权；原始身份映射保留在 provenance 中。
4. 服务为这个新云权威分配新的 authority epoch，建立有明确初始水位的受控 snapshot 和幂等 bootstrap 回执。服务端归一过账号/ACL 的最终 snapshot 才是云端接受结果；客户端不能根据旧本地 revision 推算该结果。重试同一 bootstrap 只核实并返回既有结果，不重复创建工作区。
5. 本机先持久保存云端已接受的回执/恢复断点，再将服务返回的 snapshot 载入新的受信 staging，验证领域记录与 blob。**不在原库上 UPDATE 来倒退或重复写入 revision**，而是验证后原子切换 accepted 基线、云 epoch、cursor 和权限状态；设备路径重新绑定，旧运行租约不继承。
6. 服务器接受后若本机下载、校验或激活失败，本机保持冻结，按回执重试受控恢复；不能退回本地 authority 继续写而形成双主。只有新基线激活成功，才结束切换并允许正常同步；执行仍需重新获取云端 agent/goal lease，不自动恢复旧任务。

这一路径只用于从未绑定现行云权威的纯本地工作区。对已经有云端身份的工作区，必须走当前权限、epoch、删除账本和水位校验后的恢复，或由用户明确 fork；不能把“首次上云”作为绕过冲突、权限撤销或删除的入口。

## 11. 撤权、清除、保留期与加密

撤权立即影响新 API 请求、租约、命令认领和新 blob 下载授权，取消未投递通知。已下载的离线字节无法远程保证抹除；短期已签发下载 URL 在到期前可能仍有效。可用代理下载把检查移到每次请求，但仍不能收回已经接收的内容。客户端收到撤权后关闭同步/执行，按产品数据策略清除本机缓存；用户拥有的既有导出包不等于可撤回云端文件。

账号 retention policy 必须有明确参数：同步日志天数、inactive-device 天数、backup 天数、tombstone 天数和 live-purge 宽限期。DDL 不硬编码未知运营承诺。产品启用服务前必须给出实际值与清除预计日期；purge 请求冻结操作后，清理在线 DB 内容、对象及缓存，再进入 backups_pending，等所有备份轮换期限到达才标 completed。

备份恢复先重放独立保存的删除/撤权登记再开放服务，不得把恢复备份当撤销删除。存在合法 retention hold 时显示被保留范围及原因类别，不能显示“已经彻底清除”；释放后继续 purge。最小防重放 grave 的保留与内容保留分别说明。审计记录只保存必要操作元数据且有独立保留策略，不保存凭据或无限留存正文。

v2 默认采用传输加密、存储加密和服务托管密钥，服务端可以查询授权范围内的明文元数据/正文。**这不是零知识端到端加密。** 如果后续支持客户端持钥模式，服务端只能查询明示可见的元数据，正文搜索移到客户端，另行定义索引泄露范围、密钥恢复和跨设备授权；不能同时宣称服务端不可见正文与任意正文 SQL 查询。

## 12. DDL 能保证什么，服务还要验证什么

| 由 DDL 保证 | 必须由协议/服务保证 |
|---|---|
| 复合 FK 不跨 workspace、固定枚举、唯一 operation/commit/ordinal、部分唯一活动路径和本机执行槽 | 当前账号权限、完整 CAS、不可变同 ID 同 body、固定实体注册表 |
| 领域 blob 的同作用域引用、上传声明的 hash/size 字段完整性 | 真实对象上传完成、服务端 hash/size 校验、verified 状态不可伪造 |
| cloud seq 和租约记录的唯一性与非负值 | sequencer 行锁与同事务提交、commit change_count/摘要完整、租约双 scope 与 token 有效 |
| 本机 outbox/pending/generation 同事务、cursor/inbox 可与已接受业务行同事务存储 | Repository overlay 查询与物化、游标连续性、pending 编辑不伪造领域 revision |
| tombstone/provenance、pin、purge/retention 的记录结构 | 删除过滤、GC 可达性闭包、权限撤销、备份轮换、全离线未知设备 quarantine |

需实现并通过的验收包括：跨租户/工作区伪造引用拒绝；同 operation/body 崩溃重试；同 UUID 异 body 拒绝；跨表批次任一步失败零部分写；缺提交页不推进 cursor；本地修改不被拉取覆盖；旧 cursor 强制快照；离线旧包不会默认复活删除；blob 上传未完成/跨域/错误摘要不能引用；导出期间 GC 不删 pin；租约过期/撤权/epoch 变化后新动作被拒；已脱离控制的外部效果标 unknown；通知无回执不宣称 exactly-once；完整包在无云日志条件下重建历史并校验全部必需 blob。

PostgreSQL RLS 与行锁的官方约束可分别参照 [Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) 与 [SELECT locking clause](https://www.postgresql.org/docs/current/sql-select.html)。这些是设计依赖，不能替代实际 PostgreSQL 权限、并发、崩溃和对象存储集成测试。
