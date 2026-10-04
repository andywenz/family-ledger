// CDK 断言：关键安全与恢复配置（静态证据；不代表真实部署结果）。
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";
import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { loadConfig } from "../lib/config";
import { CoreStack } from "../lib/core-stack";

function synth(): Template {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ledger-cdk-"));
  fs.writeFileSync(path.join(dir, "lambda.zip"), "PK\x05\x06" + "\0".repeat(18));
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ sha256: "a".repeat(64), commit: "c".repeat(40) }));
  const app = new App({ context: { account: "111111111111", artifactZip: path.join(dir, "lambda.zip"), artifactManifest: path.join(dir, "manifest.json") } });
  const cfg = loadConfig(app);
  return Template.fromStack(new CoreStack(app, "T", { env: { account: "111111111111", region: "ap-southeast-2" }, config: cfg }));
}

const t = synth();
const roles = () => Object.values(t.findResources("AWS::IAM::Policy")) as { Properties: { PolicyDocument: { Statement: { Action: string | string[] }[] }; Roles: { Ref: string }[] } }[];
const policiesWith = (action: string) => roles().filter((p) => p.Properties.PolicyDocument.Statement.some((s) => ([] as string[]).concat(s.Action).some((a) => a.startsWith(action))));

test("业务表与删除日志：PITR 35 天、删除保护、保留策略", () => {
  t.resourcePropertiesCountIs("AWS::DynamoDB::GlobalTable", {
    Replicas: Match.arrayWith([Match.objectLike({
      DeletionProtectionEnabled: true,
      PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true, RecoveryPeriodInDays: 35 },
    })]),
  }, 2);
  t.hasResource("AWS::DynamoDB::GlobalTable", { DeletionPolicy: "Retain" });
  t.hasResourceProperties("AWS::DynamoDB::GlobalTable", { StreamSpecification: { StreamViewType: "NEW_IMAGE" }, TimeToLiveSpecification: { AttributeName: "ttl", Enabled: true } });
});

test("所有存储桶屏蔽公开访问并加密；照片桶版本化", () => {
  const buckets = t.findResources("AWS::S3::Bucket");
  assert.equal(Object.keys(buckets).length, 3);
  for (const b of Object.values(buckets) as { Properties: Record<string, unknown> }[]) {
    assert.deepEqual(b.Properties.PublicAccessBlockConfiguration, { BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true });
    assert.ok(b.Properties.BucketEncryption);
  }
  t.hasResourceProperties("AWS::S3::Bucket", { VersioningConfiguration: { Status: "Enabled" } });
});

test("身份：关闭公开注册、密码至少 10 位、无自助找回", () => {
  t.hasResourceProperties("AWS::Cognito::UserPool", {
    AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
    Policies: { PasswordPolicy: Match.objectLike({ MinimumLength: 10 }) },
  });
  t.hasResourceProperties("AWS::Cognito::UserPoolClient", { GenerateSecret: false, ExplicitAuthFlows: Match.arrayWith(["ALLOW_USER_PASSWORD_AUTH"]), EnableTokenRevocation: true });
});

test("最小权限：只有 Worker 能调 Bedrock；只有认证管理函数有 Cognito 管理权限", () => {
  const bedrock = policiesWith("bedrock:");
  assert.equal(bedrock.length, 1);
  assert.match(bedrock[0]!.Properties.Roles[0]!.Ref, /^Worker/);
  const cognito = policiesWith("cognito-idp:Admin");
  assert.equal(cognito.length, 1);
  assert.match(cognito[0]!.Properties.Roles[0]!.Ref, /^AuthAdmin/);
});

test("Lambda：Python 3.12 arm64、生产模式、资源目录、绑定制品摘要", () => {
  t.allResourcesProperties("AWS::Lambda::Function", {
    Runtime: "python3.12",
    Architectures: ["arm64"],
    Environment: { Variables: Match.objectLike({ LEDGER_ENV: "prod", LEDGER_RESOURCE_DIR: "/var/task/resources", LEDGER_ARTIFACT_DIGEST: "a".repeat(64) }) },
  });
});

test("队列：3 次投递后进入 DLQ；DLQ 有告警", () => {
  t.hasResourceProperties("AWS::SQS::Queue", { RedrivePolicy: Match.objectLike({ maxReceiveCount: 3 }) });
  t.hasResourceProperties("AWS::CloudWatch::Alarm", { MetricName: "ApproximateNumberOfMessagesVisible", Threshold: 1 });
});

test("CloudFront：/v1/* 不缓存并转发到 HTTP API；站点经 OAC 访问", () => {
  t.hasResourceProperties("AWS::CloudFront::Distribution", {
    DistributionConfig: Match.objectLike({
      CacheBehaviors: Match.arrayWith([Match.objectLike({ PathPattern: "/v1/*", CachePolicyId: "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" })]),
    }),
  });
  t.resourceCountIs("AWS::CloudFront::OriginAccessControl", 1);
});

