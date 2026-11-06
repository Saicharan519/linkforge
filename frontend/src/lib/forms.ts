import type { FieldPath, FieldValues, UseFormSetError } from "react-hook-form";
import { ApiError } from "@/api/client";

/** Plain-language text for a failed call, by the API's error code. Never shows raw server text. */
export function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong. Try again.";
  switch (error.code) {
    case "invalid_credentials":
      return "The email or password is wrong.";
    case "rate_limited":
      return "Too many attempts. Wait a minute, then try again.";
    case "unavailable":
      return "LinkForge is unavailable right now. Try again in a moment.";
    case "network":
      return error.message;
    default:
      return "Something went wrong. Try again.";
  }
}

/**
 * Puts the API's 422 messages next to the fields they name. Returns the text for a form-level
 * message when something is left over: a non-422 error, or a 422 about a field this form lacks.
 * Returns undefined when every problem was attached to a field.
 */
export function applyApiErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: readonly FieldPath<T>[],
): string | undefined {
  if (!(error instanceof ApiError) || error.status !== 422) return describeError(error);

  let placed = 0;
  for (const problem of error.details) {
    const field = fields.find((f) => f === problem.field);
    if (field) {
      setError(field, { type: "server", message: sentence(problem.message) });
      placed++;
    }
  }
  return placed > 0 && placed === error.details.length ? undefined : "Check the form and try again.";
}

/** The API's messages are lower-case fragments ("must be a valid email address"): make a sentence. */
function sentence(text: string): string {
  const s = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(s) ? s : `${s}.`;
}

/**
 * Where to go after signing in. Only paths inside the app are accepted: anything else (another
 * site, a protocol-relative "//host", a non-string) falls back to the dashboard.
 */
export function safeReturnPath(state: unknown): string {
  if (typeof state === "object" && state !== null && "from" in state) {
    const from = (state as { from: unknown }).from;
    if (typeof from === "string" && /^\/app(?:[/?#]|$)/.test(from)) return from;
  }
  return "/app";
}
