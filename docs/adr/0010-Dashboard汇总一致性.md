# ADR-0010 Dashboard 汇总与分页一致性

日期：2026-10-04　状态：已采纳

## 决定

- 每个家庭每个月份有计数项 `MONTHVER#<YYYY-MM>`，所有影响该月账目的事务对其 `ADD version 1`（跨月修改同时增加两个月）。
- `GET /dashboard`：读月版本 v1 → 强一致分页读取全月排序项并在服务端计算汇总 → 再读 v2。若 v1≠v2 重试一次；仍不同返回 409 `data_changing`，前端提示刷新。返回 `data_version`。
- `GET /entries` 分页 cursor 内含 `data_version` 与筛选条件签名；翻页时版本变化返回 409 `data_changed`，前端整体刷新清单与汇总，不拼接旧数据。
- 汇总、饼图、方式统计与导出共用同一服务端计算函数与同一筛选参数。
