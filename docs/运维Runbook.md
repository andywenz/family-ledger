# 运维 Runbook

## A. 用户报告“不知道有没有保存成功”

1. 网站会提示“查询结果”：前端按同一动作 ID 调 `GET /v1/families/{fid}/actions/{id}`。
2. 200 → 已提交，返回结果对象；404 → 未提交，可用**同一** Idempotency-Key 重试；超时或 5xx → 仍未知，稍后再查，**不要换新 ID 重做**。
3. 管理员核对：DynamoDB 查询 `PK=FAMILY#<fid>`、`SK=ACTION#<uid>#<key>`。

## B. 识别任务卡住或 DLQ 告警

1. CloudWatch 告警 `DlqNotEmpty`／`QueueOldest`。
2. 查看 DLQ 消息体（只有 family_id 与 job_id，无账目正文），查询 `JOB#<jid>` 的 `status`、`attempt`、`model_calls`、`error_class`。
3. 修复原因后 redrive 到主队列：Worker 以 Job 状态去重；`model_calls` 已达 2 的任务不会再调用模型，会标记失败并通知用户。
4. 不要清空 DLQ 而不核对。

## C. 飞书卡片没收到

1. 查 `OUTBOX#…` 项：`delivery`＝pending／sent（附 message_id）／unknown／failed，`attempts`。
2. unknown：可能已送达；1 小时内同 uuid 重试安全（平台去重）。超过窗口不自动重发，联系用户确认后由用户在网站处理候选。
3. 交付状态从不影响入账；用户也可在网站「AI 帮我记」中处理同一批次。

## D. 回滚

1. 选择上一个通过 CI 的 `release-<commit>` 制品，运行 deploy 工作流（门禁同样适用）。
2. 代码回滚不回滚数据：撤权、删除、已入账结果保持。若新版本写入了旧版本不认识的字段，先评估兼容性；不兼容时做前向修复而非回滚。

## E. 恢复演练（候选目标 RPO≤24h、RTO≤24h，未实测）

1. ☁️ PITR 恢复主表到新表 `ledger-main-restore-<日期>`（不覆盖生产）。
2. 先重放删除日志表：对每条 `PURGED#` 标记，删除恢复表中对应账目与照片引用。
3. 用恢复表启动隔离环境（独立 Lambda 环境变量），核对账目数量、照片 version 可读、当前成员关系与撤权。
4. Cognito 密码不能从备份恢复：如需重建用户池，按 `USERS` 目录重建用户并重置临时密码。
5. 记录实际耗时与数据时间点，作为 RTO／RPO 证据（OPS-10）。

## F. 费用告警

1. 应用估算（飞书发给已绑定的系统管理员）与 AWS Budgets（SNS 邮件）分别到达；管理页 `/admin/costs` 显示估算、未知用量与账单同步值。
2. 超额**不暂停服务**（用户决定）。先查看 `COST#<月份>` 的 USAGE 记录与模型调用量，判断是否异常调用；必要时调整模型或限流参数。

## G. 照片与删除

- 回收站 30 天后由每日维护清理；照片只在无任何引用时删除全部对象版本。
- 恢复后若发现已删除数据复活，按删除日志补删。
