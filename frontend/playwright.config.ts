import { defineConfig, devices } from "@playwright/test";

// 端到端：本地 API（LEDGER_ENV=local，DynamoDB Local 独立表）＋ Vite 开发服务器。证据层级：离线。
const env = {
  LEDGER_ENV: "local",
  DYNAMODB_ENDPOINT: "http://localhost:8000",
  LEDGER_TABLE: "ledger-e2e",
  LEDGER_DELETION_TABLE: "ledger-e2e-journal",
  LEDGER_ALLOWED_ORIGINS: "http://localhost:5174",
  LEDGER_LOCAL_DATA_DIR: ".local-data/e2e",
  PORT: "8788",
};

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: { baseURL: "http://localhost:5174", channel: "chrome", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { ...devices["iPhone 13"], defaultBrowserType: "chromium", channel: "chrome" } },
  ],
  webServer: [
    {
      command: "cd .. && uv run python -m ledger.local.server",
      url: "http://localhost:8788/v1/health",
      env,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "pnpm exec vite --port 5174 --strictPort",
      url: "http://localhost:5174",
      env: { VITE_API_PORT: "8788" },
      reuseExistingServer: false,
    },
  ],
});
