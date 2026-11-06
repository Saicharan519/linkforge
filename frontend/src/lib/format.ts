// Timestamps (created_at, expires_at, last_used_at...) are UTC ISO strings and are shown in the
// user's own time zone. Daily analytics buckets are NOT timestamps and never go through here.

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const numbers = new Intl.NumberFormat();

export const formatDateTime = (iso: string) => dateTime.format(new Date(iso));
export const formatDate = (iso: string) => dateOnly.format(new Date(iso));
export const formatCount = (n: number) => numbers.format(n);

const pad = (n: number) => String(n).padStart(2, "0");

/** UTC ISO -> the "YYYY-MM-DDTHH:mm" text a datetime-local input wants, in the user's time zone. */
export function isoToLocalInput(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The text of a datetime-local input (user's time zone) -> UTC ISO. Empty or invalid -> null. */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}
