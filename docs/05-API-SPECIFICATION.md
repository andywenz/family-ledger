# API 接口设计文档（API Specification）

## 0. 文档信息

| 项目          | 内容                                                         |
| ------------- | ------------------------------------------------------------ |
| 项目名称      | 小家账本（family-ledger）                                    |
| 文档版本/状态 | v1.0 / Approved                                              |
| API 负责人    | 项目所有者（个人项目）                                       |
| 更新日期      | 2026-10-12                                                   |
| OpenAPI 文件  | `contracts/openapi.yaml`（OpenAPI 3，76 个接口）             |
| 相关文档      | [PRD](./01-PRD.md) · [Architecture](./03-SYSTEM-ARCHITECTURE.md) · [Data Model](./04-DATA-MODEL.md) |

> 机器可校验的事实来源是 `contracts/openapi.yaml`：后端运行时按它校验请求，前端类型由它生成，`scripts/check_contracts.py` 校验它与验收映射一致。本文记录通用规范、关键说明与接口索引，不重复字段定义；字段以契约为准。

## 1. API 概览与边界

- **提供什么**：网站使用的 JSON over HTTPS 接口，加两个飞书平台回调。没有面向第三方的公开 API，没有 GraphQL 与 WebSocket。
- **调用方**：本项目前端（浏览器）、飞书平台（回调）。
- **不负责**：页面渲染、汇率与账单的抓取（由后台定时任务完成，接口只读本地数据）。
- **风格**：REST；资源嵌套在家庭之下（`/families/{fid}/...`），账号相关在 `/me`、`/admin`。

## 2. 环境、域名与版本

| 环境     | 基础地址                                  | 说明                                             |
| -------- | ----------------------------------------- | ------------------------------------------------ |
| 本地开发 | `http://localhost:8787/v1`                | `LEDGER_ENV=local`，使用本地身份与存储替身       |
| 生产     | 与网站同源的 `/v1`（域名在私人配置中）    | CloudFront 按路径转发到 HTTP API；无需 CORS      |

- **版本策略**：路径前缀 `/v1`；契约版本号在 OpenAPI `info.version`。向后兼容的新增（新字段、新可选参数、新接口）不升版本；破坏性变更须新前缀并保留旧版本一段时间。
- **健康检查与版本读回**：`GET /v1/health`（API-020）返回提交号与制品摘要，部署后必须与清单一致。

## 3. 通用协议约定

| 项目       | 约定                                                                                              |
| ---------- | ------------------------------------------------------------------------------------------------- |
| 协议       | HTTPS；响应头含 HSTS、`nosniff`、`SAMEORIGIN`                                                     |
| 请求格式   | `application/json`；文件不经 API 上传，见第 11.3 节                                               |
| 金额       | 十进制字符串（如 `"5.00"`）；`*_minor` 字段为整数最小单位；前端不得自行累计金额                   |
| 时间       | UTC ISO-8601；`business_date` 为家庭时区的日历日期 `YYYY-MM-DD`；月份参数为 `YYYY-MM`             |
| ID         | 不透明字符串；不要解析其内部结构                                                                  |
| 语言       | 请求头 `X-Ledger-Lang: en` 时错误 `message` 为英文；缺省为中文；错误 `code` 不随语言变化          |
| 追踪       | 每个响应带 `request_id`（错误体内）；日志以它关联                                                 |
| 扩展字段   | `x-authz`（权限）、`x-idempotent`、`x-versioned`、`x-acceptance`（验收 ID），仅用于契约测试与文档 |

## 4. 身份认证与授权

### 4.1 身份认证方式

