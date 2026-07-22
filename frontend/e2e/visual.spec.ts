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

test("gallery — full page", async ({ page }) => {
  await page.goto("/design/gallery");
  await settle(page);
  await expect(page).toHaveScreenshot("gallery-full.png", { fullPage: true });
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
