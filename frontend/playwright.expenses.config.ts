import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  testDir: "./live",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  use: { ...base.use, trace: "retain-on-failure" },
});
