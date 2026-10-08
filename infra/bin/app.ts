// CDK 入口。合成需要先构建制品（scripts/build_lambda.py）。部署须经用户对具体账户与动作授权。
import { App } from "aws-cdk-lib";
import { loadConfig } from "../lib/config";
import { CoreStack } from "../lib/core-stack";
import { EdgeCertStack } from "../lib/edge-cert-stack";

const app = new App();
const cfg = loadConfig(app);
const env = { account: cfg.account, region: cfg.region };
let certificate;
if (cfg.siteDomain && !cfg.siteCertificateArn) {
  const edge = new EdgeCertStack(app, `FamilyLedgerCert-${cfg.envName}`, {
    env: { account: cfg.account, region: "us-east-1" },
    crossRegionReferences: true,
    domain: cfg.siteDomain,
  });
  certificate = edge.certificate;
}
new CoreStack(app, `FamilyLedger-${cfg.envName}`, { env, config: cfg, certificate, crossRegionReferences: !!certificate });
app.synth();
