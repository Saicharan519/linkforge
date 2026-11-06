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
  await user.click(screen.getByRole("button", { name: "Create account" }));
}

const created = () => http.post(`${API}/auth/register`, () => HttpResponse.json(USER, { status: 201 }));

describe("Register", () => {
  it("states the password rule and has a link to sign in", async () => {
    server.use(meSignedOut());
    renderApp("/register");
    const password = await screen.findByLabelText("Password");
    expect(password).toHaveAccessibleDescription("10 to 128 characters.");
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("rejects a short password without calling the API", async () => {
    server.use(meSignedOut()); // a register request would be unhandled and fail the test
    renderApp("/register");
    await fillAndSubmit("ada@example.com", "123456789");
    expect(await screen.findByText("Use at least 10 characters.")).toBeInTheDocument();
  });

  it("accepts exactly 10 characters and rejects 129", async () => {
    let posts = 0;
    server.use(
      meSignedOut(),
      http.post(`${API}/auth/register`, () => {
        posts++;
        return HttpResponse.json(USER, { status: 201 });
      }),
    );
    renderApp("/register");
    await fillAndSubmit("ada@example.com", "x".repeat(129));
    expect(await screen.findByText("Use at most 128 characters.")).toBeInTheDocument();
    expect(posts).toBe(0);

    const password = screen.getByLabelText("Password");
    await userEvent.clear(password);
    await userEvent.type(password, "x".repeat(10));
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(posts).toBe(1);
  });

  it("accepts exactly 128 characters", async () => {
    server.use(meSignedOut(), created());
    renderApp("/register");
    await fillAndSubmit("ada@example.com", "x".repeat(128));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("counts characters the way the API does, not UTF-16 units", async () => {
    server.use(meSignedOut(), created());
    renderApp("/register");
    // 5 emoji = 5 characters (10 UTF-16 units): too short.
    await fillAndSubmit("ada@example.com", "🔗".repeat(5));
    expect(await screen.findByText("Use at least 10 characters.")).toBeInTheDocument();

    // 65 emoji = 65 characters (130 UTF-16 units): within the limit.
    const password = screen.getByLabelText("Password");
    await userEvent.clear(password);
    await userEvent.type(password, "🔗".repeat(65));
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("disables the button while the request runs, so a second click cannot double-submit", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    let requests = 0;
    server.use(
      meSignedOut(),
      http.post(`${API}/auth/register`, async () => {
        requests++;
        await gate;
        return HttpResponse.json(USER, { status: 201 });
      }),
    );
    renderApp("/register");
    await fillAndSubmit("ada@example.com", "a long enough password");

    const busy = await screen.findByRole("button", { name: "Creating account…" });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    release();
    await screen.findByRole("heading", { name: "Sign in" });
    expect(requests).toBe(1);
  });

  it("creates the account and goes to sign in with a confirmation", async () => {
    let body: unknown;
    server.use(
      meSignedOut(),
      http.post(`${API}/auth/register`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(USER, { status: 201 });
      }),
    );
    const { router } = renderApp("/register");
    await fillAndSubmit("ada@example.com", "a long enough password");

    expect(await screen.findByRole("status")).toHaveTextContent("Account created. Sign in to continue.");
    expect(router.state.location.pathname).toBe("/login");
    expect(body).toEqual({ email: "ada@example.com", password: "a long enough password" });
  });

  it("shows 'already registered' on the email field and stays", async () => {
    server.use(meSignedOut(), http.post(`${API}/auth/register`, () => apiError(409, "email_taken")));
    const { router } = renderApp("/register");
    await fillAndSubmit("ada@example.com", "a long enough password");

    const email = await screen.findByLabelText("Email");
    await waitFor(() => expect(email).toHaveAccessibleDescription("This email is already registered."));
    expect(router.state.location.pathname).toBe("/register");
  });

  it("shows a 422 next to the field it names", async () => {
    server.use(
      meSignedOut(),
      http.post(`${API}/auth/register`, () =>
        apiError(422, "validation_error", "bad", [{ field: "email", message: "must be a valid email address" }]),
      ),
    );
    renderApp("/register");
    await fillAndSubmit("nope", "a long enough password");
    await waitFor(() =>
      expect(screen.getByLabelText("Email")).toHaveAccessibleDescription("Must be a valid email address."),
    );
  });

  it("shows a form-level message when the service is unavailable", async () => {
    server.use(meSignedOut(), http.post(`${API}/auth/register`, () => apiError(503, "unavailable")));
    renderApp("/register");
    await fillAndSubmit("ada@example.com", "a long enough password");
    expect(await screen.findByRole("alert")).toHaveTextContent(/unavailable right now/i);
  });

  it("sends a signed-in visitor to the dashboard", async () => {
    server.use(meSignedIn());
    renderApp("/register");
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });
});
