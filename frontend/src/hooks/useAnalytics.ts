import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getLinkAnalytics, getOverview } from "@/api/analytics";
import type { RangeName } from "@/api/types";

export function useOverview(range: RangeName) {
  return useQuery({
    queryKey: ["analytics", "overview", range],
    queryFn: ({ signal }) => getOverview(range, signal),
  });
}

export function useLinkAnalytics(id: string, range: RangeName) {
  return useQuery({
    queryKey: ["analytics", "link", id, range],
    queryFn: ({ signal }) => getLinkAnalytics(id, range, signal),
    // Switching 7d -> 30d keeps the old numbers on screen until the new ones arrive.
    placeholderData: keepPreviousData,
  });
}
