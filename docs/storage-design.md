# 存储设计：访问模式 → 键／索引 → 一致性 → 事务计划

日期：2026-10-04（D0）。依据：架构 §3、工程契约 §4、ADR-0002／0003／0004／0008／0009／0010。

## 1. 表

| 表 | 用途 | 计费 | 备份 | 访问角色 |
|---|---|---|---|---|
| `ledger-main` | 全部业务权威状态 | on-demand | PITR 35 天；Streams（NEW_IMAGE，供 outbox relay） | 业务 API、确认、Worker（受限条件）、维护 |
| `ledger-deletion-journal` | 最小删除标记，防止从备份复活 | on-demand | PITR 35 天 | 仅维护任务与恢复流程 |

通用属性：`PK`、`SK`、`type`（实体名）、`version`（整数，乐观锁）、`created_at`／`updated_at`（UTC ISO-8601，毫秒）、可选 `GSI1PK`／`GSI1SK`、可选 `ttl`（epoch 秒，仅用于明确允许自动过期的项）。

金额：原币金额以**整数最小单位**（`amount_minor`）＋币种元数据 `minor_digits` 存储；折算值存 `nzd_minor`／`cny_minor`；汇率值存十进制字符串（不截断）。DynamoDB Number 只用于整数。

## 2. 键设计

### 2.1 账号命名空间

| 实体 | PK | SK | 关键属性 |
|---|---|---|---|
| User | `USER#<uid>` | `PROFILE` | cognito_sub、login_name、display_name、status(active/disabled)、must_change_password、session_epoch、is_system_admin |
| 用户家庭镜像 | `USER#<uid>` | `FAM#<fid>` | role、status（与 Membership 同事务维护，仅供列出“我的家庭”） |
| 用户收到的邀请镜像 | `USER#<uid>` | `INVITE#<fid>#<iid>` | expires_at、status |
| 飞书绑定（用户侧） | `USER#<uid>` | `FEISHU` | tenant_key、open_id、default_family_id、binding_epoch、status(active/suspended) |
| 登录名映射 | `LOGIN#<lower(name)>` | `LOGIN` | uid、state(active/reserved)、rename_op_id、reserved_until |
| 账号目录 | `USERS` | `<uid>` | 供系统管理员列出账号，避免 Scan |
| Cognito sub 映射 | `SUB#<sub>` | `SUB` | uid |
| 飞书身份映射 | `FSID#<tenant>#<open_id>` | `FSID` | uid、binding_epoch |
| 一次性绑定码 | `BINDCODE#<sha256(code)>` | `CODE` | uid、family_id、expires_at、used；`ttl`=到期+1 天 |
| 账号级动作回执 | `USER#<uid>` | `ACTION#<key>` | 创建家庭、改资料、改名、绑定等非家庭范围动作（永久，无正文） |
| 改名操作 | `USER#<uid>` | `RENAME#<op_id>` | old、new、state(pending/cognito_done/done/failed)、step 时间 |

### 2.2 家庭分区 `PK=FAMILY#<fid>`