- **登录**：`POST /auth/login` 以登录名加密码换取短期 `access_token`（15 分钟，只放在浏览器内存）；同时在 `Secure; HttpOnly; SameSite=Strict; Path=/v1/auth` 的 cookie 中种下 `refresh token`（30 天，可撤销）。
- **使用**：业务接口携带 `Authorization: Bearer <access token>`。
- **刷新与退出**：`POST /auth/refresh`、`POST /auth/logout` 使用 refresh cookie，并要求双提交 CSRF：非 HttpOnly 的 `ledger_csrf` cookie 必须等于请求头 `X-CSRF-Token`，且 `Origin` 在白名单内，否则 `csrf_failed`。
- **首次登录与重置**：登录返回挑战，经 `POST /auth/challenge` 设置新密码；未完成前业务接口返回 `password_change_required`。
- **会话失效**：改密、管理员重置、停用账号会使此前签发的令牌一律 401，并暂停飞书写入授权。
- **飞书回调**：不使用令牌，用平台签名、时间戳、加密与应用租户白名单校验（`x-authz: feishu_signature`）。

### 4.2 授权规则

> 授权范围只从可信身份上下文与成员关系推导，不接受客户端提供的 user_id 作为所有权依据。

| `x-authz`          | 含义                                                                  | 典型接口                            |
| ------------------ | --------------------------------------------------------------------- | ----------------------------------- |
| `public`           | 无需登录                                                              | 健康检查                            |
| `pre_auth`         | 认证流程本身                                                          | 登录、挑战、刷新、退出              |
| `authenticated`    | 已登录且无需改密                                                      | 我的账号、家庭列表、全局币种        |
| `self`             | 仅本人资源                                                            | 改资料、改密码、飞书绑定、接受邀请  |
| `member`           | 当前家庭的有效成员                                                    | 总览、清单、记账、导出、查看分类    |
| `family_admin`     | 当前家庭管理员                                                        | 成员、邀请、分类、汇率、默认值      |
| `owner_or_admin`   | 账目录入人或家庭管理员                                                | 修改、删除、恢复账目                |
| `candidate_owner`  | 候选的发起人（管理员也不例外）                                        | 识别任务、候选编辑与确认            |
| `system_admin`     | 系统管理员（全站运维，不含家庭账目读取）                              | 账号管理、费用与告警、全局币种      |
| `feishu_signature` | 飞书平台签名校验通过                                                  | 事件与卡片回调                      |

- 跨家庭访问一律返回 404，不泄露对象是否存在；邀请接口对“账号不存在”与“已是成员”返回同一 422，避免枚举账号。
- 每次请求与每个写事务都会重新校验账号状态、会话边界与家庭成员版本，撤权立即生效。

## 5. 资源与端点总览

编号 API-001 至 API-076 与 `operationId` 一一对应；请求与响应字段见契约。

