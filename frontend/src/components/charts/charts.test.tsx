import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { getLinkAnalytics, getOverview } from "@/api/analytics";
import { shortDay } from "@/lib/format";
import { makeSeries } from "@/test/analyticsData";
import { server } from "@/test/server";
import { BreakdownList } from "./BreakdownList";
import { ChartTooltip } from "./ChartTooltip";
import { ClicksChart } from "./ClicksChart";

describe("ChartTooltip", () => {
  const point = { day: "2026-03-14", clicks: 5 };

  it("shows the day as sent, the count, and marks the last day as partial", () => {
    render(<ChartTooltip active payload={[{ payload: point }]} lastDay="2026-03-14" />);
    expect(screen.getByText("2026-03-14")).toBeInTheDocument();
    expect(screen.getByText(/5 clicks/)).toBeInTheDocument();
    expect(screen.getByText(/today so far/)).toBeInTheDocument();
  });

  it("does not mark an earlier day, and says 'click' for one", () => {
    render(<ChartTooltip active payload={[{ payload: { day: "2026-03-13", clicks: 1 } }]} lastDay="2026-03-14" />);
    expect(screen.getByText(/1 click$/)).toBeInTheDocument();
    expect(screen.queryByText(/today so far/)).not.toBeInTheDocument();
  });

  it("renders nothing when inactive", () => {
    const { container } = render(<ChartTooltip active={false} payload={[{ payload: point }]} lastDay="x" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("shortDay and the axis", () => {
  it("drops the year by slicing the string", () => {
    expect(shortDay("2026-03-14")).toBe("03-14");
  });

  it("labels the x axis with short days", async () => {
    const { container } = render(<ClicksChart series={makeSeries(7, () => 1)} label="Clicks per day" />);
    const read = () =>
      [...container.querySelectorAll(".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    await waitFor(() => expect(read().length).toBeGreaterThan(0));
    for (const tick of read()) expect(tick).toMatch(/^\d\d-\d\d$/);
  });
});

describe("BreakdownList", () => {
  it("scales each bar to the largest row", () => {
    const { container } = render(
      <BreakdownList
        title="Browsers"
        rows={[
          { label: "Chrome", clicks: 8 },
          { label: "Firefox", clicks: 2 },
        ]}
      />,
    );
    const widths = [...container.querySelectorAll<HTMLElement>("li .bg-route")].map((b) => b.style.width);
    expect(widths).toEqual(["100%", "25%"]);
  });

  it("does not divide by zero when every row is 0", () => {
    const { container } = render(<BreakdownList title="Browsers" rows={[{ label: "Chrome", clicks: 0 }]} />);
    expect(container.querySelector<HTMLElement>("li .bg-route")!.style.width).toBe("0%");
  });
});

describe("analytics requests", () => {
  const seen: string[] = [];
  const record = http.get("http://localhost:3000/api/v1/*", ({ request }) => {
    const url = new URL(request.url);
    seen.push(url.pathname + url.search);
    return HttpResponse.json({});
  });

  it.each(["7d", "30d", "90d"] as const)("asks the overview for range=%s", async (range) => {
    seen.length = 0;
    server.use(record);
    await getOverview(range);
    expect(seen).toEqual([`/api/v1/analytics/overview?range=${range}`]);
  });

  it.each(["7d", "30d", "90d"] as const)("asks one link for range=%s, with its id escaped", async (range) => {
    seen.length = 0;
    server.use(record);
    await getLinkAnalytics("a/b c", range);
    expect(seen).toEqual([`/api/v1/links/a%2Fb%20c/analytics?range=${range}`]);
  });
});
