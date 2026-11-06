// The one place that talks to the network. Rules (frontend/CLAUDE.md):
// - same origin only: paths are relative, always under /api/v1 (no CORS, no absolute URLs);
// - cookie session: every request sends credentials; nothing is stored in localStorage;
// - unsafe methods copy the readable lf_csrf cookie into the X-CSRF-Token header (double submit);
// - a 401 clears the user cache and goes to /login, except for calls that opt out
//   (/auth/me at boot, and /auth/login where 401 means "wrong password", a form error).

const API_PREFIX = "/api/v1";
const CSRF_COOKIE = "lf_csrf";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export interface FieldProblem {
  field: string;
  message: string;
}

/** Every failed call becomes one of these, so UI code only ever handles one error type. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: FieldProblem[];

  constructor(status: number, code: string, message: string, details: FieldProblem[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** The API's message for one field of a 422, if it named that field. */
  fieldMessage(field: string): string | undefined {
    return this.details.find((d) => d.field === field)?.message;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Registered once by the root layout; called on any 401 that has not opted out. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function readCookie(name: string): string | undefined {
  for (const part of document.cookie.split("; ")) {
    const eq = part.indexOf("=");
    if (eq > 0 && part.slice(0, eq) === name) {
      return decodeURIComponent(part.slice(eq + 1));
    }
  }
  return undefined;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** false = a 401 is returned as an ApiError without clearing the cache or redirecting. */
  redirectOn401?: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseProblems(value: unknown): FieldProblem[] {
  if (!Array.isArray(value)) return [];
  const out: FieldProblem[] = [];
  for (const item of value) {
    if (isRecord(item) && typeof item.field === "string" && typeof item.message === "string") {
      out.push({ field: item.field, message: item.message });
    }
  }
  return out;
}

async function toApiError(response: Response): Promise<ApiError> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }
  const error = isRecord(payload) && isRecord(payload.error) ? payload.error : undefined;
  const code = typeof error?.code === "string" ? error.code : "unknown";
  const message =
    typeof error?.message === "string" ? error.message : `Request failed (${response.status})`;
  return new ApiError(response.status, code, message, parseProblems(error?.details));
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };

  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (UNSAFE_METHODS.has(method)) {
    // Absent before the first login (register/login are allowed without it by the API).
    const token = readCookie(CSRF_COOKIE);
    if (token) headers["X-CSRF-Token"] = token;
  }

  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      method,
      headers,
      credentials: "include",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "network", "Could not reach the server. Check your connection.");
  }

  if (!response.ok) {
    const error = await toApiError(response);
    if (response.status === 401 && options.redirectOn401 !== false) {
      unauthorizedHandler?.();
    }
    throw error;
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