| 实体 | SK | 说明 |
|---|---|---|
| Family | `META` | name、timezone（默认 Pacific/Auckland） |
| FamilyConfig | `CONFIG` | default_currency、default_payment_method、category_manifest_version、version |
| Membership | `MEMBER#<uid>` | role(admin/member)、status(active/removed)、member_version、joined_at、removed_at |
| Invitation | `INVITE#<iid>` | invitee_uid、role、invited_by、expires_at、status |
| Category | `CAT#<cid>` | kind、parent_id（叶子）、name、status(active/disabled/deleted)、redirect_to、sort、version |
| 账目排序项（权威） | `ENTRY#<YYYY-MM>#<YYYY-MM-DD>#<created_at>#<eid>` | 完整账目字段、refunded_minor、refund_ids、receipt_group_id、attachment refs |
| 账目定位 | `ENTRYLOC#<eid>` | sk（当前排序项或回收站项 SK）、state(active/trashed/purged) |
| 回收站项 | `TRASH#<eid>` | 删除时整项移入；deleted_at、deleted_by、purge_after；GSI1 到期 |
| 月版本 | `MONTHVER#<YYYY-MM>` | version 计数（ADR-0010） |
| ReceiptGroup | `RGROUP#<gid>` | currency、total_minor、attachment_id、child_entry_ids |
| Batch | `BATCH#<bid>` | actor_uid、source(web_ai/feishu)、job_id、status、expires_at、version |
| Candidate | `BATCH#<bid>#CAND#<cid>` | 候选字段、field_sources、missing_fields、needs_review、version、status、entry_id、receipt_group_id、fx_preview |
| Job | `JOB#<jid>` | actor_uid、source、business_key（web：actor＋Idempotency-Key；飞书：tenant＋message_id）、input refs、status、attempt、lease_until、fencing_token、model_calls（≤2）、usage（known 小计＋unknown 次数）、error_class；分层结果 `outcome`＝{request_returned, schema_valid, semantic_valid, published}（ADR-0011）；版本绑定 model_id、prompt_version、schema_version、config_version |
| ActionReceipt | `ACTION#<actor_uid>#<key>` | kind、fingerprint、status、result_ids、result_versions（永久，ADR-0008） |
| Attachment | `ATT#<aid>` | uploader、s3_key、s3_version_id、sha256、content_type、bytes、pixels、status(pending/ready/rejected)、ref_count、ref_version、orphan_after |
| 家庭汇率修正 | `RATE#<YYYY-MM-DD>#<CUR>` | usd_value、revision、entered_by、reason |
| 家庭启用币种 | `CUR#<CUR>` | enabled_at、enabled_by |
| 导出任务 | `EXPORT#<jid>` | filter、status、s3_key、expires_at；`ttl` 7 天 |
| 重算任务 | `RECOMPUTE#<jid>` | scope、preview_digest、status、progress_cursor、done_count |
| Audit | `AUDIT#<ts>#<id>` | actor、action、object、before/after（仅 ID、版本、字段名及金额／分类等结构值，不含备注原文）；`ttl` 180 天 |
| Outbox | `OUTBOX#<id>` | kind(recognize/feishu_card/feishu_receipt/alert)、payload refs、status、attempts、next_attempt_at |

### 2.3 全局命名空间

| 实体 | PK | SK |
|---|---|---|
| 币种元数据 | `CURRENCY` | `<CUR>` |
| 供应商汇率组 | `RATES#ecb` | `<YYYY-MM-DD>`（usd 基准值 map、revision、fetched_at） |
| 飞书事件去重 | `FSEVT#<event_id>` | `EVT`；`ttl` 30 天 |
| 费用记录 | `COST#<YYYY-MM>` | `USAGE#<ts>#<id>`（模型 tokens、unknown 标记）、`BILL#<date>`、`ALERT#<threshold>`（去重） |

### 2.4 GSI1（稀疏，到期工作）

`GSI1PK = WORK#<kind>#<shard 0-3>`，`GSI1SK = <due_at ISO>#<PK>#<SK>`。kind：`outbox`、`trash_purge`、`candidate_expiry`、`attachment_orphan`、`invite_expiry`、`export_cleanup`。处理完成即移除 GSI 属性。查询为最终一致，处理前对权威项做强一致读和条件写。

## 3. 访问模式

