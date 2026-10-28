import { test, expect } from "@playwright/test";

test.use({ timezoneId: "Europe/Riga" });
test.beforeEach(async ({ page }) => { await page.goto("/design"); });

test("a Monday date input stays Monday in a positive UTC offset", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const path = "/src/lib/scheduleDates.ts";
    const dates = await import(path);
    const original = new Date("2026-10-21T12:00:00+03:00");
    const monday = dates.startOfLocalWeek(original);
    return {
      day: dates.localDateValue(monday),
      time: dates.localTimeValue(monday),
      weekday: monday.getDay(),
      original: original.toISOString(),
    };
  });
  expect(result).toEqual({ day: "2026-10-19", time: "00:00", weekday: 1, original: "2026-10-21T09:00:00.000Z" });
});

test("week navigation preserves local midnight through both clock changes", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const path = "/src/lib/scheduleDates.ts";
    const dates = await import(path);
    return ["2026-03-23T00:00:00", "2026-10-19T00:00:00"].map((value) => {
      const start = new Date(value);
      const next = dates.addLocalDays(start, 7);
      const back = dates.addLocalDays(next, -7);
      return { day: dates.localDateValue(next), time: dates.localTimeValue(next), hours: (next.getTime() - start.getTime()) / 3_600_000, back: back.toISOString(), original: start.toISOString() };
    });
  });
  expect(result[0]).toMatchObject({ day: "2026-03-30", time: "00:00", hours: 167 });
  expect(result[1]).toMatchObject({ day: "2026-10-26", time: "00:00", hours: 169 });
  for (const value of result) expect(value.back).toBe(value.original);
});

for (const fixture of [
  { day: "2026-03-29", start: "2026-03-28T22:00:00.000Z", end: "2026-03-29T21:00:00.000Z", hours: 23 },
  { day: "2026-10-25", start: "2026-10-24T21:00:00.000Z", end: "2026-10-25T22:00:00.000Z", hours: 25 },
]) {
  test(`all-day work on ${fixture.day} ends at the next local midnight`, async ({ page }) => {
    const result = await page.evaluate(async (date) => {
      const path = "/src/lib/scheduleDates.ts";
      const dates = await import(path);
      return dates.assignmentWindow({ date, start: "", end: "", all_day: true });
    }, fixture.day);
    expect(result).toEqual({ starts_at: fixture.start, ends_at: fixture.end, time_zone: "Europe/Riga" });
    expect((Date.parse(result.ends_at) - Date.parse(result.starts_at)) / 3_600_000).toBe(fixture.hours);
  });
}

test("timed work records exact local choices and the browser time zone", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const path = "/src/lib/scheduleDates.ts";
    const dates = await import(path);
    return dates.assignmentWindow({ date: "2026-10-21", start: "09:15", end: "17:45", all_day: false });
  });
  expect(result).toEqual({ starts_at: "2026-10-21T06:15:00.000Z", ends_at: "2026-10-21T14:45:00.000Z", time_zone: "Europe/Riga" });
});

test("multi-day work preserves its end date and all-day ranges include the last day", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const path = "/src/lib/scheduleDates.ts";
    const dates = await import(path);
    return {
      timed: dates.assignmentWindow({ date: "2026-10-24", end_date: "2026-10-26", start: "17:00", end: "09:00", all_day: false }),
      allDay: dates.assignmentWindow({ date: "2026-10-24", end_date: "2026-10-26", start: "", end: "", all_day: true }),
    };
  });
  expect(result).toEqual({
    timed: { starts_at: "2026-10-24T14:00:00.000Z", ends_at: "2026-10-26T07:00:00.000Z", time_zone: "Europe/Riga" },
    allDay: { starts_at: "2026-10-23T21:00:00.000Z", ends_at: "2026-10-26T22:00:00.000Z", time_zone: "Europe/Riga" },
  });
});

test("invalid dates, invalid times and nonexistent spring times fail explicitly", async ({ page }) => {
  const errors = await page.evaluate(async () => {
    const path = "/src/lib/scheduleDates.ts";
    const dates = await import(path);
    const base = { date: "2026-10-21", start: "09:00", end: "17:00", all_day: false };
    return [
      { date: "2026-02-30" }, { date: "" }, { date: "2026-13-01" },
      { start: "25:00" }, { end: "17:60" }, { end: "09:00" },
      { start: "17:00", end: "09:00" }, { date: "2026-03-29", start: "03:30" },
      { end_date: "2026-10-20" },
    ].map((patch) => {
      try { dates.assignmentWindow({ ...base, ...patch }); return "accepted"; }
      catch (error) { return error instanceof Error ? error.message : "Unknown error"; }
    });
  });
  expect(errors).toHaveLength(9);
  expect(errors).not.toContain("accepted");
  expect(errors[0]).toBe("Choose a valid calendar date.");
  expect(errors[5]).toBe("The assignment must end after it starts on the selected date.");
  expect(errors[7]).toContain("does not exist");
  expect(errors[8]).toBe("The end date must be on or after the start date.");
});

test.describe("a different browser time zone", () => {
  test.use({ timezoneId: "America/New_York" });
  test("all-day boundaries follow that zone's clock-change dates", async ({ page }) => {
    const result = await page.evaluate(async () => {
      const path = "/src/lib/scheduleDates.ts";
      const dates = await import(path);
      return ["2026-03-08", "2026-11-01"].map((date) => dates.assignmentWindow({ date, start: "", end: "", all_day: true }));
    });
    expect(result).toEqual([
      { starts_at: "2026-03-08T05:00:00.000Z", ends_at: "2026-03-09T04:00:00.000Z", time_zone: "America/New_York" },
      { starts_at: "2026-11-01T04:00:00.000Z", ends_at: "2026-11-02T05:00:00.000Z", time_zone: "America/New_York" },
    ]);
  });
});
