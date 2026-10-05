import { defineConfig } from "vitest/config";

// 只收集 src 下的单元测试；e2e/*.spec.ts 由 Playwright 运行
export default defineConfig({
  test: { include: ["src/**/*.test.ts"] },
});
