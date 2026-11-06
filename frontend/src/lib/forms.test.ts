import type { UseFormSetError } from "react-hook-form";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import { applyApiErrors, describeError, safeReturnPath } from "./forms";

type V = { email: string; password: string };

describe("safeReturnPath", () => {
  it("accepts paths inside the app", () => {
    expect(safeReturnPath({ from: "/app" })).toBe("/app");
    expect(safeReturnPath({ from: "/app/links?status=active" })).toBe("/app/links?status=active");
    expect(safeReturnPath({ from: "/app/links/abc#x" })).toBe("/app/links/abc#x");
  });

  it.each([
    ["another site", { from: "https://evil.example/app" }],
    ["protocol-relative", { from: "//evil.example" }],
    ["backslash trick", { from: "/\\evil.example" }],
    ["a lookalike prefix", { from: "/application" }],
    ["outside /app", { from: "/login" }],
    ["not a string", { from: 42 }],
    ["no state", undefined],
    ["null state", null],
    ["state of another shape", { registered: true }],
  ])("falls back to /app for %s", (_name, state) => {
    expect(safeReturnPath(state)).toBe("/app");
  });
});

describe("describeError", () => {
  it("maps API codes to plain sentences and never leaks server text", () => {
    expect(describeError(new ApiError(401, "invalid_credentials", "invalid email or password"))).toBe(
      "The email or password is wrong.",
    );
    expect(describeError(new ApiError(429, "rate_limited", "raw"))).toMatch(/too many attempts/i);
    expect(describeError(new ApiError(503, "unavailable", "raw"))).toMatch(/unavailable right now/i);
    expect(describeError(new ApiError(500, "internal_error", "stack trace here"))).toBe(
      "Something went wrong. Try again.",
    );
    expect(describeError(new Error("boom"))).toBe("Something went wrong. Try again.");
  });

  it("passes the connection message through", () => {
    expect(describeError(new ApiError(0, "network", "Could not reach the server."))).toBe(
      "Could not reach the server.",
    );
  });
});

describe("applyApiErrors", () => {
  const validation = (details: { field: string; message: string }[]) =>
    new ApiError(422, "validation_error", "bad", details);

  it("attaches every named field and returns no form-level message", () => {
    const setError = vi.fn() as unknown as UseFormSetError<V> & ReturnType<typeof vi.fn>;
    const out = applyApiErrors(
      validation([
        { field: "email", message: "must be a valid email address" },
        { field: "password", message: "too short" },
      ]),
      setError,
      ["email", "password"],
    );
    expect(out).toBeUndefined();
    expect(setError).toHaveBeenCalledWith("email", { type: "server", message: "Must be a valid email address." });
    expect(setError).toHaveBeenCalledWith("password", { type: "server", message: "Too short." });
  });

  it("falls back to a form-level message when a field is unknown to the form", () => {
    const setError = vi.fn() as unknown as UseFormSetError<V>;
    expect(applyApiErrors(validation([{ field: "alias", message: "x" }]), setError, ["email", "password"])).toBe(
      "Check the form and try again.",
    );
  });

  it("falls back when only some fields could be placed", () => {
    const setError = vi.fn() as unknown as UseFormSetError<V>;
    const out = applyApiErrors(
      validation([
        { field: "email", message: "bad" },
        { field: "other", message: "bad" },
      ]),
      setError,
      ["email", "password"],
    );
    expect(out).toBe("Check the form and try again.");
  });

  it("uses describeError for anything that is not a 422", () => {
    const setError = vi.fn() as unknown as UseFormSetError<V>;
    expect(applyApiErrors(new ApiError(429, "rate_limited", "x"), setError, ["email"])).toMatch(/too many/i);
    expect(setError).not.toHaveBeenCalled();
  });
});
