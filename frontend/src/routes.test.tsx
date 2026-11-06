import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { api } from "@/api/client";
import { server } from "@/test/server";
import { meSignedOut, renderApp, USER } from "@/test/utils";

const API = "http://localhost:3000/api/v1";
const unauthorized = () =>
  HttpResponse.json({ error: { code: "unauthorized", message: "sign in" } }, { status: 401 });

describe("protected routes", () => {
  it("shows a loading state, then sends a signed-out visitor to /login with a return path", async () => {
    server.use(http.get(`${API}/auth/me`, unauthorized));
    const { router } = renderApp("/app/links?status=active");

    expect(screen.getByRole("status")).toHaveTextContent(/checking your session/i);

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.state).toEqual({ from: "/app/links?status=active" });
  });

  it("shows the screen and the user's email when signed in", async () => {
    server.use(http.get(`${API}/auth/me`, () => HttpResponse.json(USER)));
    renderApp("/app/links");

    expect(await screen.findByRole("heading", { name: "Links" })).toBeInTheDocument();
    expect(screen.getAllByText(USER.email).length).toBeGreaterThan(0);
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("shows an error, not a login page, when the session check itself fails", async () => {
    server.use(http.get(`${API}/auth/me`, () => HttpResponse.json({ error: { code: "unavailable", message: "x" } }, { status: 503 })));
    renderApp("/app");
    expect(await screen.findByRole("alert")).toHaveTextContent(/can't check your session/i);
  });

  it("does not guard public pages", async () => {
    // Only /auth/me is allowed (the page checks for an existing session); anything else fails the test.
    server.use(meSignedOut());
    renderApp("/login");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("falls back to a not-found page", async () => {
    renderApp("/nope");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});

describe("401 while using the app", () => {
  it("empties the cache and goes to /login, remembering the page", async () => {
    let calls = 0;
    server.use(
      // first answer: signed in. Every later call (a data request) is a 401.
      http.get(`${API}/auth/me`, () => (calls++ === 0 ? HttpResponse.json(USER) : unauthorized())),
    );
    const { router, queryClient } = renderApp("/app/settings");
    await screen.findByRole("heading", { name: "Settings" });

    queryClient.setQueryData(["links"], ["stale data of the old user"]);
    // A data call failing with 401 is what the client reports to the root layout.
    server.use(http.get(`${API}/links`, unauthorized));
    await act(async () => {
      await api("/links").catch(() => undefined);
    });

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.state).toEqual({ from: "/app/settings" });
    expect(queryClient.getQueryData(["links"])).toBeUndefined();
    expect(queryClient.getQueryData(["session"])).toBeNull();
  });
});

describe("401 on a public page", () => {
  it("does not move the visitor away from the page they are on", async () => {
    server.use(meSignedOut());
    const { router } = renderApp("/register");
    await screen.findByRole("heading", { name: "Create your account" });

    server.use(http.get(`${API}/links`, unauthorized));
    await act(async () => {
      await api("/links").catch(() => undefined);
    });

    expect(router.state.location.pathname).toBe("/register");
  });
});

describe("sign out", () => {
  it("posts to /auth/logout, drops the session and lands on /login", async () => {
    document.cookie = "lf_csrf=tok; path=/";
    let csrf: string | null = null;
    server.use(
      http.get(`${API}/auth/me`, () => HttpResponse.json(USER)),
      http.post(`${API}/auth/logout`, ({ request }) => {
        csrf = request.headers.get("x-csrf-token");
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { router, queryClient } = renderApp("/app");
    await screen.findByRole("heading", { name: "Dashboard" });
    queryClient.setQueryData(["links"], ["data of the signed-out user"]);

    await userEvent.click(screen.getAllByRole("button", { name: /sign out/i })[0]!);

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(csrf).toBe("tok");
    expect(queryClient.getQueryData(["session"])).toBeNull();
    expect(queryClient.getQueryData(["links"])).toBeUndefined();
  });
});
