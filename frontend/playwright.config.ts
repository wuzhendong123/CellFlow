import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.CF_E2E_BASE || "http://127.0.0.1:8765",
    viewport: { width: 1600, height: 1000 },
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM } : {},
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  outputDir: "e2e/.results",
});