| 编号    | 方法与路径                                              | operationId             | 说明                                   | 权限             | 对应 FR       |
| ------- | ------------------------------------------------------- | ----------------------- | -------------------------------------- | ---------------- | ------------- |
| API-001 | POST /auth/login                                        | login                   | 登录名加密码登录                       | pre_auth         | FR-001        |
| API-002 | POST /auth/challenge                                    | respondChallenge        | 完成强制改密                           | pre_auth         | FR-001        |
| API-003 | POST /auth/refresh                                      | refresh                 | 用 refresh cookie 换新 access token    | pre_auth         | FR-001        |
| API-004 | POST /auth/logout                                       | logout                  | 退出并撤销当前 refresh                 | pre_auth         | FR-001        |
| API-005 | GET /me                                                 | getMe                   | 当前账号、所属家庭、飞书绑定状态       | authenticated    | FR-003        |
| API-006 | PATCH /me/profile                                       | updateMyProfile         | 修改显示名                             | self             | FR-003        |
| API-007 | PATCH /me/login-name                                    | changeLoginName         | 修改登录名                             | self             | FR-003        |
| API-008 | GET /me/login-name/operations/{op_id}                   | getRenameOperation      | 查询改名操作                           | self             | FR-003        |
| API-009 | POST /me/password                                       | changeMyPassword        | 修改密码，使其他会话失效               | self             | FR-003        |
| API-010 | GET /me/actions/{action_id}                             | getMyAction             | 查询账号级动作回执                     | self             | FR-003        |
| API-011 | GET /admin/users                                        | listUsers               | 全站账号列表                           | system_admin     | FR-002        |
| API-012 | POST /admin/users                                       | createUser              | 创建账号，邮件交付或返回一次性临时密码 | system_admin     | FR-002        |
| API-013 | PATCH /admin/users/{uid}                                | updateUserStatus        | 停用或启用账号                         | system_admin     | FR-002        |
| API-014 | POST /admin/users/{uid}/reset-password                  | resetUserPassword       | 重置为临时密码                         | system_admin     | FR-002        |
| API-015 | POST /me/feishu-binding-code                            | createFeishuBindingCode | 生成 10 分钟有效的一次性绑定码         | self             | FR-017        |
| API-016 | PATCH /me/feishu-binding                                | updateFeishuBinding     | 修改飞书默认家庭                       | self             | FR-017        |
| API-017 | DELETE /me/feishu-binding                               | deleteFeishuBinding     | 解除飞书绑定                           | self             | FR-017        |
| API-018 | GET /admin/costs                                        | getCosts                | 应用估算、未知用量、账单同步值分开展示 | system_admin     | FR-018        |
| API-019 | GET /admin/alerts                                       | listAlerts              | 预算告警与交付状态                     | system_admin     | FR-018        |
| API-020 | GET /health                                             | getHealth               | 健康检查与部署版本读回                 | public           | NFR-009       |
| API-021 | GET /families                                           | listFamilies            | 我所属的家庭                           | authenticated    | FR-004        |
| API-022 | POST /families                                          | createFamily            | 创建家庭，生成分类并启用币种           | authenticated    | FR-004        |
| API-023 | GET /families/{fid}                                     | getFamily               | 家庭信息                               | member           | FR-004        |
| API-024 | PATCH /families/{fid}                                   | renameFamily            | 改家庭名                               | family_admin     | FR-004        |
| API-025 | GET /families/{fid}/config                              | getFamilyConfig         | 家庭配置                               | member           | FR-004        |
| API-026 | PATCH /families/{fid}/config                            | updateFamilyConfig      | 改默认币种、辅助币种、方式、时区       | family_admin     | FR-004        |
| API-027 | GET /families/{fid}/members                             | listMembers             | 成员列表（含已离开者显示名）           | member           | FR-005        |
| API-028 | PATCH /families/{fid}/members/{uid}                     | changeMemberRole        | 授予或撤销管理员                       | family_admin     | FR-005        |
| API-029 | DELETE /families/{fid}/members/{uid}                    | removeMember            | 移除成员                               | family_admin     | FR-005        |
| API-030 | GET /families/{fid}/invitations                         | listFamilyInvitations   | 家庭发出的邀请                         | family_admin     | FR-005        |
| API-031 | POST /families/{fid}/invitations                        | inviteMember            | 按登录名邀请（7 天有效）               | family_admin     | FR-005        |
| API-032 | DELETE /families/{fid}/invitations/{iid}                | revokeInvitation        | 撤销邀请                               | family_admin     | FR-005        |
| API-033 | POST /families/{fid}/invitations/{iid}/accept           | acceptInvitation        | 接受邀请                               | self             | FR-005        |
| API-034 | POST /families/{fid}/invitations/{iid}/decline          | declineInvitation       | 拒绝邀请                               | self             | FR-005        |
| API-035 | GET /me/invitations                                     | listMyInvitations       | 我收到的待处理邀请                     | self             | FR-005        |
| API-036 | GET /families/{fid}/dashboard                           | getDashboard            | 月度汇总（与清单、导出同一筛选）       | member           | FR-011        |
| API-037 | GET /families/{fid}/entries                             | listEntries             | 月账目清单（倒序，分页）               | member           | FR-011        |
| API-038 | POST /families/{fid}/entries                            | createEntry             | 创建账目（含退款）                     | member           | FR-006、FR-008|
| API-039 | POST /families/{fid}/entries/preview                    | previewEntry            | 折算预览，不写入                       | member           | FR-006        |
| API-040 | GET /families/{fid}/entries/{eid}                       | getEntry                | 账目详情                               | member           | FR-007        |
| API-041 | PATCH /families/{fid}/entries/{eid}                     | updateEntry             | 修改同一账目                           | owner_or_admin   | FR-007、FR-008|
| API-042 | DELETE /families/{fid}/entries/{eid}                    | deleteEntry             | 移入回收站                             | owner_or_admin   | FR-009        |
| API-043 | POST /families/{fid}/entries/{eid}/restore              | restoreEntry            | 从回收站恢复                           | owner_or_admin   | FR-009        |
| API-044 | GET /families/{fid}/trash                               | listTrash               | 回收站清单                             | member           | FR-009        |
| API-045 | POST /families/{fid}/exports                            | createExport            | 导出 CSV 或 XLSX                       | member           | FR-012        |
| API-046 | GET /families/{fid}/exports/{jid}                       | getExport               | 导出状态与下载链接                     | member           | FR-012        |
| API-047 | GET /families/{fid}/actions/{action_id}                 | getFamilyAction         | 查询本人在该家庭的动作回执             | member           | NFR-003       |
| API-048 | GET /families/{fid}/categories                          | listCategories          | 分类目录（两级树）                     | member           | FR-013        |
| API-049 | POST /families/{fid}/categories                         | createCategory          | 新增分类                               | family_admin     | FR-013        |
| API-050 | PATCH /families/{fid}/categories/{cid}                  | updateCategory          | 改名、移动、排序、停用或启用           | family_admin     | FR-013        |
| API-051 | DELETE /families/{fid}/categories/{cid}                 | deleteCategory          | 删除未使用的分类                       | family_admin     | FR-013        |
| API-052 | POST /families/{fid}/categories/{cid}/merge             | mergeCategory           | 合并到同类别叶子分类                   | family_admin     | FR-013        |
| API-053 | GET /currencies                                         | listGlobalCurrencies    | 全局币种元数据                         | authenticated    | FR-014        |
| API-054 | GET /families/{fid}/currencies                          | listFamilyCurrencies    | 本家庭启用的币种                       | member           | FR-014        |
| API-055 | POST /families/{fid}/currencies                         | enableFamilyCurrency    | 启用币种                               | family_admin     | FR-014        |
| API-056 | POST /admin/currencies                                  | createGlobalCurrency    | 新增全局币种                           | system_admin     | FR-014        |
| API-057 | GET /families/{fid}/rates                               | resolveRates            | 解析某日可用的完整汇率组               | member           | FR-014        |
| API-058 | GET /families/{fid}/rates/manual                        | listManualRates         | 家庭补录的汇率                         | member           | FR-014        |
| API-059 | POST /families/{fid}/rates/manual                       | putManualRate           | 补录或修正某日某币种汇率               | family_admin     | FR-014        |
| API-060 | POST /families/{fid}/rates/recompute                    | createRecomputePreview  | 发起重算预览（异步）                   | family_admin     | FR-014        |
| API-061 | GET /families/{fid}/rates/recompute/{jid}               | getRecompute            | 查询重算预览或进度                     | family_admin     | FR-014        |
| API-062 | POST /families/{fid}/rates/recompute/{jid}/confirm      | confirmRecompute        | 确认预览并分片执行                     | family_admin     | FR-014        |
| API-063 | POST /families/{fid}/attachments/upload-intents         | createUploadIntent      | 创建上传意图，返回限定的直传表单       | member           | FR-010        |
| API-064 | POST /families/{fid}/attachments/{aid}/complete         | completeUpload          | 校验已上传对象并开放关联               | member           | FR-010        |
| API-065 | GET /families/{fid}/attachments/{aid}/download          | getAttachmentUrl        | 签发 60 秒查看链接                     | member           | FR-010        |
| API-066 | POST /families/{fid}/recognition-jobs                   | createRecognitionJob    | 提交文字或文字加一张照片识别           | member           | FR-015        |
| API-067 | GET /families/{fid}/recognition-jobs/{jid}              | getRecognitionJob       | 识别任务状态（仅发起人）               | candidate_owner  | FR-015        |
| API-068 | GET /families/{fid}/batches                             | listMyBatches           | 我的候选批次（含飞书来源）             | member           | FR-016        |
| API-069 | GET /families/{fid}/batches/{bid}                       | getBatch                | 批次及候选                             | candidate_owner  | FR-016        |
| API-070 | POST /families/{fid}/batches/{bid}/cancel               | cancelBatch             | 取消整批                               | candidate_owner  | FR-016        |
| API-071 | PATCH /families/{fid}/batches/{bid}/candidates/{cid}    | updateCandidate         | 修改候选字段，生成新版本               | candidate_owner  | FR-016        |
| API-072 | DELETE /families/{fid}/batches/{bid}/candidates/{cid}   | deleteCandidate         | 删除候选                               | candidate_owner  | FR-016        |
| API-073 | POST /families/{fid}/batches/{bid}/candidates/{cid}/split | splitCandidate        | 拆成 2 至 10 条子候选                  | candidate_owner  | FR-016        |
| API-074 | POST /families/{fid}/batches/{bid}/confirm              | confirmCandidates       | 确认入账（最多 10 条，全部或全无）     | candidate_owner  | FR-016        |
| API-075 | POST /integrations/feishu/events                        | feishuEvents            | 飞书事件订阅回调                       | feishu_signature | FR-017        |
| API-076 | POST /integrations/feishu/cards                         | feishuCardActions       | 飞书卡片交互回调                       | feishu_signature | FR-017        |

