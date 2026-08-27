import { existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the InvoiceIQ design system — both end-to-end smoke tests
 * (`e2e/smoke.spec.ts`) and visual-regression snapshots (`e2e/visual.spec.ts`),
 * run against the `/design` showcase (fixtures-only, no backend, deterministic).
 *
 * The browser is the environment's pre-installed Chromium (pinned via
 * `executablePath`), so no `playwright install` download is needed. A fixed
 * viewport + device scale + Playwright's built-in animation freezing keep
 * screenshots stable across runs.
 */
// Use the environment's pre-installed Chromium when present (the dev sandbox pins
// it here); otherwise fall back to Playwright's own bundled browser — which is the
// right choice inside the version-matched Playwright CI container. `undefined`
// launchOptions.executablePath means "use the bundled browser".
const PINNED = process.env.PW_CHROMIUM_PATH || "/opt/pw-browsers/chromium";
const CHROMIUM = existsSync(PINNED) ? PINNED : undefined;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  timeout: 30_000,
  expect: {
    // WO-Y — an ABSOLUTE pixel budget, measured rather than guessed.
    //
    // This was `maxDiffPixelRatio: 0.02`, described as "a small tolerance for
    // antialiasing". On a full-page shot of the gallery (1280x3928) two percent
    // is ~100,000 pixels, and a seeded layout regression — one padding step on
    // the dashboard's quick actions — moved 10,681. The gate ran, and passed a
    // change it existed to catch. A ratio is the wrong unit here for exactly
    // that reason: it scales the allowance with page height, so the longest
    // pages (the ones with the most to regress) get the largest licence.
    //
    // Measured noise floor in a fixed environment: 12 of 13 snapshots are
    // byte-identical run to run, and the thirteenth differs by 222 pixels in
    // the native date input's calendar glyph (see the gallery spec). 100 is
    // above that floor for every deterministic snapshot and two orders of
    // magnitude below a real regression.
    toHaveScreenshot: { maxDiffPixels: 100, animations: "disabled", caret: "hide" },
  },
  use: {
    baseURL: "http://localhost:5173",
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 1,
    trace: "on-first-retry",
    launchOptions: { executablePath: CHROMIUM },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 }, launchOptions: { executablePath: CHROMIUM } } },
  ],
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5173",
    url: "http://localhost:5173/design",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