| # | 访问模式 | 操作 | 一致性 |
|---|---|---|---|
| A1 | 登录名→用户 | GetItem `LOGIN#` | 强 |
| A2 | JWT sub→用户＋状态 | GetItem `SUB#`→`USER#/PROFILE` | 强 |
| A3 | 我的家庭列表 | Query `USER#<uid>` begins_with `FAM#` | 强 |
| A4 | 授权检查 | GetItem `FAMILY#/MEMBER#<uid>`；提交事务中 ConditionCheck | 强 |
| A5 | 月 Dashboard／月清单 | Query `FAMILY#` begins_with `ENTRY#<YYYY-MM>#`，倒序，分页 | 强 |
| A6 | 按 ID 取账目 | GetItem `ENTRYLOC#`→GetItem sk | 强 |
| A7 | 回收站 | Query begins_with `TRASH#` | 强 |
| A8 | 分类目录 | Query begins_with `CAT#` | 强 |
| A9 | 候选批次 | Query begins_with `BATCH#<bid>` | 强 |
| A10 | 动作回执查询 | GetItem `ACTION#<actor>#<key>` | 强 |
| A11 | 汇率解析 | GetItem `FAMILY#/RATE#d#CUR`（×3）＋ GetItem `RATES#ecb/<d>`，d 由业务日期向前至多 8 天 | 强（家庭）／最终（全局只读历史） |
| A12 | 成员、邀请 | Query begins_with `MEMBER#`、`INVITE#` | 强 |
| A13 | 到期工作 | Query GSI1 `WORK#<kind>#<shard>` GSI1SK ≤ now | 最终→处理前强读 |
| A14 | 飞书身份→用户 | GetItem `FSID#` | 强 |
| A15 | 费用月汇总、告警去重 | Query `COST#<YYYY-MM>`；条件 Put `ALERT#` | 强 |
| A16 | 照片引用 | GetItem `ATT#` | 强 |
| A17 | 重算范围 | Query A5 按月份范围 | 强 |

不使用 Scan 计算任何业务结果。维护任务只扫 GSI1 分片。

## 4. 事务计划

记号：P=Put，U=Update，D=Delete，C=ConditionCheck。所有事务都含：`C USER#<uid>/PROFILE`（status=active 且 session_epoch=读取值）、`C FAMILY#/MEMBER#<uid>`（status=active 且 member_version=读取值）——下表以“鉴权2”表示；`P ACTION#…`（attribute_not_exists）记为“回执1”；`P AUDIT#…` 记为“审计1”；`U MONTHVER#m` 记为“月版本 k”。