## 6. 单接口定义模板

下面给出最能说明契约规则的核心接口；其余接口遵循同样的通用规则（第 3、7、9 节），字段见契约。

### API-001 — 登录

| 项目     | 内容                                                                                          |
| -------- | --------------------------------------------------------------------------------------------- |
| 方法路径 | `POST /v1/auth/login`                                                                         |
| 权限     | `pre_auth`                                                                                    |
| 幂等     | 否                                                                                            |
| 请求     | `{ "login_name": string, "password": string }`                                                |
| 成功响应 | 200：令牌结果（`status`、`access_token`、`expires_in` 秒）；首次登录或重置后返回改密挑战      |
| 错误     | 401 `unauthenticated`（凭证错误，不区分原因）；422 `validation_failed`；429 `rate_limited`   |
| 限流     | 每 15 分钟同一登录名 10 次、同一来源 30 次；失败与成功都计数                                  |

### API-038 — 创建账目

| 项目     | 内容                                                                                                                                  |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| 方法路径 | `POST /v1/families/{fid}/entries`                                                                                                     |
| 权限     | `member`                                                                                                                              |
| 幂等     | 必须带 `Idempotency-Key`                                                                                                              |
| 请求     | `business_date`、`type`、`amount`（必填）；`leaf_category_id`、`currency`、`note`、`payment_method`、`attachment_ids`（最多 3）、`refund_of`（退款时） |
| 成功响应 | 201：完整账目，含 `fx_snapshot`、`nzd_minor`、`cny_minor`、`display_amounts`、`version`                                                |
| 错误     | 403 `forbidden`；409 `version_conflict`、`data_changed`（分类目录已变化）、`idempotency_key_reused`；422 `validation_failed`、`rate_pending`、`category_kind_mismatch`、`category_inactive`、`refund_exceeds_remaining`、`refund_currency_mismatch`、`refund_target_invalid` |
| 副作用   | 同一事务写入账目、定位项、月版本、审计与动作回执                                                                                      |

