import { browserTimeZone } from "./format";

const invalidDate = () => new Error("Choose a valid calendar date.");

function checked(date: Date): Date {
  if (!Number.isFinite(date.getTime())) throw invalidDate();
  return date;
}

/** Calendar setters preserve the local wall time across offset changes. */
export function addLocalDays(date: Date, days: number): Date {
  if (!Number.isSafeInteger(days)) throw new Error("Choose a whole number of calendar days.");
  const result = new Date(checked(date).getTime());
  result.setDate(result.getDate() + days);
  return checked(result);
}

export function startOfLocalWeek(date: Date): Date {
  const start = new Date(checked(date).getTime());
  start.setHours(0, 0, 0, 0);
  return addLocalDays(start, -((start.getDay() + 6) % 7));
}

/** Date inputs contain local calendar dates, never a sliced UTC timestamp. */
export function localDateValue(date: Date): string {
  checked(date);
  const year = date.getFullYear();
  if (year < 1 || year > 9999) throw invalidDate();
  return `${String(year).padStart(4, "0")}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function localTimeValue(date: Date): string {
  checked(date);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function calendarDate(value: string): Date {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) throw invalidDate();
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const result = new Date(0);
  result.setFullYear(year, month - 1, day);
  result.setHours(0, 0, 0, 0);
  if (year < 1 || localDateValue(result) !== value) throw invalidDate();
  return result;
}

function atLocalTime(date: Date, value: string): Date {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  if (!match) throw new Error("Choose a valid start and end time.");
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) throw new Error("Choose a valid start and end time.");
  const result = new Date(date.getTime());
  result.setHours(hours, minutes, 0, 0);
  // A spring clock change can normalize a nonexistent local time forward.
  if (localDateValue(result) !== localDateValue(date) || localTimeValue(result) !== value) {
    throw new Error("That time does not exist on this date in your time zone. Choose another time.");
  }
  return result;
}

export interface AssignmentWindowInput {
  date: string;
  /** Inclusive last day for all-day work; same-day default for timed work. */
  end_date?: string;
  start: string;
  end: string;
  all_day: boolean;
}

export function assignmentWindow(input: AssignmentWindowInput): {
  starts_at: string;
  ends_at: string;
  time_zone: string;
} {
  const date = calendarDate(input.date);
  const endDate = calendarDate(input.end_date ?? input.date);
  if (localDateValue(endDate) < localDateValue(date)) {
    throw new Error("The end date must be on or after the start date.");
  }
  const starts = input.all_day ? date : atLocalTime(date, input.start);
  const ends = input.all_day ? addLocalDays(endDate, 1) : atLocalTime(endDate, input.end);
  localDateValue(ends);
  if (ends <= starts) throw new Error("The assignment must end after it starts on the selected date.");
  return {
    starts_at: starts.toISOString(),
    ends_at: ends.toISOString(),
    time_zone: browserTimeZone(),
  };
}
