import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, describe, expect, it } from "vitest";
import { makeOverview, makeSeries } from "@/test/analyticsData";
import { server } from "@/test/server";
import { API, apiError, meSignedIn, renderApp } from "@/test/utils";

// A time zone behind UTC: any code that turns "2026-03-01" into a local Date would show Feb 28.
beforeAll(() => {
  process.env.TZ = "America/Los_Angeles";
});

const overviewHandler = (data = makeOverview(), seen?: string[]) =>
  http.get(`${API}/analytics/overview`, ({ request }) => {
    seen?.push(new URL(request.url).searchParams.get("range") ?? "");
    return HttpResponse.json(data);
  });

describe("Dashboard", () => {
  it("shows a loading state, then the totals, the chart and the top links", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const seen: string[] = [];
    server.use(
      meSignedIn(),
      http.get(`${API}/analytics/overview`, async ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("range") ?? "");
        await gate;
        return HttpResponse.json(makeOverview());
      }),
    );
    renderApp("/app");
    expect(await screen.findByRole("status", { name: "Loading your numbers" })).toBeInTheDocument();
    release();

    expect(await screen.findByText("Clicks, last 30 days")).toBeInTheDocument();
    expect(seen).toEqual(["30d"]);
    expect(screen.getByText("Links", { selector: ".plate-label" }).parentElement).toHaveTextContent("3");
    expect(screen.getByText("Clicks, last 30 days").parentElement).toHaveTextContent("7");

    const top = screen.getByRole("region", { name: "Top links" });
    const items = within(top).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]!).getByRole("link", { name: "Alpha" })).toHaveAttribute("href", "/app/links/id-a");
    expect(within(items[1]!).getByRole("link", { name: "beta22" })).toBeInTheDocument(); // no title -> code
    expect(screen.getByText("Days are UTC. Counts may undercount during outages.")).toBeInTheDocument();
  });

  it("draws a bar for each day that had clicks and describes the chart in words", async () => {
    server.use(meSignedIn(), overviewHandler());
    const { container } = renderApp("/app");
    const chart = await screen.findByRole("img", { name: "Clicks per day: 7 clicks over 30 days" });
    expect(chart).toBeInTheDocument();
    await screen.findByText("Clicks, last 30 days");
    // two days had clicks (2 and 5); Recharts draws no bar for a zero day
    expect(container.querySelectorAll(".recharts-bar-rectangle").length).toBe(2);
  });

  it("gives the same numbers as a table, with day labels exactly as the API sent them", async () => {
    server.use(
      meSignedIn(),
      overviewHandler(makeOverview({ series: makeSeries(30, (i) => i, "2026-03-14") })),
    );
    renderApp("/app");
    const table = await screen.findByRole("table", { name: /Clicks per day, per UTC day/ });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(31); // header + 30 days
    // The series ends on 2026-03-14 and starts 29 days earlier. In Los Angeles a local Date built
    // from "2026-03-01" would read Feb 28, so these exact strings catch any date conversion.
    expect(within(table).getByText("2026-02-13")).toBeInTheDocument();
    expect(within(table).getByText("2026-03-01")).toBeInTheDocument();
    expect(within(table).getByText("2026-03-14")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("2026-02-13")).toBeInTheDocument(); // oldest first
    expect(within(rows[30]!).getByText("2026-03-14")).toBeInTheDocument(); // today last
  });

  it("invites the first link when the account has none", async () => {
    server.use(meSignedIn(), overviewHandler(makeOverview({ total_links: 0, total_clicks: 0, top_links: [] })));
    renderApp("/app");
    expect(await screen.findByRole("heading", { name: "No links yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Links" })).toHaveAttribute("href", "/app/links");
  });

  it("says so when there are links but no clicks yet", async () => {
    server.use(
      meSignedIn(),
      overviewHandler(makeOverview({ total_clicks: 0, series: makeSeries(30), top_links: [] })),
    );
    renderApp("/app");
    expect(await screen.findByText("No clicks in this period yet.")).toBeInTheDocument();
    expect(screen.getByText("Nothing recorded in this period.")).toBeInTheDocument();
  });

  it("shows an error with a retry that works", async () => {
    server.use(meSignedIn(), http.get(`${API}/analytics/overview`, () => apiError(503, "unavailable")));
    renderApp("/app");
    expect(await screen.findByRole("alert")).toHaveTextContent(/unavailable right now/i);

    server.use(overviewHandler());
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Clicks, last 30 days")).toBeInTheDocument();
  });
});