### API-041 — 修改账目

| 项目     | 内容                                                                                                    |
| -------- | ------------------------------------------------------------------------------------------------------- |
| 方法路径 | `PATCH /v1/families/{fid}/entries/{eid}`                                                                |
| 权限     | `owner_or_admin`                                                                                        |
| 幂等     | 必须带 `Idempotency-Key`；必须带 `expected_version`                                                     |
| 请求     | `expected_version`（必填）与要修改的字段（只提交变化的字段）                                            |
| 成功响应 | 200：更新后的账目                                                                                       |
| 错误     | 404 `not_found`；409 `version_conflict`（`details.current` 为当前内容）；422 `type_change_not_allowed`、`rate_pending` 等 |
| 规则     | 改日期或币种取新汇率快照，其余保留原快照；类型规则见 ADR-0006；已有退款的消费不能让关联失效            |

### API-042 — 删除账目

| 项目     | 内容                                                                                                    |
| -------- | ------------------------------------------------------------------------------------------------------- |
| 方法路径 | `DELETE /v1/families/{fid}/entries/{eid}?expected_version=N[&with_refunds=true]`                        |
| 权限     | `owner_or_admin`                                                                                        |
| 成功响应 | 200：`entry_id`、`state`、`purge_after`、`version`、`also_trashed`（一并删除的退款 ID）                 |
| 错误     | 409 `has_linked_refunds`（有关联退款但未带 `with_refunds=true`）、`version_conflict`                    |

