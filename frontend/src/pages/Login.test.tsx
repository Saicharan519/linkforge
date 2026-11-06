import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/test/server";
import { API, apiError, meSignedIn, meSignedOut, renderApp, USER } from "@/test/utils";

async function fillAndSubmit(email: string, password: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("Login", () => {
  it("has labelled fields and a link to register", async () => {
    server.use(meSignedOut());
    renderApp("/login");
    expect(await screen.findByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByRole("link", { name: "Create an account" })).toHaveAttribute("href", "/register");
  });

  it("asks for both fields and sends nothing when they are empty", async () => {
    server.use(meSignedOut()); // any login request would be unhandled and fail the test
    renderApp("/login");
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your email.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInvalid();
  });

  it("signs in and lands on the dashboard", async () => {
    let body: unknown;
    server.use(
      meSignedOut(),
      http.post(`${API}/auth/login`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(USER);
      }),
    );
    const { router } = renderApp("/login");
    await fillAndSubmit("ada@example.com", "a long enough password");

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(body).toEqual({ email: "ada@example.com", password: "a long enough password" });
    expect(router.state.location.pathname).toBe("/app");
  });

  it("returns to the page the user was sent from", async () => {
    server.use(meSignedOut(), http.post(`${API}/auth/login`, () => HttpResponse.json(USER)));
    const { router } = renderApp({ pathname: "/login", state: { from: "/app/settings" } });
    await fillAndSubmit("ada@example.com", "a long enough password");
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/settings");
  });

  it("never follows a return path that leaves the app", async () => {
    server.use(meSignedOut(), http.post(`${API}/auth/login`, () => HttpResponse.json(USER)));
    const { router } = renderApp({ pathname: "/login", state: { from: "//evil.example/app" } });
    await fillAndSubmit("ada@example.com", "a long enough password");
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(router.state.location.pathname).toBe("/app");
  });

  it("says the credentials are wrong, stays on the page and lets the user retry", async () => {
    server.use(meSignedOut(), http.post(`${API}/auth/login`, () => apiError(401, "invalid_credentials")));
    const { router } = renderApp("/login");
    await fillAndSubmit("ada@example.com", "wrong password!");

    expect(await screen.findByRole("alert")).toHaveTextContent("The email or password is wrong.");
    expect(router.state.location.pathname).toBe("/login");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("explains rate limiting and an unavailable service in plain words", async () => {
    server.use(meSignedOut(), http.post(`${API}/auth/login`, () => apiError(429, "rate_limited")));
    renderApp("/login");
    await fillAndSubmit("ada@example.com", "a long enough password");
    expect(await screen.findByRole("alert")).toHaveTextContent(/too many attempts/i);

    server.use(http.post(`${API}/auth/login`, () => apiError(503, "unavailable")));
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/unavailable right now/i));
  });

  it("shows a 422 next to the field it names", async () => {
    server.use(
      meSignedOut(),
      http.post(`${API}/auth/login`, () =>
        apiError(422, "validation_error", "bad", [{ field: "email", message: "must be a valid email address" }]),
      ),
    );
    renderApp("/login");
    await fillAndSubmit("not-an-email", "a long enough password");
    const field = await screen.findByLabelText("Email");
    await waitFor(() => expect(field).toHaveAccessibleDescription("Must be a valid email address."));
    expect(field).toBeInvalid();
  });

  it("disables the button while the request runs, so a second click cannot double-submit", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    let requests = 0;
    server.use(
      meSignedOut(),
      http.post(`${API}/auth/login`, async () => {
        requests++;
        await gate;
        return HttpResponse.json(USER);
      }),
    );
    renderApp("/login");
    await fillAndSubmit("ada@example.com", "a long enough password");

    const busy = await screen.findByRole("button", { name: "Signing in…" });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    release();
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(requests).toBe(1);
  });

  it("sends a visitor who is already signed in straight to the dashboard", async () => {
    server.use(meSignedIn());
    const { router } = renderApp("/login");
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app");
  });

  it("confirms a new account after registering", async () => {
    server.use(meSignedOut());
    renderApp({ pathname: "/login", state: { registered: true } });
    expect(await screen.findByRole("status")).toHaveTextContent("Account created. Sign in to continue.");
  });
});
