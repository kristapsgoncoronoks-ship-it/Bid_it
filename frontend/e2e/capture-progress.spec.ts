import { test, expect, type Page } from "@playwright/test";

/**
 * WO-X — a long capture must not read as a stuck one.
 *
 * The review screen used to say "Reading the document…" for everything: a
 * one-second CSV and a forty-page scan produced the same sentence, so there was
 * no way to tell a job that was working from one that had died. The poll now
 * carries the parser's phase and, on the only slow phase, the page it has
 * reached.
 *
 * What this pins is that the SCREEN follows the run: three successive polls
 * return three different pages and the sentence changes each time, and the
 * progress bar appears only where the server sends a measured percent.
 *
 * Synthetic fixtures only (master context §10): fictional figures/ids.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};

/** What successive polls return: queued, then page 12 of 40, then page 30. */
const POLLS = [
  { stage: "queued", pages_done: 0, pages_total: null, percent: null },
  { stage: "ocr", pages_done: 12, pages_total: 40, percent: 30 },
  { stage: "ocr", pages_done: 30, pages_total: 40, percent: 75 },
];

async function mockApi(page: Page, polls = POLLS): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  let poll = 0;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));

    if (path === "/invoices/upload/run-1") {
      const state = polls[Math.min(poll, polls.length - 1)];
      poll += 1;
      return route.fulfill(
        json({ extraction_run_id: "run-1", status: "queued", method: null, draft: null, ...state }),
      );
    }
    return route.fulfill(json({}));
  });
}

test("a scan being read reports the page it has reached, and the count advances", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/captures/run-1");

  // Waiting for a worker is a state with a name of its own — not the same
  // sentence as being read.
  await expect(page.getByText("Waiting for a free reader…")).toBeVisible();
  // No measurement yet, so no bar: an indeterminate wait is shown as one.
  await expect(page.getByRole("progressbar")).toBeHidden();

  // The parser reaches page 12 of 40 — a sentence a person can act on.
  await expect(page.getByText("Recognising page 13 of 40…")).toBeVisible();
  const bar = page.getByRole("progressbar", { name: "Pages read" });
  await expect(bar).toHaveAttribute("aria-valuenow", "30");

  // …and it keeps moving, which is the whole point.
  await expect(page.getByText("Recognising page 31 of 40…")).toBeVisible();
  await expect(bar).toHaveAttribute("aria-valuenow", "75");
});

test("a phase nothing measured shows its name and no invented number", async ({ page }) => {
  await mockApi(page, [
    { stage: "interpreting", pages_done: 0, pages_total: null, percent: null },
    { stage: "interpreting", pages_done: 0, pages_total: null, percent: null },
  ]);
  await page.goto("/captures/run-1");

  await expect(page.getByText("Working out the invoice…")).toBeVisible();
  // A bar here would be a claim about remaining time that nothing measured.
  await expect(page.getByRole("progressbar")).toBeHidden();
});