### API-036 — 月度总览

| 项目     | 内容                                                                                                              |
| -------- | ----------------------------------------------------------------------------------------------------------------- |
| 方法路径 | `GET /v1/families/{fid}/dashboard?month=YYYY-MM[&created_by&type&category&payment_method]`                         |
| 权限     | `member`                                                                                                          |
| 成功响应 | 200：`totals`（`income`、`expense_gross`、`refund`、`net_expense`、`balance`，各为 NZD 与 CNY 的金额对）、`by_payment_method`、`category_pie`、`primary`、`secondary`、`data_version`、`computed_at`、`entry_count` |
| 错误     | 409 `data_changing`（计算期间数据变化，重试）                                                                     |
| 规则     | 服务端全月计算，与清单、导出使用同一筛选；缺汇率的账目不计入合计并计数返回，绝不按 0                              |

### API-066 — 提交识别任务

| 项目     | 内容                                                                                          |
| -------- | --------------------------------------------------------------------------------------------- |
| 方法路径 | `POST /v1/families/{fid}/recognition-jobs`                                                    |
| 权限     | `member`                                                                                      |
| 幂等     | 必须带 `Idempotency-Key`                                                                      |
| 请求     | `text`（1 至 300 字）与 `attachment_id`（已登记的照片）至少一项                               |
| 成功响应 | 202：任务（`status` 为 `received`／`queued`）；用 API-067 轮询直到 `candidate_ready` 或 `failed` |
| 错误     | 422 `validation_failed`；429 `rate_limited`                                                   |
| 规则     | 只创建持久任务，不入账；识别结果是候选                                                        |

### API-074 — 确认候选

| 项目     | 内容                                                                                                        |
| -------- | ----------------------------------------------------------------------------------------------------------- |
| 方法路径 | `POST /v1/families/{fid}/batches/{bid}/confirm`                                                             |
| 权限     | `candidate_owner`                                                                                           |
| 幂等     | 必须带 `Idempotency-Key`                                                                                    |
| 请求     | `items`：1 至 10 个 `{ candidate_id, version, ... }`，版本必须是用户看到的版本                              |
| 成功响应 | 200：`action_id`、`status`、`entries`（已入账的账目）                                                       |
| 错误     | 409 `candidate_stale`、`version_conflict`；410 `candidate_expired`；422 `batch_too_large`、`rate_pending`  |
| 规则     | 同一事务全部提交或全部不提交；重复请求返回原结果                                                            |

### API-063 至 API-065 — 照片上传三步

见第 11.3 节。

### API-075 — 飞书事件回调

| 项目     | 内容                                                                                              |
| -------- | ------------------------------------------------------------------------------------------------- |
| 方法路径 | `POST /v1/integrations/feishu/events`                                                             |
| 权限     | `feishu_signature`，不使用令牌与幂等头                                                            |
| 处理     | 验签、时间戳、解密、token、应用与租户白名单；按事件类型分派；事件去重；写入识别任务后立即返回 200 |
| 错误     | 401（校验失败）；未配置飞书时 503 `integration_not_configured`                                    |
| 规则     | 不在请求内调用模型或发送消息；卡片回调（API-076）须在 3 秒内返回新卡片                            |

## 7. 标准错误模型

所有错误使用同一结构：

```json
{ "error": { "code": "version_conflict", "message": "用户可读信息", "request_id": "…", "details": { "current": {}, "fields": [], "retry_after": 0 } } }
```

- `message` 为用户可读信息，不含他人对象内容，受 `X-Ledger-Lang` 控制；`details` 为脱敏细节（字段错误、当前资源、重试秒数）。
- 客户端按 `code` 判断，不解析 `message`。

