# ADR-0009 DynamoDB 单表设计

日期：2026-10-04　状态：已采纳

## 决定

业务权威数据使用一张 on-demand 表 `ledger-main`，另设受限表 `ledger-deletion-journal` 保存删除标记（独立 IAM 与备份策略）。完整访问模式、键设计与事务计划见 [docs/storage-design.md](../storage-design.md)。

## 要点

- 家庭数据全部位于 `PK=FAMILY#<fid>` 分区；账号、登录名、绑定、全局汇率、费用位于独立命名空间。
- 账目排序项 `ENTRY#<YYYY-MM>#<date>#<created_at>#<eid>` 为权威内容，`ENTRYLOC#<eid>` 为定位指针；回收站项单独前缀 `TRASH#`，Dashboard 查询无需过滤已删除。
- 唯一 GSI `GSI1`（稀疏）用于到期工作：outbox 待发送、回收站清理、候选过期、孤立照片清理。GSI 不作提交或去重依据。
- 本地开发用 DynamoDB Local 运行同一 repository 实现；SQLite 不使用。
