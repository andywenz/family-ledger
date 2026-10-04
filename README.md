# 小家账本（family-ledger）

面向少数私人用户的家庭共享记账网站：手工记账、网站 AI 识别、飞书私聊卡片确认，多币种汇率快照，月度 Dashboard。

**当前状态：** 全部功能可在本地运行并通过离线测试（后端单元／集成／HTTP 契约测试、浏览器旅程测试、CDK 断言测试）。识别模型已用真实 Amazon Bedrock 完成比较并选定 Nova Pro（[ADR-0015](docs/adr/0015-识别模型选型.md)）。飞书真实联调与生产部署的验证状态见 [验收映射](verification/验收映射.md) 与 [发布记录](verification/release.md)；未标为“真实通过”的项不应视为已在生产验证。

| 位置 | 内容 |
|---|---|
| [contracts/openapi.yaml](contracts/openapi.yaml) | 全部 HTTP 接口、Schema、权限（`x-authz`）、幂等与版本要求 |
| [contracts/model.schema.json](contracts/model.schema.json) | AI 识别模型的唯一合法输出格式 |
| [seed/](seed/) | 初始分类模板（六种目录）与币种元数据 |
| [docs/storage-design.md](docs/storage-design.md) | DynamoDB 访问模式、键、一致性与事务计划 |
| [docs/adr/](docs/adr/) | 架构决策记录 |
| [docs/本地开发.md](docs/本地开发.md) | 本地安装、检查与启动 |
| [verification/验收映射.md](verification/验收映射.md) | 需求→用例→页面→接口→验收 ID 追踪与状态 |
| [docs/部署.md](docs/部署.md)、[docs/运维Runbook.md](docs/运维Runbook.md) | 部署、恢复、回滚 |
| [verification/model-eval/](verification/model-eval/) | 真实模型效果与成本比较（合成样本） |

## 技术栈

Python 3.12（uv）· React 19 ＋ Vite ＋ TypeScript · AWS Lambda ＋ HTTP API ＋ DynamoDB ＋ Cognito ＋ S3 ＋ Bedrock ＋ SQS · AWS CDK。

## 隐私

真实账号、域名环境配置、Excel、照片、账目、凭证与生产日志不进入本仓库。私人配置放在 `config/private/`（已忽略）。测试只使用合成数据。