| HTTP | `code`                                                                                                                                 |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------- |
| 401  | `unauthenticated`、`password_change_required`                                                                                          |
| 403  | `forbidden`、`csrf_failed`                                                                                                             |
| 404  | `not_found`、`action_not_found`                                                                                                        |
| 409  | `version_conflict`、`idempotency_key_reused`、`data_changing`、`data_changed`、`has_linked_refunds`、`category_in_use`、`last_admin`、`login_name_taken`、`rename_pending`、`candidate_stale` |
| 410  | `candidate_expired`、`invitation_expired`、`purged`                                                                                    |
| 415  | `unsupported_media_type`                                                                                                               |
| 422  | `validation_failed`、`rate_pending`、`refund_exceeds_remaining`、`refund_currency_mismatch`、`refund_target_invalid`、`type_change_not_allowed`、`category_kind_mismatch`、`category_inactive`、`category_cycle`、`batch_too_large`、`upload_invalid` |
| 429  | `rate_limited`（`details.retry_after` 为建议等待秒数）                                                                                 |
| 500  | `internal`                                                                                                                             |
| 503  | `upstream_unknown`（结果未知，须按动作 ID 查询）、`integration_not_configured`                                                         |

## 8. 列表查询、过滤、排序与分页

- **分页**：`page_size`（1 至 100，默认 50）与不透明 `cursor`；响应给出下一页游标。
- **排序**：账目按业务日期倒序，同日按创建时间与 ID 稳定排序；回收站按删除时间倒序。
- **过滤**：账目清单、总览与导出共用 `created_by`、`type`、`category`、`payment_method`；月份必填。
- **一致性**：清单与总览读取权威数据（强一致）；总览若在计算期间数据变化返回 `data_changing`，客户端重试。

## 9. 幂等、并发与重试约定

- **幂等键**：所有写操作（POST、PATCH、DELETE，认证入口与飞书回调除外）必须带 `Idempotency-Key`（16 至 64 位字母、数字、下划线、连字符）。同键同指纹返回原结果；同键不同正文返回 409 `idempotency_key_reused`。
- **响应丢失**：先 `GET .../actions/{action_id}` 查询回执，不得换新键重试；`503 upstream_unknown` 同理。
- **乐观锁**：修改、删除、恢复、确认携带 `expected_version`；不匹配返回 409 `version_conflict` 及调用者有权查看的当前版本。
- **重试**：对 `data_changing`、网络错误可用同一键重试；对 4xx 业务错误不要重试。
- **回执保留**：动作回执永久保留（不含正文，ADR-0008）。

## 10. 限流、配额与外部依赖

| 对象           | 限制                                                                   | 超限行为                         |
| -------------- | ---------------------------------------------------------------------- | -------------------------------- |
| 登录           | 每 15 分钟同一登录名 10 次、同一来源 30 次                             | 429 `rate_limited`               |
| 一次 AI 输入   | 文字 1 至 300 字，一张照片，至多 10 条候选                             | 422 `validation_failed` 等       |
| 每个识别任务   | 至多 2 次模型调用                                                      | 任务失败，提示手工录入           |
| 单次确认       | 至多 10 条候选                                                         | 422 `batch_too_large`            |
| 照片           | 每账目至多 3 张；格式限 JPEG、PNG、WebP；单张至多 10 MB；校验像素与摘要 | 415 或 422 `upload_invalid`      |
| 下载链接       | 照片查看 60 秒，导出下载 5 分钟，每次签发前重新检查成员关系            | —                                |

外部依赖（模型、汇率、账单、飞书）的超时与失败不会拖垮同步接口：识别经队列异步，汇率与账单只在后台任务拉取，飞书发送经发件箱。预算超标只告警，不限流（BR-014）。

## 11. Webhook、异步任务与文件上传

### 11.1 Webhook

