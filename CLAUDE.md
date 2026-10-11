# 小家账本（family-ledger）

## 项目登记
- 本项目 slug：`family-ledger`，已登记到项目总览。登记文件 `project.json` 含站点域名，只保存在本机（已加入 `.git/info/exclude`，不入库）；换机器时按 register-project skill 重建。
- 所有 AWS 资源必须带 `Project=family-ledger` 标签；调用 Bedrock 必须经带该标签的 Application Inference Profile（见 `infra/lib/core-stack.ts`）。
- 改动 `project.json` 或项目文档（PRD／架构／数据模型／API）后重新运行登记脚本（幂等）；文档只在本项目里维护，总览只读。

## 项目文档
- PRD / 架构 / 数据模型 / API 四份文档在 `docs/`（`01-PRD.md`、`03-SYSTEM-ARCHITECTURE.md`、`04-DATA-MODEL.md`、`05-API-SPECIFICATION.md`），以本仓库为唯一事实来源；项目总览只读。
- 改动需求、架构、表结构或接口时，同一次改动里更新对应文档；接口字段以 `contracts/openapi.yaml` 为准。
- 文档改完后运行登记脚本同步到总览（幂等）；自检：`python3 ~/.claude/skills/project-docs/scripts/check_docs.py .`。
- 文档与公开仓库一并发布：不要写入账户 ID、域名、邮箱、私人称谓（提交前扫描见 `docs/部署.md`）。
