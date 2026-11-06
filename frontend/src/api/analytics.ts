import { api } from "./client";
import type { LinkAnalytics, Overview, RangeName } from "./types";

export const getOverview = (range: RangeName, signal?: AbortSignal) =>
  api<Overview>(`/analytics/overview?range=${range}`, { signal });

export const getLinkAnalytics = (id: string, range: RangeName, signal?: AbortSignal) =>
  api<LinkAnalytics>(`/links/${encodeURIComponent(id)}/analytics?range=${range}`, { signal });