- 入站：飞书事件（API-075）与卡片交互（API-076）。校验链：签名、时间戳、解密、token、应用与租户白名单；失败 401。
- 去重：事件 ID 去重（保留 30 天）；同一消息的不同事件 ID 只产生一个任务。
- 出站：本系统不向第三方推送 Webhook；飞书消息经发件箱由后台投递。

### 11.2 异步任务

| 任务     | 创建接口 | 状态接口 | 状态流转                                                         | 结果                              |
| -------- | -------- | -------- | ---------------------------------------------------------------- | --------------------------------- |
| 识别     | API-066  | API-067  | `received` → `queued` → `running` → `candidate_ready` 或 `failed` | 候选批次（API-069）               |
| 导出     | API-045  | API-046  | `queued` → `running` → `ready` 或 `failed`                       | 5 分钟有效的下载链接              |
| 汇率重算 | API-060  | API-061  | `preparing` → `preview_ready` → `applying` → `done`              | 经 API-062 确认后分片执行         |

客户端轮询状态接口；识别失败时给出失败类别，由前端显示。

### 11.3 文件上传

1. `POST /families/{fid}/attachments/upload-intents`（API-063）：提交 `content_type`、`bytes`、`sha256`，返回限定键、大小与类型的短期对象存储 POST 表单。
2. 浏览器按表单直传对象存储（文件不经 API）。
3. `POST /families/{fid}/attachments/{aid}/complete`（API-064）：服务端校验真实格式、像素、摘要，通过后附件为 `ready`，才能关联账目或识别。
4. 查看：`GET .../attachments/{aid}/download`（API-065）签发 60 秒有效的链接。

## 12. OpenAPI 事实来源

完整契约在 `contracts/openapi.yaml`，本文不复制。查看方式：

- 路径与方法：`paths`；公共参数：`components.parameters`（幂等键、CSRF、分页、筛选）。
- 数据结构：`components.schemas`（`Entry`、`Dashboard`、`Candidate`、`Error` 等）。
- 权限与验收：每个操作的 `x-authz`、`x-idempotent`、`x-versioned`、`x-acceptance`。
- AI 识别模型的唯一合法输出格式在 `contracts/model.schema.json`。

## 13. 测试与契约验证

- **契约校验**：`scripts/check_contracts.py` 校验契约格式，并检查验收映射覆盖全部验收 ID 与全部 `operationId`。
- **运行时校验**：后端按契约校验路径、参数与请求体（构建时转为 JSON）；前端类型由契约生成，类型不符编译失败。
- **HTTP 测试**：`tests/http/` 覆盖认证、账目、文件、飞书回调；集成测试在 DynamoDB Local 上运行事务与故障场景。
- **浏览器旅程**：Playwright 在桌面与手机视口跑完整用户流程。
- **兼容性**：新增字段与接口为向后兼容；破坏性变更须经契约差异审查。
- 测试编号 TC-xxx 未单独建立，验收条件与测试的对应见 `verification/验收映射.md`。

## 14. 变更与兼容管理

- 契约先改、再改实现与前端；`scripts/check.sh` 一次运行契约、静态检查、测试与构建。
- 新增可选字段与新接口不升版本；删除或改变语义须新路径前缀并保留旧版本。
- 弃用接口先在契约中标注并在发布记录中说明，保留一个发布周期。
- 每次发布记录提交号与制品摘要，`/v1/health` 读回一致后才算发布完成。

## 15. API 检查清单

- [x] 每个接口有唯一编号、权限、幂等与错误说明（核心接口详见第 6 节，其余见契约）
- [x] 授权从可信身份与成员关系推导
- [x] 错误结构统一，`code` 稳定且不随语言变化
- [x] 写接口有幂等键与版本冲突处理
- [x] 列表有分页与稳定排序
- [x] 文件上传不经 API，校验与过期规则明确
- [x] 异步任务有状态接口与失败处理
- [x] 契约与测试、前端类型保持一致

## 16. 变更记录

| 版本 | 日期       | 变更                                                         | 作者             |
| ---- | ---------- | ------------------------------------------------------------ | ---------------- |
| v1.0 | 2026-10-12 | 按标准模板整理：编号接口索引、核心接口说明、通用协议与错误表 | 项目所有者与助手 |
