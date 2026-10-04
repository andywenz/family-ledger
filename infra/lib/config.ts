// 部署参数：来自 CDK context（-c 或 config/private/cdk.context.json），公开仓库只含占位默认值。
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { App } from "aws-cdk-lib";

// 私人部署参数文件（已被 .gitignore 排除）；-c 命令行参数优先
const PRIVATE_CONTEXT = join(__dirname, "..", "..", "config", "private", "cdk.context.json");

function privateContext(): Record<string, string> {
  return existsSync(PRIVATE_CONTEXT) ? JSON.parse(readFileSync(PRIVATE_CONTEXT, "utf-8")) : {};
}

export interface LedgerConfig {
  envName: string; // prod | staging
  account?: string;
  region: string; // 主区域 ap-southeast-2
  siteDomain?: string; // 例如 ledger.example.com（DNS 在 Cloudflare 手工维护）
  alertEmail?: string; // 预算备用通知邮箱（私人配置）
  budgetUsd: string; // NZD 15 按账单币种换算后的金额
  modelId: string;
  modelRegion: string;
  artifactZip: string; // dist/lambda.zip
  artifactManifest: string; // dist/manifest.json
  feishuTenantKey?: string;
}

export function loadConfig(app: App): LedgerConfig {
  const file = privateContext();
  const c = (k: string) => (app.node.tryGetContext(k) as string | undefined) ?? file[k];
  return {
    envName: c("envName") ?? "prod",
    account: c("account"),
    region: c("region") ?? "ap-southeast-2",
    siteDomain: c("siteDomain"),
    alertEmail: c("alertEmail"),
    budgetUsd: c("budgetUsd") ?? "9",
    modelId: c("modelId") ?? "amazon.nova-pro-v1:0", // ADR-0015
    modelRegion: c("modelRegion") ?? "ap-southeast-2",
    artifactZip: c("artifactZip") ?? "../dist/lambda.zip",
    artifactManifest: c("artifactManifest") ?? "../dist/manifest.json",
    feishuTenantKey: c("feishuTenantKey"),
  };
}
