import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { describe, expect, it } from "vitest";
import { fakeLinksApi, makeLink } from "@/test/fakeApi";
import { server } from "@/test/server";
import { API, apiError, meSignedIn, renderApp } from "@/test/utils";

function setup(links = [makeLink({ title: "Docs", code: "docs1", total_clicks: 1234 }), makeLink({ code: "plain1" })], url = "/app/links") {
  const fake = fakeLinksApi(links);
  server.use(meSignedIn(), ...fake.handlers);
  const view = renderApp(url);
  return { fake, ...view };
}

const lastList = (fake: ReturnType<typeof fakeLinksApi>) => fake.listRequests().at(-1)!.query;

describe("Links list", () => {
  it("shows loading, then each link with its title, short URL, destination, status and clicks", async () => {
    const fake = fakeLinksApi([makeLink({ title: "Docs", code: "docs1", total_clicks: 1234 }), makeLink({ code: "plain1" })]);
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    server.use(meSignedIn(), ...fake.handlers);
    server.use(
      http.get(`${API}/links`, async (info) => {
        await gate;
        const [handler] = fake.handlers;
        return (await handler!.run({ request: info.request, requestId: info.requestId }))?.response;
      }),
    );
    renderApp("/app/links");
    expect(await screen.findByRole("status", { name: "Loading links" })).toBeInTheDocument();
    release();

    const list = await screen.findByRole("list", { name: "Your links" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByRole("heading", { name: "Docs" })).toBeInTheDocument();
    expect(within(rows[0]!).getByText("https://go.example/docs1")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("Active")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("1,234")).toBeInTheDocument();
    // no title -> the code is the name
    expect(within(rows[1]!).getByRole("heading", { name: "plain1" })).toBeInTheDocument();
  });

  it("labels each status with a word, not only a colour", async () => {
    setup([
      makeLink({ code: "a1", status: "active" }),
      makeLink({ code: "d1", status: "disabled", is_active: false }),
      makeLink({ code: "e1", status: "expired", expires_at: "2026-01-01T00:00:00Z" }),
    ]);
    await screen.findByRole("list", { name: "Your links" });
    const list = screen.getByRole("list", { name: "Your links" });
    for (const word of ["Active", "Disabled", "Expired"]) expect(within(list).getByText(word)).toBeInTheDocument();
  });

  it("asks for the first page of 20 with no filters", async () => {
    const { fake } = setup();
    await screen.findByRole("list", { name: "Your links" });
    const q = lastList(fake);
    expect(q.get("page")).toBe("1");
    expect(q.get("page_size")).toBe("20");
    expect(q.has("q")).toBe(false);
    expect(q.has("status")).toBe(false);
  });

  it("searches 300 ms after typing, from page 1, and puts the search in the URL", async () => {
    const { fake, router } = setup();
    await screen.findByRole("list", { name: "Your links" });
    const before = fake.listRequests().length;

    await userEvent.type(screen.getByLabelText("Search"), "docs");
    expect(fake.listRequests().length).toBe(before); // nothing sent while typing quickly

    await waitFor(() => expect(lastList(fake).get("q")).toBe("docs"));
    expect(router.state.location.search).toContain("q=docs");
    expect(await screen.findAllByRole("heading", { level: 2 })).toHaveLength(1);
  });

  it("filters by status and starts again from page 1", async () => {
    const many = Array.from({ length: 45 }, (_, i) => makeLink({ code: `c${i}`, status: i % 2 ? "expired" : "active" }));
    const { fake, router } = setup(many, "/app/links?page=2");
    await screen.findByText(/Page 2 of/);

    await userEvent.selectOptions(screen.getByLabelText("Status"), "expired");
    await waitFor(() => expect(lastList(fake).get("status")).toBe("expired"));
    expect(lastList(fake).get("page")).toBe("1");
    expect(router.state.location.search).toBe("?status=expired");
  });

  it("pages forward and back, and disables the buttons at the ends", async () => {
    const many = Array.from({ length: 45 }, (_, i) => makeLink({ code: `c${i}` }));
    const { fake } = setup(many);
    expect(await screen.findByText("45 links · Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("45 links · Page 2 of 3")).toBeInTheDocument();
    expect(lastList(fake).get("page")).toBe("2");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("45 links · Page 3 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("45 links · Page 2 of 3")).toBeInTheDocument();
  });

  it("reads page and filters from the URL and ignores junk values", async () => {
    const { fake } = setup([makeLink()], "/app/links?q=abc&status=disabled&page=3");
    await waitFor(() => expect(fake.listRequests().length).toBeGreaterThan(0));
    const first = fake.listRequests()[0]!.query;
    expect([first.get("q"), first.get("status"), first.get("page")]).toEqual(["abc", "disabled", "3"]);
  });

  it.each(["-4", "0", "abc", "1.5x", ""])("falls back to page 1 for page=%s", async (bad) => {
    const { fake } = setup([makeLink()], `/app/links?status=bogus&page=${bad}`);
    await screen.findByRole("list", { name: "Your links" });
    const q = fake.listRequests()[0]!.query;
    expect(q.has("status")).toBe(false);
    expect(q.get("page")).toBe("1");
  });

  it("steps back when the current page no longer exists", async () => {
    const { router } = setup([makeLink({ code: "only" })], "/app/links?page=5");
    await waitFor(() => expect(router.state.location.search).toBe(""));
    expect(await screen.findByRole("list", { name: "Your links" })).toBeInTheDocument();
  });
});

describe("Links empty and error states", () => {
  it("invites the first link when there are none, and the button opens the form", async () => {
    setup([]);
    expect(await screen.findByRole("heading", { name: "No links yet" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /create your first link/i }));
    expect(await screen.findByRole("dialog", { name: "New link" })).toBeInTheDocument();
  });

  it("says nothing matches a filter and clears it on request", async () => {
    const { fake, router } = setup([makeLink({ code: "abc" })]);
    await screen.findByRole("list", { name: "Your links" });
    await userEvent.type(screen.getByLabelText("Search"), "zzz");
    expect(await screen.findByRole("heading", { name: "No links match" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByRole("list", { name: "Your links" })).toBeInTheDocument();
    expect(screen.getByLabelText("Search")).toHaveValue("");
    expect(router.state.location.search).toBe("");
    expect(lastList(fake).has("q")).toBe(false);
  });

  it("shows an error with a way to retry", async () => {
    const fake = fakeLinksApi([makeLink({ code: "ok1" })]);
    server.use(meSignedIn(), http.get(`${API}/links`, () => apiError(503, "unavailable")));
    renderApp("/app/links");
    expect(await screen.findByRole("alert")).toHaveTextContent(/unavailable right now/i);

    server.use(...fake.handlers); // the server recovers
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("list", { name: "Your links" })).toBeInTheDocument();
  });
});

describe("Create link", () => {
  async function openCreate() {
    await screen.findByRole("list", { name: "Your links" });
    await userEvent.click(screen.getByRole("button", { name: "New link" }));
    return screen.findByRole("dialog", { name: "New link" });
  }

  it("creates a link with only the destination, shows it in the list and says so", async () => {
    document.cookie = "lf_csrf=tok; path=/";
    const { fake } = setup();
    const dialog = await openCreate();

    await userEvent.type(within(dialog).getByLabelText("Destination URL"), "https://example.com/new");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create link" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Link created.")).toBeInTheDocument();

    const post = fake.state.requests.find((r) => r.method === "POST")!;
    expect(post.body).toEqual({ destination_url: "https://example.com/new" }); // no empty alias/title/expiry
    expect(post.csrf).toBe("tok");
    expect(await screen.findByText("https://example.com/new")).toBeInTheDocument();
  });

  it("sends alias, title and expiry (as UTC) when given", async () => {
    const { fake } = setup();
    const dialog = await openCreate();
    await userEvent.type(within(dialog).getByLabelText("Destination URL"), "https://example.com/x");
    await userEvent.type(within(dialog).getByLabelText("Custom alias (optional)"), "my-alias");
    await userEvent.type(within(dialog).getByLabelText("Title (optional)"), "Launch page");
    fireEvent.change(within(dialog).getByLabelText("Expires (optional)"), { target: { value: "2030-01-02T03:04" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Create link" }));

    await waitFor(() => expect(fake.state.requests.some((r) => r.method === "POST")).toBe(true));
    expect(fake.state.requests.find((r) => r.method === "POST")!.body).toEqual({
      destination_url: "https://example.com/x",
      alias: "my-alias",
      title: "Launch page",
      expires_at: new Date("2030-01-02T03:04").toISOString(),
    });
  });

  it("asks for a destination and sends nothing when it is empty", async () => {
    const { fake } = setup();
    const dialog = await openCreate();
    await userEvent.click(within(dialog).getByRole("button", { name: "Create link" }));
    expect(await within(dialog).findByText("Enter a destination URL.")).toBeInTheDocument();
    expect(fake.state.requests.some((r) => r.method === "POST")).toBe(false);
  });

  it("shows 'alias in use' on the alias field and keeps the form open", async () => {
    const { fake } = setup();
    fake.state.createError = apiError(409, "alias_taken");
    const dialog = await openCreate();
    await userEvent.type(within(dialog).getByLabelText("Destination URL"), "https://example.com/x");
    await userEvent.type(within(dialog).getByLabelText("Custom alias (optional)"), "taken");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create link" }));

    await waitFor(() =>
      expect(within(dialog).getByLabelText("Custom alias (optional)")).toHaveAccessibleDescription(/This alias is already in use\./),
    );
    expect(screen.getByRole("dialog", { name: "New link" })).toBeInTheDocument();
  });

  it("shows API validation messages next to their fields", async () => {
    const { fake } = setup();
    fake.state.createError = apiError(422, "validation_error", "bad", [
      { field: "destination_url", message: "must be an http or https URL" },
      { field: "alias", message: "this alias is reserved" },
    ]);
    const dialog = await openCreate();
    await userEvent.type(within(dialog).getByLabelText("Destination URL"), "ftp://x");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create link" }));

    await waitFor(() =>
      expect(within(dialog).getByLabelText("Destination URL")).toHaveAccessibleDescription("Must be an http or https URL."),
    );
    expect(within(dialog).getByLabelText("Custom alias (optional)")).toHaveAccessibleDescription(/This alias is reserved\./);
  });

  it("explains rate limiting in the form", async () => {
    const { fake } = setup();
    fake.state.createError = apiError(429, "rate_limited");
    const dialog = await openCreate();
    await userEvent.type(within(dialog).getByLabelText("Destination URL"), "https://example.com/x");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create link" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/too many attempts/i);
  });

  it("closes with Cancel and with Escape without sending anything", async () => {
    const { fake } = setup();
    await openCreate();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "New link" }));
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(fake.state.requests.some((r) => r.method === "POST")).toBe(false);
  });
});

describe("Edit, enable/disable and delete", () => {
  it("edits a link: no alias field, nothing to save until something changes, and only changes are sent", async () => {
    document.cookie = "lf_csrf=tok; path=/";
    const link = makeLink({ code: "docs1", title: "Docs", destination_url: "https://example.com/old", expires_at: "2030-05-06T07:08:00Z" });
    const { fake } = setup([link]);
    await userEvent.click(await screen.findByRole("button", { name: "Edit Docs" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit link" });

    expect(within(dialog).queryByLabelText(/alias/i)).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Destination URL")).toHaveValue("https://example.com/old");
    expect(within(dialog).getByRole("button", { name: "Save changes" })).toBeDisabled();

    const url = within(dialog).getByLabelText("Destination URL");
    await userEvent.clear(url);
    await userEvent.type(url, "https://example.com/new");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Link updated.")).toBeInTheDocument();
    const patch = fake.state.requests.find((r) => r.method === "PATCH")!;
    expect(patch.body).toEqual({ destination_url: "https://example.com/new" }); // title and expiry untouched
    expect(patch.csrf).toBe("tok");
  });

  it("clears the title and the expiry by sending null", async () => {
    const link = makeLink({ title: "Docs", expires_at: "2030-05-06T07:08:00Z" });
    const { fake } = setup([link]);
    await userEvent.click(await screen.findByRole("button", { name: "Edit Docs" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit link" });
    await userEvent.clear(within(dialog).getByLabelText("Title (optional)"));
    fireEvent.change(within(dialog).getByLabelText("Expires (optional)"), { target: { value: "" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await screen.findByText("Link updated.");
    expect(fake.state.requests.find((r) => r.method === "PATCH")!.body).toEqual({ title: null, expires_at: null });
  });

  it("disables and re-enables a link with is_active only", async () => {
    const { fake } = setup([makeLink({ code: "docs1", title: "Docs" })]);
    await userEvent.click(await screen.findByRole("button", { name: "Disable Docs" }));
    expect(await screen.findByText("Link disabled.")).toBeInTheDocument();
    expect(fake.state.requests.find((r) => r.method === "PATCH")!.body).toEqual({ is_active: false });

    await userEvent.click(await screen.findByRole("button", { name: "Enable Docs" }));
    expect(await screen.findByText("Link enabled.")).toBeInTheDocument();
    expect(fake.state.requests.filter((r) => r.method === "PATCH").at(-1)!.body).toEqual({ is_active: true });
  });

  it("reports a failed toggle", async () => {
    setup([makeLink({ title: "Docs" })]);
    server.use(http.patch(`${API}/links/:id`, () => apiError(503, "unavailable")));
    await userEvent.click(await screen.findByRole("button", { name: "Disable Docs" }));
    expect(await screen.findByText(/unavailable right now/i)).toBeInTheDocument();
  });

  it("asks before deleting, and Cancel deletes nothing", async () => {
    const { fake } = setup([makeLink({ code: "docs1", title: "Docs" })]);
    await userEvent.click(await screen.findByRole("button", { name: "Delete Docs" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete this link?" });
    expect(dialog).toHaveTextContent("https://go.example/docs1");
    expect(dialog).toHaveTextContent("404");

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(fake.state.requests.some((r) => r.method === "DELETE")).toBe(false);
    expect(screen.getByRole("heading", { name: "Docs" })).toBeInTheDocument();
  });

  it("deletes after confirmation, removes the row and says so", async () => {
    document.cookie = "lf_csrf=tok; path=/";
    const { fake } = setup([makeLink({ code: "docs1", title: "Docs" }), makeLink({ code: "keep1" })]);
    await userEvent.click(await screen.findByRole("button", { name: "Delete Docs" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete link" }));

    expect(await screen.findByText("Link deleted.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Docs" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "keep1" })).toBeInTheDocument();
    const del = fake.state.requests.find((r) => r.method === "DELETE")!;
    expect(del.csrf).toBe("tok");
  });

  it("keeps the dialog open and explains when deleting fails", async () => {
    setup([makeLink({ title: "Docs" })]);
    server.use(http.delete(`${API}/links/:id`, () => apiError(503, "unavailable")));
    await userEvent.click(await screen.findByRole("button", { name: "Delete Docs" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete link" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/unavailable right now/i);
  });
});

describe("Busy states", () => {
  it("disables Delete while the request runs, so a second click cannot delete twice", async () => {
    const { fake } = setup([makeLink({ code: "docs1", title: "Docs" })]);
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    let deletes = 0;
    server.use(
      http.delete(`${API}/links/:id`, async () => {
        deletes++;
        await gate;
        return new Response(null, { status: 204 });
      }),
    );
    void fake;
    await userEvent.click(await screen.findByRole("button", { name: "Delete Docs" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete link" }));
    const busy = await within(dialog).findByRole("button", { name: "Deleting…" });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    release();
    await screen.findByText("Link deleted.");
    expect(deletes).toBe(1);
  });

  it("keeps showing the current page while the next one loads (no loading flash)", async () => {
    const many = Array.from({ length: 30 }, (_, i) => makeLink({ code: `c${String(i).padStart(2, "0")}` }));
    const fake = fakeLinksApi(many);
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    server.use(meSignedIn(), ...fake.handlers);
    renderApp("/app/links");
    await screen.findByText("30 links · Page 1 of 2");

    server.use(
      http.get(`${API}/links`, async (info) => {
        await gate;
        const [handler] = fake.handlers;
        return (await handler!.run({ request: info.request, requestId: info.requestId }))?.response;
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(screen.queryByRole("status", { name: "Loading links" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "c00" })).toBeInTheDocument(); // still page 1
    release();
    expect(await screen.findByText("30 links · Page 2 of 2")).toBeInTheDocument();
  });
});

describe("Copy", () => {
  it("copies the short_url exactly as the API sent it", async () => {
    const link = makeLink({ code: "docs1", title: "Docs", short_url: "https://go.example.test/docs1" });
    setup([link]);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Copy short link Docs" }));
    expect(await navigator.clipboard.readText()).toBe("https://go.example.test/docs1");
    expect(screen.getByRole("button", { name: "Copy short link Docs" })).toHaveTextContent("Copied");
  });
});
