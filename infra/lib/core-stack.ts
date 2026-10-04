// 家庭记账本主栈（Sydney）。架构 §1–§8、storage-design、ADR-0007／0009／0012／0013／0014。
import * as fs from "node:fs";
import { CfnOutput, Duration, RemovalPolicy, Stack, type StackProps, Tags } from "aws-cdk-lib";
import { HttpApi, HttpMethod, CorsHttpMethod } from "aws-cdk-lib/aws-apigatewayv2";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import { CfnBudget } from "aws-cdk-lib/aws-budgets";
import type { ICertificate } from "aws-cdk-lib/aws-certificatemanager";
import {
  AllowedMethods, CachePolicy, Distribution, OriginRequestPolicy, ResponseHeadersPolicy, ViewerProtocolPolicy,
} from "aws-cdk-lib/aws-cloudfront";
import { HttpOrigin, S3BucketOrigin } from "aws-cdk-lib/aws-cloudfront-origins";
import { Alarm, ComparisonOperator, type IMetric, TreatMissingData } from "aws-cdk-lib/aws-cloudwatch";
import { SnsAction } from "aws-cdk-lib/aws-cloudwatch-actions";
import { AccountRecovery, UserPool, UserPoolClient } from "aws-cdk-lib/aws-cognito";
import { AttributeType, Billing, StreamViewType, TableV2 } from "aws-cdk-lib/aws-dynamodb";
import { Rule, RuleTargetInput, Schedule } from "aws-cdk-lib/aws-events";
import { LambdaFunction } from "aws-cdk-lib/aws-events-targets";
import { Effect, PolicyStatement, ServicePrincipal } from "aws-cdk-lib/aws-iam";
import { Architecture, Code, Function as Fn, Runtime, StartingPosition } from "aws-cdk-lib/aws-lambda";
import { DynamoEventSource, SqsEventSource } from "aws-cdk-lib/aws-lambda-event-sources";
import { LogGroup, RetentionDays } from "aws-cdk-lib/aws-logs";
import { BlockPublicAccess, Bucket, BucketEncryption, HttpMethods } from "aws-cdk-lib/aws-s3";
import { Topic } from "aws-cdk-lib/aws-sns";
import { EmailSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import { Queue } from "aws-cdk-lib/aws-sqs";
import type { Construct } from "constructs";
import type { LedgerConfig } from "./config";

export interface CoreStackProps extends StackProps {
  config: LedgerConfig;
  certificate?: ICertificate;
}

export class CoreStack extends Stack {
  constructor(scope: Construct, id: string, props: CoreStackProps) {
    super(scope, id, props);
    const cfg = props.config;
    Tags.of(this).add("Project", "family-ledger");
    Tags.of(this).add("Environment", cfg.envName);
    const manifest = JSON.parse(fs.readFileSync(cfg.artifactManifest, "utf-8")) as { sha256: string; commit: string };

    // ── 数据 ──
    const table = new TableV2(this, "Main", {
      partitionKey: { name: "PK", type: AttributeType.STRING },
      sortKey: { name: "SK", type: AttributeType.STRING },
      billing: Billing.onDemand(),
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true, recoveryPeriodInDays: 35 },
      timeToLiveAttribute: "ttl",
      dynamoStream: StreamViewType.NEW_IMAGE,
      deletionProtection: true,
      removalPolicy: RemovalPolicy.RETAIN,
      globalSecondaryIndexes: [{
        indexName: "GSI1",
        partitionKey: { name: "GSI1PK", type: AttributeType.STRING },
        sortKey: { name: "GSI1SK", type: AttributeType.STRING },
        projectionType: undefined,
      }],
    });
    const journal = new TableV2(this, "DeletionJournal", {
      partitionKey: { name: "PK", type: AttributeType.STRING },
      sortKey: { name: "SK", type: AttributeType.STRING },
      billing: Billing.onDemand(),
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true, recoveryPeriodInDays: 35 },
      deletionProtection: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const siteOrigin = cfg.siteDomain ? `https://${cfg.siteDomain}` : "https://example.invalid";
    const privateBucket = (id: string, extra: Partial<ConstructorParameters<typeof Bucket>[2]> = {}) =>
      new Bucket(this, id, {
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
        encryption: BucketEncryption.S3_MANAGED,
        enforceSSL: true,
        removalPolicy: RemovalPolicy.RETAIN,
        ...extra,
      });
    const photos = privateBucket("Photos", {
      versioned: true, // 架构 §8：防覆盖误删；删除日志清理所有版本
      lifecycleRules: [{ noncurrentVersionExpiration: Duration.days(35), abortIncompleteMultipartUploadAfter: Duration.days(1) }],
      cors: [{ allowedMethods: [HttpMethods.POST], allowedOrigins: [siteOrigin], allowedHeaders: ["*"], maxAge: 600 }],
    });
    const exportsBucket = privateBucket("Exports", { lifecycleRules: [{ expiration: Duration.days(7) }] });
    const site = privateBucket("Site");

    // ── 身份 ──
    const pool = new UserPool(this, "Users", {
      selfSignUpEnabled: false,
      signInCaseSensitive: false,
      passwordPolicy: { minLength: 10, requireLowercase: true, requireDigits: true, requireUppercase: false, requireSymbols: false, tempPasswordValidity: Duration.days(7) },
      accountRecovery: AccountRecovery.NONE, // 无邮箱自助找回，由系统管理员重置
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const client = new UserPoolClient(this, "WebClient", {
      userPool: pool,
      generateSecret: false,
      authFlows: { userPassword: true },
      accessTokenValidity: Duration.minutes(15),
      idTokenValidity: Duration.minutes(15),
      refreshTokenValidity: Duration.days(30),
      enableTokenRevocation: true,
      preventUserExistenceErrors: true,
    });

    // ── 秘密（值由人工在控制台写入，不进入代码或 CI 输出） ──
    // 秘密存于 SSM Parameter Store 的 SecureString（标准层免费；AWS 托管密钥 aws/ssm 加密）。
    // CloudFormation 不能创建 SecureString，由运维命令写入（docs/部署.md）；代码只接受 SecureString。
    const paramPrefix = `/family-ledger/${cfg.envName}`;
    const sessionParam = `${paramPrefix}/session-secret`;
    const feishuParam = `${paramPrefix}/feishu`;
    const readParams = new PolicyStatement({
      actions: ["ssm:GetParameter"],
      resources: [sessionParam, feishuParam].map((n) => `arn:aws:ssm:${this.region}:${this.account}:parameter${n}`),
    });

    // ── 队列 ──
    const dlq = new Queue(this, "RecognitionDlq", { retentionPeriod: Duration.days(14), enforceSSL: true });
    const queue = new Queue(this, "Recognition", {
      visibilityTimeout: Duration.seconds(180),
      deadLetterQueue: { queue: dlq, maxReceiveCount: 3 }, // 架构 §6：poison 消息 3 次后进入 DLQ
      enforceSSL: true,
    });

    // ── 函数 ──
    const code = Code.fromAsset(cfg.artifactZip);
    const env: Record<string, string> = {
      LEDGER_ENV: cfg.envName,
      LEDGER_TABLE: table.tableName,
      LEDGER_DELETION_TABLE: journal.tableName,
      LEDGER_RESOURCE_DIR: "/var/task/resources",
      COGNITO_USER_POOL_ID: pool.userPoolId,
      COGNITO_CLIENT_ID: client.userPoolClientId,
      COGNITO_ISSUER: `https://cognito-idp.${this.region}.amazonaws.com/${pool.userPoolId}`,
      LEDGER_SESSION_SECRET_PARAM: sessionParam,
      LEDGER_FEISHU_SECRET_PARAM: feishuParam,
      LEDGER_BLOB_BUCKET: photos.bucketName,
      LEDGER_EXPORT_BUCKET: exportsBucket.bucketName,
      LEDGER_ALLOWED_ORIGINS: siteOrigin,
      LEDGER_MODEL: cfg.modelId,
      LEDGER_RECOGNITION_QUEUE_URL: queue.queueUrl,
      LEDGER_VERSION: manifest.commit.slice(0, 12),
      LEDGER_COMMIT: manifest.commit,
      LEDGER_ARTIFACT_DIGEST: manifest.sha256,
    };
    const fn = (id: string, handler: string, timeout: number, memory = 512) =>
      new Fn(this, id, {
        runtime: Runtime.PYTHON_3_12,
        architecture: Architecture.ARM_64,
        code,
        handler,
        timeout: Duration.seconds(timeout),
        memorySize: memory,
        environment: env,
        logGroup: new LogGroup(this, `${id}Logs`, {
          retention: RetentionDays.TWO_WEEKS, // 架构 §8：运行日志 14 天
          removalPolicy: RemovalPolicy.DESTROY,
        }),
      });
    // 网站首屏并发 5～7 个请求：1769 MB（1 个完整 vCPU）缩短冷启动（2026-10-05 实测 512 MB 冷启动 5～6.5 秒）
    const api = fn("Api", "ledger.handlers.api.handler", 20, 1769);
    const authAdmin = fn("AuthAdmin", "ledger.handlers.api.handler", 20, 1769);
    // 卡片回调须 3 秒内响应：1769 MB＝1 个完整 vCPU，缩短冷启动（2026-10-04 实测 512 MB 冷启动约 4.7 秒）
    const feishuFn = fn("FeishuCallback", "ledger.handlers.api.handler", 10, 1769);
    const worker = fn("Worker", "ledger.handlers.worker.worker_handler", 120, 1024);
    const relay = fn("Relay", "ledger.handlers.worker.relay_handler", 60);
    const maintenance = fn("Maintenance", "ledger.handlers.maintenance.handler", 300);

    for (const f of [api, authAdmin, feishuFn, worker, relay, maintenance]) {
      table.grantReadWriteData(f);
      f.addToRolePolicy(readParams); // 只能读这两个参数（会话密钥、飞书参数）
    }
    journal.grantReadWriteData(maintenance);
    journal.grantReadWriteData(api); // 清理在维护任务；保留写权限供删除事务跨表写入
    for (const f of [api, authAdmin, worker, maintenance]) photos.grantReadWrite(f);
    photos.grantDelete(maintenance);
    exportsBucket.grantReadWrite(api);
    queue.grantSendMessages(relay);
    // Cognito 管理接口只给认证与系统管理函数（ADR-0014）
    authAdmin.addToRolePolicy(new PolicyStatement({
      effect: Effect.ALLOW,
      actions: ["cognito-idp:AdminCreateUser", "cognito-idp:AdminSetUserPassword", "cognito-idp:AdminUserGlobalSignOut",
        "cognito-idp:AdminDisableUser", "cognito-idp:AdminEnableUser", "cognito-idp:AdminGetUser"],
      resources: [pool.userPoolArn],
    }));
    // Bedrock 只给识别 Worker，并限定模型
    worker.addToRolePolicy(new PolicyStatement({
      effect: Effect.ALLOW,
      actions: ["bedrock:InvokeModel"],
      resources: [`arn:aws:bedrock:${cfg.modelRegion}::foundation-model/${cfg.modelId}`],
    }));

    worker.addEventSource(new SqsEventSource(queue, { batchSize: 1, reportBatchItemFailures: true }));
    relay.addEventSource(new DynamoEventSource(table, { startingPosition: StartingPosition.LATEST, batchSize: 50, retryAttempts: 3 }));
    // 白天每 5 分钟预热：Api 同时 4 个实例（覆盖首屏并发），AuthAdmin 2 个（登录与刷新），飞书回调 1 个。
    // 只在 UTC 18:00–10:59（新西兰夏令时 07:00–23:59，冬令时 06:00–22:59）；夜间不预热，偶发冷启动约 2 秒。
    // hold 0.3 秒足以让同一轮的预热落在不同实例（2026-10-05 按费用调整，原为全天＋0.8 秒）。
    const warmSchedule = () => Schedule.expression("cron(0/5 18-23,0-10 * * ? *)");
    const warm = (hold: number) => RuleTargetInput.fromObject({ warmup: true, hold_ms: hold });
    // 每条规则最多 5 个目标：Api 与 AuthAdmin 分开
    new Rule(this, "ApiWarmup", {
      schedule: warmSchedule(),
      targets: [1, 2, 3, 4].map(() => new LambdaFunction(api, { event: warm(300) })),
    });
    new Rule(this, "AuthWarmup", {
      schedule: warmSchedule(),
      targets: [1, 2].map(() => new LambdaFunction(authAdmin, { event: warm(300) })),
    });
    new Rule(this, "FeishuWarmup", {
      schedule: warmSchedule(),
      targets: [new LambdaFunction(feishuFn, { event: RuleTargetInput.fromObject({ warmup: true }) })],
    });
    new Rule(this, "OutboxSweep", { schedule: Schedule.rate(Duration.minutes(5)), targets: [new LambdaFunction(relay)] });
    new Rule(this, "DailyMaintenance", {
      schedule: Schedule.cron({ minute: "17", hour: "14" }), // 奥克兰凌晨
      targets: [new LambdaFunction(maintenance, { event: RuleTargetInput.fromObject({ tasks: ["trash", "orphans", "batches", "budget"] }) })],
    });
    new Rule(this, "DeliverySweep", {
      schedule: Schedule.rate(Duration.minutes(5)),
      targets: [new LambdaFunction(maintenance, { event: RuleTargetInput.fromObject({ tasks: ["outbox"] }) })],
    });

    // ── HTTP API（经 CloudFront 同源 /v1 访问，ADR-0014） ──
    const http = new HttpApi(this, "Http", { corsPreflight: { allowMethods: [CorsHttpMethod.ANY], allowOrigins: [siteOrigin] } });
    const route = (path: string, f: Fn) => http.addRoutes({ path, methods: [HttpMethod.ANY], integration: new HttpLambdaIntegration(`${f.node.id}${path.replace(/\W/g, "")}`, f) });
    route("/v1/{proxy+}", api);
    route("/v1/auth/{proxy+}", authAdmin);
    route("/v1/admin/{proxy+}", authAdmin);
    route("/v1/me/password", authAdmin);
    route("/v1/integrations/feishu/{proxy+}", feishuFn);

    // ── 网站分发 ──
    const apiDomain = `${http.apiId}.execute-api.${this.region}.amazonaws.com`;
    const dist = new Distribution(this, "Web", {
      defaultBehavior: {
        origin: S3BucketOrigin.withOriginAccessControl(site),
        viewerProtocolPolicy: ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        responseHeadersPolicy: ResponseHeadersPolicy.SECURITY_HEADERS,
      },
      additionalBehaviors: {
        "/v1/*": {
          origin: new HttpOrigin(apiDomain),
          viewerProtocolPolicy: ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: AllowedMethods.ALLOW_ALL,
          cachePolicy: CachePolicy.CACHING_DISABLED, // API 与凭证响应不缓存
          originRequestPolicy: OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
      },
      defaultRootObject: "index.html",
      errorResponses: [{ httpStatus: 404, responseHttpStatus: 200, responsePagePath: "/index.html" }],
      domainNames: cfg.siteDomain && props.certificate ? [cfg.siteDomain] : undefined,
      certificate: props.certificate,
    });

    // ── 告警与预算（超额只通知，不停服） ──
    const alerts = new Topic(this, "Alerts");
    if (cfg.alertEmail) alerts.addSubscription(new EmailSubscription(cfg.alertEmail));
    const alarm = (id: string, metric: IMetric, threshold: number) =>
      new Alarm(this, id, { metric, threshold, evaluationPeriods: 1, comparisonOperator: ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD, treatMissingData: TreatMissingData.NOT_BREACHING }).addAlarmAction(new SnsAction(alerts));
    alarm("DlqNotEmpty", dlq.metricApproximateNumberOfMessagesVisible(), 1);
    alarm("QueueOldest", queue.metricApproximateAgeOfOldestMessage(), 600);
    alarm("Api5xx", http.metricServerError({ period: Duration.minutes(5) }), 5);
    alarm("WorkerErrors", worker.metricErrors({ period: Duration.minutes(15) }), 3);
    new CfnBudget(this, "MonthlyBudget", {
      budget: {
        budgetName: `family-ledger-${cfg.envName}`,
        budgetType: "COST",
        timeUnit: "MONTHLY",
        budgetLimit: { amount: Number(cfg.budgetUsd), unit: "USD" },
        costFilters: { TagKeyValue: ["user:Project$family-ledger"] },
      },
      notificationsWithSubscribers: [80, 100].map((t) => ({
        notification: { notificationType: "ACTUAL", comparisonOperator: "GREATER_THAN", threshold: t, thresholdType: "PERCENTAGE" },
        subscribers: [{ subscriptionType: "SNS", address: alerts.topicArn }],
      })),
    });
    alerts.addToResourcePolicy(new PolicyStatement({
      actions: ["sns:Publish"], resources: [alerts.topicArn],
      principals: [new ServicePrincipal("budgets.amazonaws.com")],
    }));

    new CfnOutput(this, "SiteBucket", { value: site.bucketName });
    new CfnOutput(this, "DistributionDomain", { value: dist.distributionDomainName });
    new CfnOutput(this, "UserPoolId", { value: pool.userPoolId });
    // 首发初始化（python -m ledger.ops.initialize）使用
    new CfnOutput(this, "TableName", { value: table.tableName });
    new CfnOutput(this, "JournalTableName", { value: journal.tableName });
    new CfnOutput(this, "ArtifactDigest", { value: manifest.sha256 });
  }
}
