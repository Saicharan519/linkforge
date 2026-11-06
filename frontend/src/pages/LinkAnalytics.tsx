import { ArrowLeft } from "lucide-react";
import { Link as RouterLink, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import type { RangeName } from "@/api/types";
import { BreakdownList } from "@/components/charts/BreakdownList";
import { ClicksChart } from "@/components/charts/ClicksChart";
import { UtcNote } from "@/components/charts/UtcNote";
import { StatusBadge } from "@/components/links/StatusBadge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatPlate } from "@/components/ui/stat-plate";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useLinkAnalytics } from "@/hooks/useAnalytics";
import { useLink } from "@/hooks/useLinks";
import { describeError } from "@/lib/forms";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const RANGES: { value: RangeName; label: string }[] = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
];
const DEFAULT_RANGE: RangeName = "30d";
const parseRange = (v: string | null): RangeName => RANGES.find((r) => r.value === v)?.value ?? DEFAULT_RANGE;

const DEVICE_LABEL = { desktop: "Desktop", mobile: "Mobile", tablet: "Tablet", other: "Other" } as const;

export default function LinkAnalytics() {
  useDocumentTitle("Link analytics");
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const range = parseRange(params.get("range"));
  const rangeLabel = RANGES.find((r) => r.value === range)!.label;

  const link = useLink(id);
  const stats = useLinkAnalytics(id, range);

  const back = (
    <Button asChild variant="ghost" size="sm" className="-ml-3">
      <RouterLink to="/app/links">
        <ArrowLeft aria-hidden="true" /> All links
      </RouterLink>
    </Button>
  );

  // Not yours and not existing look the same on purpose (the API answers 404 for both).
  if (link.error instanceof ApiError && link.error.status === 404) {
    return (
      <div className="space-y-4">
        {back}
        <EmptyState title="Link not found" body="It may have been deleted, or it is not in your account." />
      </div>
    );
  }

  const setRange = (next: RangeName) => {
    const nextParams = new URLSearchParams(params);
    if (next === DEFAULT_RANGE) nextParams.delete("range");
    else nextParams.set("range", next);
    setParams(nextParams, { replace: true });
  };

  const data = stats.data;

  return (
    <div className="space-y-6">
      <div>
        {back}
        {link.data ? (
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-3xl font-extrabold tracking-tight">{link.data.title ?? link.data.code}</h1>
            <StatusBadge status={link.data.status} />
          </div>
        ) : (
          <h1 className="mt-1 text-3xl font-extrabold tracking-tight">Link analytics</h1>
        )}
        {link.data && (
          <>
            <p className="tabular mt-1 break-all text-route">{link.data.short_url}</p>
            <p className="truncate text-sm text-muted-foreground" title={link.data.destination_url}>
              {link.data.destination_url}
            </p>
          </>
        )}
      </div>

      <div role="group" aria-label="Date range" className="inline-flex gap-1 rounded-lg border-2 border-line bg-plate p-1">
        {RANGES.map((r) => (
          <button
            key={r.value}
            type="button"
            aria-pressed={r.value === range}
            onClick={() => setRange(r.value)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm font-bold",
              r.value === range ? "bg-signal text-signal-foreground" : "text-foreground hover:bg-muted",
            )}
          >
            {r.label}
          </button>
        ))}
      </div>

      {stats.isPending ? (
        <div role="status" aria-label="Loading analytics" className="space-y-4">
          <Skeleton className="h-28" />
          <Skeleton className="h-72" />
        </div>
      ) : stats.isError ? (
        <ErrorState message={describeError(stats.error)} onRetry={() => void stats.refetch()} />
      ) : data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <StatPlate label={`Clicks, last ${rangeLabel}`} value={formatCount(data.total)} hint="Bots are not counted." />
            {link.data && <StatPlate label="Clicks, all time" value={formatCount(link.data.total_clicks)} />}
          </div>

          <section aria-label="Clicks per day" className="plate p-5">
            <h2 className="plate-label mb-3">Clicks per day, last {rangeLabel}</h2>
            <ClicksChart series={data.series} label="Clicks per day" />
          </section>

          <div className="grid gap-4 lg:grid-cols-3">
            <BreakdownList title="Referrers" rows={data.referrers.map((r) => ({ label: r.host, clicks: r.clicks }))} />
            <BreakdownList
              title="Devices"
              rows={data.devices.map((d) => ({ label: DEVICE_LABEL[d.device_type], clicks: d.clicks }))}
            />
            <BreakdownList title="Browsers" rows={data.browsers.map((b) => ({ label: b.browser, clicks: b.clicks }))} />
          </div>
          <UtcNote />
        </>
      ) : null}
    </div>
  );
}
