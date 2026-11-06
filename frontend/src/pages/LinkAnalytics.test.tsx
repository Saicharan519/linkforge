import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, describe, expect, it } from "vitest";
import { makeLinkAnalytics, makeSeries } from "@/test/analyticsData";
import { makeLink } from "@/test/fakeApi";
import { server } from "@/test/server";
import { API, apiError, meSignedIn, renderApp } from "@/test/utils";

beforeAll(() => {
  process.env.TZ = "America/Los_Angeles";
});

const link = makeLink({ id: "L1", code: "docs1", title: "Docs", total_clicks: 1500, short_url: "https://go.example/docs1" });

function setup(url = "/app/links/L1") {
  const ranges: string[] = [];
  server.use(
    meSignedIn(),
    http.get(`${API}/links/L1`, () => HttpResponse.json(link)),
    http.get(`${API}/links/L1/analytics`, ({ request }) => {
      const range = new URL(request.url).searchParams.get("range") ?? "";
      ranges.push(range);
      return HttpResponse.json(makeLinkAnalytics(range === "7d" ? 7 : range === "90d" ? 90 : 30));
    }),
  );
  return { ranges, ...renderApp(url) };
}

describe("Link analytics", () => {
  it("shows the link, defaults to 30 days and asks the API for exactly that", async () => {
    const { ranges } = setup();
    expect(await screen.findByRole("heading", { name: "Docs" })).toBeInTheDocument();
    expect(screen.getByText("https://go.example/docs1")).toBeInTheDocument();
    expect(await screen.findByText("Clicks, last 30 days")).toBeInTheDocument();
    expect(ranges).toEqual(["30d"]);
    expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("link", { name: /all links/i })).toHaveAttribute("href", "/app/links");
  });

  it("shows the range total and the all-time total side by side", async () => {
    setup();
    expect((await screen.findByText("Clicks, last 30 days")).parentElement).toHaveTextContent("9");
    expect(screen.getByText("Clicks, all time").parentElement).toHaveTextContent("1,500");
  });

  it("switches range, asks the API again, and keeps the choice in the URL", async () => {
    const { ranges, router } = setup();
    await screen.findByText("Clicks, last 30 days");

    await userEvent.click(screen.getByRole("button", { name: "7 days" }));
    expect(await screen.findByText("Clicks, last 7 days")).toBeInTheDocument();
    expect(ranges.at(-1)).toBe("7d");
    expect(router.state.location.search).toBe("?range=7d");
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("img", { name: /over 7 days/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "90 days" }));
    expect(await screen.findByText("Clicks, last 90 days")).toBeInTheDocument();
    expect(ranges.at(-1)).toBe("90d");

    await userEvent.click(screen.getByRole("button", { name: "30 days" }));
    await waitFor(() => expect(router.state.location.search).toBe(""));
  });

  it("reads the range from the URL and ignores junk", async () => {
    const first = setup("/app/links/L1?range=90d");
    await screen.findByText("Clicks, last 90 days");
    expect(first.ranges).toEqual(["90d"]);
  });

  it("falls back to 30 days for an unknown range", async () => {
    const { ranges } = setup("/app/links/L1?range=1y");
    await screen.findByText("Clicks, last 30 days");
    expect(ranges).toEqual(["30d"]);
  });

  it("draws the bars and lists the days exactly as the API sent them", async () => {
    setup("/app/links/L1?range=7d");
    await screen.findByText("Clicks, last 7 days");
    const table = await screen.findByRole("table", { name: /per UTC day/ });
    const days = within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0]!.textContent);
    expect(days).toEqual(["2026-03-08", "2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13", "2026-03-14"]);
    // only the last day had clicks; Recharts draws no bar for a zero day
    expect(document.querySelectorAll(".recharts-bar-rectangle").length).toBe(1);
  });

  it("lists referrers, devices and browsers with their counts", async () => {
    setup();
    await screen.findByText("Clicks, last 30 days");

    const referrers = screen.getByRole("region", { name: "Referrers" });
    expect(within(referrers).getByText("news.example.com")).toBeInTheDocument();
    expect(within(referrers).getByText("(direct)")).toBeInTheDocument();

    const devices = screen.getByRole("region", { name: "Devices" });
    expect(within(devices).getByText("Desktop").parentElement).toHaveTextContent("7");
    expect(within(devices).getByText("Mobile")).toBeInTheDocument();

    const browsers = screen.getByRole("region", { name: "Browsers" });
    expect(within(browsers).getByText("Chrome").parentElement).toHaveTextContent("8");
    expect(screen.getByText("Days are UTC. Counts may undercount during outages.")).toBeInTheDocument();
  });

  it("says so when a breakdown has nothing in it", async () => {
    server.use(meSignedIn(), http.get(`${API}/links/L1`, () => HttpResponse.json(link)));
    server.use(
      http.get(`${API}/links/L1/analytics`, () =>
        HttpResponse.json(makeLinkAnalytics(30, { total: 0, series: makeSeries(30), referrers: [], devices: [], browsers: [] })),
      ),
    );
    renderApp("/app/links/L1");
    expect(await screen.findByText("No clicks in this period yet.")).toBeInTheDocument();
    expect(screen.getAllByText("Nothing recorded in this period.")).toHaveLength(3);
  });

  it("shows a loading state first", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    server.use(
      meSignedIn(),
      http.get(`${API}/links/L1`, () => HttpResponse.json(link)),
      http.get(`${API}/links/L1/analytics`, async () => {
        await gate;
        return HttpResponse.json(makeLinkAnalytics());
      }),
    );
    renderApp("/app/links/L1");
    expect(await screen.findByRole("status", { name: "Loading analytics" })).toBeInTheDocument();
    release();
    expect(await screen.findByText("Clicks, last 30 days")).toBeInTheDocument();
  });

  it("keeps the old numbers on screen while another range loads", async () => {
    setup();
    await screen.findByText("Clicks, last 30 days");

    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    server.use(
      http.get(`${API}/links/L1/analytics`, async () => {
        await gate;
        return HttpResponse.json(makeLinkAnalytics(7));
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "7 days" }));
    expect(screen.queryByRole("status", { name: "Loading analytics" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Referrers" })).toBeInTheDocument();
    release();
    await screen.findByText("Clicks, last 7 days");
  });

  it("shows 'not found' for a link that is not yours or does not exist", async () => {
    server.use(
      meSignedIn(),
      http.get(`${API}/links/nope`, () => apiError(404, "not_found")),
      http.get(`${API}/links/nope/analytics`, () => apiError(404, "not_found")),
    );
    renderApp("/app/links/nope");
    expect(await screen.findByRole("heading", { name: "Link not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /all links/i })).toBeInTheDocument();
  });

  it("shows an error with a retry that works", async () => {
    server.use(
      meSignedIn(),
      http.get(`${API}/links/L1`, () => HttpResponse.json(link)),
      http.get(`${API}/links/L1/analytics`, () => apiError(503, "unavailable")),
    );
    renderApp("/app/links/L1");
    expect(await screen.findByRole("alert")).toHaveTextContent(/unavailable right now/i);

    server.use(http.get(`${API}/links/L1/analytics`, () => HttpResponse.json(makeLinkAnalytics())));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Clicks, last 30 days")).toBeInTheDocument();
  });
});