test("预算：80%／100% 通知到 SNS，按项目标签过滤", () => {
  t.hasResourceProperties("AWS::Budgets::Budget", {
    Budget: Match.objectLike({ CostFilters: { TagKeyValue: ["user:Project$family-ledger"] } }),
    NotificationsWithSubscribers: [
      Match.objectLike({ Notification: Match.objectLike({ Threshold: 80 }) }),
      Match.objectLike({ Notification: Match.objectLike({ Threshold: 100 }) }),
    ],
  });
});

test("首发初始化所需输出存在；Worker 只能调用选定模型（ADR-0015）", () => {
  t.hasOutput("TableName", {});
  t.hasOutput("JournalTableName", {});
  t.hasOutput("UserPoolId", {});
  const policies = JSON.stringify(t.findResources("AWS::IAM::Policy"));
  if (!policies.includes("foundation-model/amazon.nova-pro-v1:0")) throw new Error("Worker 未限定 Nova Pro ARN");
  if (!policies.includes("RecognitionProfile")) throw new Error("Worker 未授权推理配置文件");
  if (policies.includes("nova-lite")) throw new Error("仍引用 Lite");
});

test("飞书回调：1769 MB 且白天每 5 分钟预热（卡片 3 秒限制，FS-06）", () => {
  const fns = t.findResources("AWS::Lambda::Function");
  const feishu = Object.entries(fns).find(([id]) => id.startsWith("FeishuCallback"));
  if (!feishu || (feishu[1] as any).Properties.MemorySize !== 1769) throw new Error("FeishuCallback 内存不是 1769");
  t.hasResourceProperties("AWS::Events::Rule", {
    ScheduleExpression: "cron(0/5 18-23,0-10 * * ? *)",
    Targets: Match.arrayWith([Match.objectLike({ Input: JSON.stringify({ warmup: true }) })]),
  });
});

test("网站函数：1769 MB，且每 5 分钟同时预热多个实例", () => {
  const fns = t.findResources("AWS::Lambda::Function");
  for (const name of ["Api", "AuthAdmin"]) {
    const f = Object.entries(fns).find(([id]) => id.startsWith(name) && !id.startsWith("AuthAdmin") === (name === "Api"));
    if (!f || (f[1] as any).Properties.MemorySize !== 1769) throw new Error(`${name} 内存不是 1769`);
  }
  const rules = Object.values(t.findResources("AWS::Events::Rule")) as any[];
  const counts = rules.map((r) => (r.Properties.Targets ?? []).length);
  if (!counts.includes(4) || !counts.includes(2)) throw new Error("缺少 Api×4／AuthAdmin×2 预热规则");
});

test("网站预热只在白天且每次只占用 0.3 秒", () => {
  const rules = Object.values(t.findResources("AWS::Events::Rule")) as any[];
  const warmups = rules.filter((r) => JSON.stringify(r.Properties.Targets ?? []).includes("warmup"));
  if (warmups.length !== 3) throw new Error(`预热规则应为 3 条，实际 ${warmups.length}`);
  for (const r of warmups) {
    if (r.Properties.ScheduleExpression !== "cron(0/5 18-23,0-10 * * ? *)") throw new Error("预热应只在白天");
    for (const tgt of r.Properties.Targets) {
      const hold = JSON.parse(tgt.Input).hold_ms;
      if (hold !== undefined && hold > 300) throw new Error("预热 hold 应不超过 300 毫秒");
    }
  }
});

test("秘密：不再使用 Secrets Manager；函数只能读两个 SSM 参数", () => {
  t.resourceCountIs("AWS::SecretsManager::Secret", 0);
  const policies = JSON.stringify(t.findResources("AWS::IAM::Policy"));
  if (!policies.includes("parameter/family-ledger/prod/session-secret")) throw new Error("缺少会话密钥参数读取权限");
  if (!policies.includes("parameter/family-ledger/prod/feishu")) throw new Error("缺少飞书参数读取权限");
  if (policies.includes("ssm:PutParameter") || policies.includes("parameter/*")) throw new Error("SSM 权限过宽");
});

test("AI 费用带项目标签：识别经应用推理配置文件调用", () => {
  t.hasResourceProperties("AWS::Bedrock::ApplicationInferenceProfile", {
    ModelSource: { CopyFrom: "arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.nova-pro-v1:0" },
    Description: Match.stringLikeRegexp("^([0-9a-zA-Z:.][ _-]?)+$"), // CloudFormation 早期校验的格式
    Tags: Match.arrayWith([{ Key: "Project", Value: "family-ledger" }]),
  });
  t.hasResourceProperties("AWS::Lambda::Function", {
    Environment: { Variables: Match.objectLike({ LEDGER_MODEL: "amazon.nova-pro-v1:0", LEDGER_MODEL_INVOKE_ID: Match.anyValue() }) },
  });
});
