import { test, expect } from "@playwright/test";

/**
 * WO-N — form labels are ASSOCIATED, not just adjacent. The sign-in fields
 * shipped with visual labels no screen reader could connect to their inputs
 * (the 2026-08-12 release runbook carried them as known-broken). These tests
 * pin the fix the only way that can't regress silently: getByLabel() resolves
 * a control through the real label→control association (htmlFor/id or
 * wrapping), so an unassociated label makes the locator fail. The static
 * sweep lives in scripts/check-labels.mjs; this proves the flagship page's
 * associations survive rendering.
 */

test.beforeEach(async ({ page }) => {
  // Unauthenticated: the auth bootstrap 401s and /login renders for real.
  await page.route("**/api/v1/auth/me", (r) =>
    r.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "unauthenticated" }) }),
  );
  await page.goto("/login");
});

test("sign-in fields are reachable through their labels", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

  // Association proof: filling THROUGH the label reaches the actual control.
  await page.getByLabel("Email").fill("driver@fleet.example");
  await expect(page.getByLabel("Email")).toHaveValue("driver@fleet.example");
  await page.getByLabel("Password").fill("wheel-chock-8");
  await expect(page.getByLabel("Password")).toHaveValue("wheel-chock-8");

  // The SSO workspace input is labelled too.
  await expect(page.getByLabel("Single sign-on")).toBeVisible();
});

test("registration fields are reachable through their labels", async ({ page }) => {
  await page.getByRole("button", { name: "Need an account? Register" }).click();
  await expect(page.getByRole("heading", { name: "Create your workspace" })).toBeVisible();

  await page.getByLabel("Organization").fill("Site Crew OU");
  await expect(page.getByLabel("Organization")).toHaveValue("Site Crew OU");
  await page.getByLabel("Your name").fill("Dispatcher");
  await page.getByLabel("Email").fill("dispatch@fleet.example");
  await page.getByLabel("Password").fill("tarpaulin-99");
  await expect(page.getByLabel("Your name")).toHaveValue("Dispatcher");
});
