import { execSync } from "node:child_process";
import { writeFileSync } from "node:fs";

// 建立独立 e2e 表（DynamoDB Local）与测试管理员；凭证只写入 git 忽略的本地目录。
export default function globalSetup() {
  const env = {
    ...process.env,
    LEDGER_ENV: "local",
    DYNAMODB_ENDPOINT: "http://localhost:8000",
    LEDGER_TABLE: "ledger-e2e",
    LEDGER_DELETION_TABLE: "ledger-e2e-journal",
    LEDGER_LOCAL_DATA_DIR: ".local-data/e2e",
  };
  const opts = { cwd: "..", env, encoding: "utf-8" as const };
  execSync("uv run python -m ledger.local.bootstrap --admin e2eowner --name 维护者", opts);
  for (const project of ["desktop", "mobile"]) {
    const out = execSync("uv run python -m ledger.local.e2e_seed", opts).trim().split("\n").pop()!;
    writeFileSync(`../.local-data/e2e/admin-${project}.json`, out);
  }
}