| 用例 | 事务项 | 项数上限 |
|---|---|---|
| T1 手工创建账目 | 鉴权2＋`C CONFIG`(manifest_version)＋`P ENTRY#`＋`P ENTRYLOC#`＋回执1＋审计1＋月版本1＋（附件）`U ATT#` ref_count+1 且 status=ready ×≤3 | 11 |
| T2 修改账目（同月） | 鉴权2＋C CONFIG＋`U ENTRY#`(version=expected)＋回执1＋审计1＋月版本1＋附件增减 ≤6＋（若是退款）`U 原消费` 额度重算 | 14 |
| T3 修改账目（跨月） | 鉴权2＋C CONFIG＋`D 旧ENTRY#`(version=expected)＋`P 新ENTRY#`＋`U ENTRYLOC#`(sk=旧)＋回执1＋审计1＋月版本2＋附件 ≤6＋退款原项1 | 17 |
| T4 创建退款 | 鉴权2＋`U 原消费ENTRY#`（state 有效、currency 相同、refunded_minor+x ≤ amount_minor、version=读取值；refunded+=x，version+1）＋`P 退款ENTRY#`＋`P ENTRYLOC#`＋回执1＋审计1＋月版本≤2 | 10 |
| T5 删除账目（入回收站） | 鉴权2＋`D ENTRY#`(version=expected、refund_ids 为空或随同删除)＋`P TRASH#`＋`U ENTRYLOC#`＋回执1＋审计1＋月版本1；若“一并删除关联退款”每笔退款再 D/P/U 3 项＋原消费额度 | 9＋3n |
| T6 恢复 | 鉴权2＋`D TRASH#`(purge_after>now)＋`P ENTRY#`＋`U ENTRYLOC#`＋（退款）`U 原消费` 额度条件＋回执1＋审计1＋月版本1 | 10 |
| T7 批次确认（≤10） | 鉴权2＋C CONFIG(manifest)＋`U BATCH#`(status=open、expires_at>now、version)＋每候选 `U CAND#`(version=展示版本、status=ready、fx_revision 绑定)＋每账目 `P ENTRY#`＋`P ENTRYLOC#`＋`P RGROUP#`(≤1)＋`U ATT#`(≤1)＋回执1＋审计1（批次汇总一条）＋月版本 ≤10 | 7+3×10+10 = 47 ≤ 100 |
| T8 分类改名／移动 | 鉴权2（admin）＋`U CAT#`(version)＋`U CONFIG`(manifest_version+1)＋回执1＋审计1 | 6 |
| T9 分类合并 | 鉴权2＋`U 源CAT#`(redirect_to，status=disabled)＋`C 目标CAT#`(active、同 kind、无 redirect)＋`U CONFIG`＋回执1＋审计1 | 7 |
| T10 成员变更 | 鉴权2（admin）＋`U MEMBER#目标`(member_version+1)＋`U USER#目标/FAM#`＋（降级／移除管理员时）`U META` admin_count-1 且条件 admin_count>1＋回执1＋审计1 | 7 |
| T11 接受邀请 | `C USER/PROFILE`＋`U INVITE#`(status=pending、未过期)＋`P MEMBER#`＋`P USER#/FAM#`＋`U USER#/INVITE#…`＋回执1＋审计1 | 7 |
| T12 Job 认领 | `U JOB#`(status∈{queued,running 且 lease 过期}，attempt<上限；attempt+1、fencing_token+1、lease_until) | 1 |
| T13 发布候选 | 鉴权2（发起人当前仍有效，ISE-011）＋`C CONFIG`(manifest_version=生成候选时使用的版本)＋`U JOB#`(status=running、fencing_token=持有值、lease_until>now，ISE-017)＋`P BATCH#`＋`P CAND#`×≤10＋`P OUTBOX#`(卡片，飞书来源) | 16 |
| T14 飞书事件接收 | `P FSEVT#`(not_exists)＋`P JOB#`(job_id＝hash(tenant, message_id)，not_exists；不同 event_id 的同一消息只产生一个 Job)＋`P OUTBOX#`(recognize) | 3 |
| T15 汇率重算确认（每分片） | 鉴权2＋`U RECOMPUTE#`(progress_cursor=期望)＋每账目 `U ENTRY#`(version 与预览一致)×≤20＋月版本 ≤2＋审计1 | 26 |

T7 中同一 DynamoDB item 不能在一次事务中出现两次：月版本按月份去重；同一批次的 ATT# 只出现一次；候选与账目各自独立项。若某候选与另一候选指向同一附件，附件只做一次 ref_count+n。

## 5. 一致性与恢复要点

- 外部服务（S3、Cognito、飞书、Bedrock）不进入事务。跨服务流程先写 pending 状态（rename op、upload intent、outbox），再执行外部动作，再条件回写；失败可沿同一操作 ID 重试或查询。
- Outbox 写入与业务写同事务；Streams 触发 relay，GSI1 `WORK#outbox` 兜底扫描。relay 投递后回写失败会重复投递，Worker 以 Job 状态＋fencing token 去重。
- 退款额度、分类 manifest、成员版本、候选版本均在提交事务内条件检查，读时检查只用于提前友好报错。
- 删除日志：清理 TRASH 项时先写 `ledger-deletion-journal`（entry_id、family_id、purged_at、attachment version ids），再删业务项与照片版本。恢复环境开放流量前先重放删除日志。

## 6. 留存

| 数据 | 留存 |
|---|---|
| 有效账目、照片 | 长期 |
| 回收站账目 | 30 天后清理正文，ENTRYLOC 保留 state=purged |
| 候选正文 | 7 天到期后清除，最小状态保留 400 天 |
| 拒绝／过期／孤立照片 | 7 天 |
| ActionReceipt | 永久（无正文） |
| 飞书事件去重 | 30 天 |
| Audit | 180 天 |
| 删除日志 | ≥ PITR 窗口 + 30 天 |
| 导出文件 | 7 天（下载链接 5 分钟） |
