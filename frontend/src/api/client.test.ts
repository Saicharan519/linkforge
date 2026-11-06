import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "@/test/server";
import { api, ApiError, setUnauthorizedHandler } from "./client";

const URL = "http://localhost:3000/api/v1/thing";

describe("api client", () => {
  it("sends credentials and uses the relative /api/v1 prefix", async () => {
    server.use(http.get(URL, () => HttpResponse.json({ ok: true })));
    // MSW rebuilds the Request and drops `credentials`, so look at what the client handed to fetch.
    const spy = vi.spyOn(globalThis, "fetch");
    await expect(api("/thing")).resolves.toEqual({ ok: true });
    expect(spy).toHaveBeenCalledWith("/api/v1/thing", expect.objectContaining({ credentials: "include" }));
    spy.mockRestore();
  });

  it("copies the lf_csrf cookie into X-CSRF-Token on POST, PATCH and DELETE", async () => {
    document.cookie = "lf_csrf=tok%2B1; path=/";
    const seen: Record<string, string | null> = {};
    server.use(
      http.all(URL, ({ request }) => {
        seen[request.method] = request.headers.get("x-csrf-token");
        return new HttpResponse(null, { status: 204 });
      }),
    );
    for (const method of ["POST", "PATCH", "DELETE"] as const) {
      await api("/thing", { method });
    }
    expect(seen).toEqual({ POST: "tok+1", PATCH: "tok+1", DELETE: "tok+1" });
  });

  it("does not send the token on GET, and sends none when there is no cookie", async () => {
    const seen: Record<string, string | null> = {};
    server.use(
      http.all(URL, ({ request }) => {
        seen[request.method] = request.headers.get("x-csrf-token");
        return HttpResponse.json({});
      }),
    );
    document.cookie = "lf_csrf=tok; path=/";
    await api("/thing");
    document.cookie = "lf_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    await api("/thing", { method: "POST" });
    expect(seen).toEqual({ GET: null, POST: null });
  });

  it("sends a JSON body with a content type, and none without a body", async () => {
    let type: string | null = "unset";
    let body: unknown;
    server.use(
      http.post(URL, async ({ request }) => {
        type = request.headers.get("content-type");
        body = await request.json();
        return HttpResponse.json({});
      }),
    );
    await api("/thing", { method: "POST", body: { a: 1 } });
    expect(type).toBe("application/json");
    expect(body).toEqual({ a: 1 });
  });

  it("turns the API error body into an ApiError with per-field messages", async () => {
    server.use(
      http.post(URL, () =>
        HttpResponse.json(
          {
            error: {
              code: "validation_error",
              message: "the request contains invalid data",
              details: [{ field: "alias", message: "too short" }],
            },
          },
          { status: 422 },
        ),
      ),
    );
    const error = await api("/thing", { method: "POST" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.code).toBe("validation_error");
    expect(apiError.fieldMessage("alias")).toBe("too short");
    expect(apiError.fieldMessage("other")).toBeUndefined();
  });

  it("survives an error response that is not JSON", async () => {
    server.use(http.get(URL, () => new HttpResponse("<html>bad gateway</html>", { status: 502 })));
    const error = (await api("/thing").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.code).toBe("unknown");
  });

  it("reports a dropped connection as status 0, code network", async () => {
    server.use(http.get(URL, () => HttpResponse.error()));
    const error = (await api("/thing").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.code).toBe("network");
  });

  describe("401 handling", () => {
    const unauthorized = () =>
      HttpResponse.json({ error: { code: "unauthorized", message: "sign in" } }, { status: 401 });

    it("calls the unauthorized handler and still throws", async () => {
      const handler = vi.fn();
      setUnauthorizedHandler(handler);
      server.use(http.get(URL, unauthorized));
      await expect(api("/thing")).rejects.toMatchObject({ status: 401 });
      expect(handler).toHaveBeenCalledTimes(1);
    });

    it("does not call it when the call opts out (/auth/me at boot, /auth/login)", async () => {
      const handler = vi.fn();
      setUnauthorizedHandler(handler);
      server.use(http.get(URL, unauthorized));
      await expect(api("/thing", { redirectOn401: false })).rejects.toMatchObject({ status: 401 });
      expect(handler).not.toHaveBeenCalled();
    });

    it("does not call it for other errors", async () => {
      const handler = vi.fn();
      setUnauthorizedHandler(handler);
      server.use(http.get(URL, () => HttpResponse.json({ error: { code: "not_found", message: "x" } }, { status: 404 })));
      await expect(api("/thing")).rejects.toMatchObject({ status: 404 });
      expect(handler).not.toHaveBeenCalled();
    });
  });
});
