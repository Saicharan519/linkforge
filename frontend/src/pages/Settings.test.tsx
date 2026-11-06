import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { ApiKey } from "@/api/types";
import { server } from "@/test/server";
import { API, apiError, meSignedIn, renderApp } from "@/test/utils";

const SECRET = "lf_ab12cd34_THE_WHOLE_SECRET_VALUE";

const key = (over: Partial<ApiKey> = {}): ApiKey => ({
  id: "k1",
  name: "Deploy script",
  prefix: "lf_ab12cd34",
  created_at: "2026-03-01T10:00:00Z",
  last_used_at: null,
  revoked_at: null,
  ...over,
});

interface Seen {
  method: string;
  body: unknown;
  csrf: string | null;
}

/** An in-memory /api-keys that records what the browser sent. */
function fakeKeys(initial: ApiKey[]) {
  const state = { keys: [...initial], seen: [] as Seen[] };
  const note = async (request: Request) =>
    state.seen.push({
      method: request.method,
      body: request.method === "POST" ? await request.json().catch(() => undefined) : undefined,
      csrf: request.headers.get("x-csrf-token"),
    });
  const handlers = [
    http.get(`${API}/api-keys`, () => HttpResponse.json(state.keys)),
    http.post(`${API}/api-keys`, async ({ request }) => {
      await note(request);
      const created = key({ id: `k${state.keys.length + 1}`, name: state.seen.at(-1)!.body ? (state.seen.at(-1)!.body as { name: string }).name : "?", prefix: "lf_ab12cd34" });
      state.keys.push(created);
      return HttpResponse.json({ ...created, key: SECRET }, { status: 201 });
    }),
    http.delete(`${API}/api-keys/:id`, async ({ request, params }) => {
      await note(request);
      const k = state.keys.find((x) => x.id === params.id);
      if (k) k.revoked_at = "2026-03-10T10:00:00Z";
      return new HttpResponse(null, { status: 204 });
    }),
  ];
  return { state, handlers };
}

function setup(keys: ApiKey[]) {
  const fake = fakeKeys(keys);
  server.use(meSignedIn(), ...fake.handlers);
  const view = renderApp("/app/settings");
  return { fake, ...view };
}

