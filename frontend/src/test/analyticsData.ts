import type { DayCount, LinkAnalytics, Overview } from "@/api/types";

/** `days` consecutive UTC calendar-date strings ending at `last` (default 2026-03-14), oldest first. */
export function makeSeries(days: number, clicksOf: (i: number) => number = () => 0, last = "2026-03-14"): DayCount[] {
  const [y, m, d] = last.split("-").map(Number) as [number, number, number];
  const end = Date.UTC(y, m - 1, d);
  return Array.from({ length: days }, (_, i) => {
    const t = new Date(end - (days - 1 - i) * 86_400_000);
    const day = `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, "0")}-${String(t.getUTCDate()).padStart(2, "0")}`;
    return { day, clicks: clicksOf(i) };
  });
}

export function makeOverview(overrides: Partial<Overview> = {}): Overview {
  const series = makeSeries(30, (i) => (i === 29 ? 5 : i === 28 ? 2 : 0));
  return {
    total_links: 3,
    total_clicks: 7,
    series,
    top_links: [
      { id: "id-a", code: "alpha1", title: "Alpha", clicks: 5 },
      { id: "id-b", code: "beta22", title: null, clicks: 2 },
    ],
    ...overrides,
  };
}

export function makeLinkAnalytics(days = 30, overrides: Partial<LinkAnalytics> = {}): LinkAnalytics {
  return {
    total: 9,
    series: makeSeries(days, (i) => (i === days - 1 ? 9 : 0)),
    referrers: [
      { host: "news.example.com", clicks: 6 },
      { host: "(direct)", clicks: 3 },
    ],
    devices: [
      { device_type: "desktop", clicks: 7 },
      { device_type: "mobile", clicks: 2 },
    ],
    browsers: [
      { browser: "Chrome", clicks: 8 },
      { browser: "Firefox", clicks: 1 },
    ],
    ...overrides,
  };
}
