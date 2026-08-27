import { test, expect } from "@playwright/test";

/**
 * Visual-regression snapshots of the design system. First run creates baselines
 * (`npm run test:vr:update`); later runs fail on any unintended pixel change.
 * Targets the fixtures-only `/design` surface so snapshots are deterministic.
 *
 * Fonts/data are stable; Playwright freezes animations and hides the caret (see
 * playwright.config.ts) so only genuine visual changes diff.
 */

// Wait for fonts + lazy chunk to settle before snapshotting.
async function settle(page: import("@playwright/test").Page) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => (document as unknown as { fonts: { ready: Promise<unknown> } }).fonts.ready);
}

// WO-Y — the one snapshot with a measured, named allowance above the global
// budget. Its `<input type="date">` is a NATIVE control: Chromium paints the
// calendar-picker glyph itself, and it lands a sub-pixel differently between
// runs for ~222 pixels while the value it renders (07/15/2026, a literal in
// the fixture) is identical. That is browser chrome, not our design system.
//
// The allowance is on THIS snapshot only, and it is a count rather than a
// ratio, so it cannot quietly grow with the page. Widening it is a decision
// someone has to make here, in the open — which is what the global 2% ratio it
// replaced was not.
const GALLERY_NATIVE_CONTROL_GLYPH = 400;

test("gallery — full page", async ({ page }) => {
  await page.goto("/design/gallery");
  await settle(page);
  await expect(page).toHaveScreenshot("gallery-full.png", {
    fullPage: true,
    maxDiffPixels: GALLERY_NATIVE_CONTROL_GLYPH,
  });
});

const ROUTES: { path: string; name: string }[] = [
  { path: "/design", name: "dashboard" },
  { path: "/design/supplier-invoices", name: "supplier-invoices" },
  { path: "/design/customer-invoices", name: "customer-invoices" },
  { path: "/design/expenses", name: "expenses" },
  { path: "/design/payments", name: "payments" },
  { path: "/design/reports", name: "reports" },
  { path: "/design/contacts", name: "contacts" },
  { path: "/design/settings", name: "settings" },
  { path: "/design/administration", name: "administration" },
];

for (const route of ROUTES) {
  test(`route — ${route.name}`, async ({ page }) => {
    await page.goto(route.path);
    await settle(page);
    await expect(page).toHaveScreenshot(`route-${route.name}.png`, { fullPage: true });
  });
}

test("overlay — modal open", async ({ page }) => {
  await page.goto("/design/gallery");
  await settle(page);
  await page.getByRole("button", { name: "Open modal" }).click();
  await expect(page.getByRole("dialog", { name: "Example modal" })).toBeVisible();
  await expect(page).toHaveScreenshot("overlay-modal.png");
});

test("overlay — drawer open", async ({ page }) => {
  await page.goto("/design/gallery");
  await settle(page);
  await page.getByRole("button", { name: "Open drawer" }).click();
  await expect(page.getByRole("dialog", { name: "Example drawer" })).toBeVisible();
  await expect(page).toHaveScreenshot("overlay-drawer.png");
});

test("mobile — supplier invoices at 390px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/design/supplier-invoices");
  await settle(page);
  await expect(page).toHaveScreenshot("mobile-supplier-invoices.png", { fullPage: true });
});