describe("Settings: API keys", () => {
  it("lists keys with name, fingerprint, dates, and no secret", async () => {
    setup([
      key(),
      key({ id: "k2", name: "Old", last_used_at: "2026-03-05T09:00:00Z", revoked_at: "2026-03-06T09:00:00Z" }),
    ]);
    const list = await screen.findByRole("list", { name: "Your API keys" });
    const [active, revoked] = within(list).getAllByRole("listitem");

    expect(within(active!).getByText("Deploy script")).toBeInTheDocument();
    expect(within(active!).getByText("lf_ab12cd34…")).toBeInTheDocument();
    expect(active).toHaveTextContent("Last used never");
    expect(within(active!).getByRole("button", { name: "Revoke Deploy script" })).toBeInTheDocument();

    expect(revoked).not.toHaveTextContent("Last used never"); // it has a last-used date
    expect(revoked).toHaveTextContent(/Last used .*2026/);
    expect(within(revoked!).getByText("Revoked")).toBeInTheDocument();
    expect(within(revoked!).queryByRole("button")).not.toBeInTheDocument(); // nothing left to revoke
    expect(document.body).not.toHaveTextContent(SECRET);
  });

  it("explains when there are no keys", async () => {
    setup([]);
    expect(await screen.findByRole("heading", { name: "No API keys" })).toBeInTheDocument();
  });

  it("shows an error with a retry that works", async () => {
    server.use(meSignedIn(), http.get(`${API}/api-keys`, () => apiError(503, "unavailable")));
    renderApp("/app/settings");
    expect(await screen.findByRole("alert")).toHaveTextContent(/unavailable right now/i);
    server.use(http.get(`${API}/api-keys`, () => HttpResponse.json([key()])));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Deploy script")).toBeInTheDocument();
  });

  it("asks for a name and sends nothing when it is empty or too long", async () => {
    const { fake } = setup([]);
    await userEvent.click(await screen.findByRole("button", { name: "New key" }));
    const dialog = await screen.findByRole("dialog", { name: "New API key" });

    await userEvent.click(within(dialog).getByRole("button", { name: "Create key" }));
    expect(await within(dialog).findByText("Enter a name.")).toBeInTheDocument();

    await userEvent.type(within(dialog).getByLabelText("Name"), "x".repeat(65));
    await userEvent.click(within(dialog).getByRole("button", { name: "Create key" }));
    expect(await within(dialog).findByText("Use at most 64 characters.")).toBeInTheDocument();
    expect(fake.state.seen).toHaveLength(0);
  });

  it("creates a key, shows the secret once, and never again", async () => {
    document.cookie = "lf_csrf=tok; path=/";
    const { fake } = setup([]);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "New key" }));
    const dialog = await screen.findByRole("dialog", { name: "New API key" });
    await user.type(within(dialog).getByLabelText("Name"), "  Deploy script ");
    await user.click(within(dialog).getByRole("button", { name: "Create key" }));

    // the reveal
    const reveal = await screen.findByRole("dialog", { name: "Copy your key now" });
    expect(reveal).toHaveTextContent(SECRET);
    expect(fake.state.seen[0]).toEqual({ method: "POST", body: { name: "Deploy script" }, csrf: "tok" });

    await user.click(within(reveal).getByRole("button", { name: "Copy API key" }));
    expect(await navigator.clipboard.readText()).toBe(SECRET);

    // a stray Escape or click outside must not throw the secret away
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Copy your key now" })).toBeInTheDocument();

    await user.click(within(reveal).getByRole("button", { name: "I have saved it" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.body).not.toHaveTextContent(SECRET);

    // the list now has the key, by fingerprint only
    const list = await screen.findByRole("list", { name: "Your API keys" });
    expect(within(list).getByText("Deploy script")).toBeInTheDocument();
    expect(within(list).getByText("lf_ab12cd34…")).toBeInTheDocument();
  });

  it("shows API validation and rate-limit messages in the form", async () => {
    setup([]);
    server.use(
      http.post(`${API}/api-keys`, () =>
        apiError(422, "validation_error", "bad", [{ field: "name", message: "String should have at most 64 characters" }]),
      ),
    );
    await userEvent.click(await screen.findByRole("button", { name: "New key" }));
    const dialog = await screen.findByRole("dialog", { name: "New API key" });
    await userEvent.type(within(dialog).getByLabelText("Name"), "x");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create key" }));
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Name")).toHaveAccessibleDescription(/String should have at most 64 characters\./),
    );

    server.use(http.post(`${API}/api-keys`, () => apiError(429, "rate_limited")));
    await userEvent.click(within(dialog).getByRole("button", { name: "Create key" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/too many attempts/i);
  });

  it("asks before revoking, Cancel revokes nothing", async () => {
    const { fake } = setup([key()]);
    await userEvent.click(await screen.findByRole("button", { name: "Revoke Deploy script" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke this key?" });
    expect(dialog).toHaveTextContent("Deploy script");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(fake.state.seen).toHaveLength(0);
  });

  it("revokes after confirmation and shows the key as revoked", async () => {
    document.cookie = "lf_csrf=tok; path=/";
    const { fake } = setup([key()]);
    await userEvent.click(await screen.findByRole("button", { name: "Revoke Deploy script" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Revoke key" }));

    expect(await screen.findByText("Key Deploy script revoked.")).toBeInTheDocument();
    expect(fake.state.seen[0]).toMatchObject({ method: "DELETE", csrf: "tok" });
    const list = await screen.findByRole("list", { name: "Your API keys" });
    const row = within(list).getAllByRole("listitem")[0]!;
    await waitFor(() => expect(within(row).getByText("Revoked")).toBeInTheDocument());
    expect(within(row).queryByRole("button", { name: /revoke/i })).not.toBeInTheDocument();
  });

  it("disables Revoke while the request runs, so a second click cannot revoke twice", async () => {
    setup([key()]);
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    let deletes = 0;
    server.use(
      http.delete(`${API}/api-keys/:id`, async () => {
        deletes++;
        await gate;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Revoke Deploy script" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke key" }));
    const busy = await within(dialog).findByRole("button", { name: "Revoking…" });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    release();
    await screen.findByText("Key Deploy script revoked.");
    expect(deletes).toBe(1);
  });

  it("keeps the dialog open and says why when revoking fails", async () => {
    setup([key()]);
    server.use(http.delete(`${API}/api-keys/:id`, () => apiError(503, "unavailable")));
    await userEvent.click(await screen.findByRole("button", { name: "Revoke Deploy script" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke key" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/unavailable right now/i);
  });
});

describe("Settings: change password", () => {
  async function fill(oldPw: string, newPw: string) {
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Current password"), oldPw);
    await user.type(screen.getByLabelText("New password"), newPw);
    await user.click(screen.getByRole("button", { name: "Change password" }));
  }

  it("changes the password, confirms it, clears the fields and sends the CSRF header", async () => {
    document.cookie = "lf_csrf=tok; path=/";
    let seen: { body: unknown; csrf: string | null } | undefined;
    server.use(
      meSignedIn(),
      http.post(`${API}/auth/change-password`, async ({ request }) => {
        seen = { body: await request.json(), csrf: request.headers.get("x-csrf-token") };
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/app/settings");
    await fill("the old password", "a brand new password");

    expect(await screen.findByText("Password changed. Your other sessions were signed out.")).toBeInTheDocument();
    expect(seen).toEqual({ body: { old_password: "the old password", new_password: "a brand new password" }, csrf: "tok" });
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.getByLabelText("New password")).toHaveValue("");
  });

  it("says the current password is wrong, on that field, and stays signed in", async () => {
    server.use(
      meSignedIn(),
      http.post(`${API}/auth/change-password`, () =>
        apiError(422, "validation_error", "bad", [{ field: "old_password", message: "incorrect" }]),
      ),
    );
    const { router } = renderApp("/app/settings");
    await fill("wrong one here", "a brand new password");
    await waitFor(() =>
      expect(screen.getByLabelText("Current password")).toHaveAccessibleDescription("Your current password is wrong."),
    );
    expect(router.state.location.pathname).toBe("/app/settings");
  });

  it("checks required fields and the length rule without calling the API", async () => {
    server.use(meSignedIn()); // a change-password request would be unhandled and fail the test
    renderApp("/app/settings");
    await userEvent.click(await screen.findByRole("button", { name: "Change password" }));
    expect(await screen.findByText("Enter your current password.")).toBeInTheDocument();
    expect(screen.getByText("Choose a new password.")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Current password"), "old password");
    await userEvent.type(screen.getByLabelText("New password"), "123456789");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("Use at least 10 characters.")).toBeInTheDocument();
  });

  it("accepts 10 characters (counting emoji as one each) and rejects 129", async () => {
    let posts = 0;
    server.use(
      meSignedIn(),
      http.post(`${API}/auth/change-password`, () => {
        posts++;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/app/settings");
    await fill("old password", "x".repeat(129));
    expect(await screen.findByText("Use at most 128 characters.")).toBeInTheDocument();
    expect(posts).toBe(0);

    await userEvent.clear(screen.getByLabelText("New password"));
    await userEvent.type(screen.getByLabelText("New password"), "🔗".repeat(10));
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    await screen.findByText(/Password changed/);
    expect(posts).toBe(1);
  });

  it("accepts exactly 128 characters, and counts characters not UTF-16 units", async () => {
    let posts = 0;
    server.use(
      meSignedIn(),
      http.post(`${API}/auth/change-password`, () => {
        posts++;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/app/settings");
    await fill("old password", "x".repeat(128));
    await screen.findByText(/Password changed/);
    expect(posts).toBe(1);

    // 5 emoji = 5 characters (10 UTF-16 units): too short.
    const next = screen.getByLabelText("New password");
    await userEvent.type(screen.getByLabelText("Current password"), "old password");
    await userEvent.type(next, "🔗".repeat(5));
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("Use at least 10 characters.")).toBeInTheDocument();

    // 65 emoji = 65 characters (130 UTF-16 units): allowed.
    await userEvent.clear(next);
    await userEvent.type(next, "🔗".repeat(65));
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(posts).toBe(2));
  });

  it("warns that the password may already be changed when the server could not finish", async () => {
    server.use(meSignedIn(), http.post(`${API}/auth/change-password`, () => apiError(503, "unavailable")));
    renderApp("/app/settings");
    await fill("old password", "a brand new password");
    expect(await screen.findByRole("alert")).toHaveTextContent(/may already be the new one/i);
  });

  it("sends the user to sign in when the session has ended (401)", async () => {
    server.use(meSignedIn(), http.post(`${API}/auth/change-password`, () => apiError(401, "unauthorized")));
    const { router } = renderApp("/app/settings");
    await fill("old password", "a brand new password");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.state).toEqual({ from: "/app/settings" });
  });
});
