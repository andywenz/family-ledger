// CloudFront 证书必须位于 us-east-1。DNS 验证记录需在 Cloudflare 手工添加（部署时 CloudFormation 等待验证）。
import { RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import { Certificate, CertificateValidation } from "aws-cdk-lib/aws-certificatemanager";
import type { Construct } from "constructs";

export class EdgeCertStack extends Stack {
  readonly certificate: Certificate;

  constructor(scope: Construct, id: string, props: StackProps & { domain: string }) {
    super(scope, id, props);
    this.certificate = new Certificate(this, "SiteCert", {
      domainName: props.domain,
      validation: CertificateValidation.fromDns(), // 无 Route53：输出 CNAME 由人在 Cloudflare 添加
    });
    this.certificate.applyRemovalPolicy(RemovalPolicy.RETAIN); // 删除本栈时保留证书（可能仍被 CloudFront 使用）
  }
}
