import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 90_000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:3000",
    launchOptions: { executablePath: process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe" },
    trace: "retain-on-failure",
  },
});
