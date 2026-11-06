import { Link as RouterLink } from "react-router-dom";
import { BreakdownList } from "@/components/charts/BreakdownList";
import { ClicksChart } from "@/components/charts/ClicksChart";
import { UtcNote } from "@/components/charts/UtcNote";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatPlate } from "@/components/ui/stat-plate";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useOverview } from "@/hooks/useAnalytics";
import { describeError } from "@/lib/forms";
import { formatCount } from "@/lib/format";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const RANGE = "30d";

export default function Dashboard() {
  useDocumentTitle("Dashboard");
  const overview = useOverview(RANGE);
  const data = overview.data;

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-extrabold tracking-tight">Dashboard</h1>

      {overview.isPending ? (
        <div role="status" aria-label="Loading your numbers" className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Skeleton className="h-28" />
            <Skeleton className="h-28" />
          </div>
          <Skeleton className="h-72" />
        </div>
      ) : overview.isError ? (
        <ErrorState message={describeError(overview.error)} onRetry={() => void overview.refetch()} />
      ) : data && data.total_links === 0 ? (
        <EmptyState
          title="No links yet"
          body="Create a short link and share it. Its clicks will be counted here."
          action={
            <Button asChild>
              <RouterLink to="/app/links">Go to Links</RouterLink>
            </Button>
          }
        />
      ) : data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <StatPlate label="Links" value={formatCount(data.total_links)} />
            <StatPlate label="Clicks, last 30 days" value={formatCount(data.total_clicks)} hint="Bots are not counted." />
          </div>

          <section aria-label="Clicks per day" className="plate p-5">
            <h2 className="plate-label mb-3">Clicks per day, last 30 days</h2>
            <ClicksChart series={data.series} label="Clicks per day" />
          </section>

          <section aria-label="Top links">
            {data.top_links.length === 0 ? (
              <BreakdownList title="Top links" rows={[]} />
            ) : (
              <div className="plate p-5">
                <h2 className="plate-label">Top links, last 30 days</h2>
                <ol className="mt-3 divide-y divide-line-soft">
                  {data.top_links.map((link) => (
                    <li key={link.id} className="flex items-center justify-between gap-3 py-2">
                      <RouterLink
                        to={`/app/links/${link.id}`}
                        className="min-w-0 truncate font-bold text-route underline-offset-2 hover:underline"
                      >
                        {link.title ?? link.code}
                      </RouterLink>
                      <span className="tabular shrink-0">{formatCount(link.clicks)}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </section>
          <UtcNote />
        </>
      ) : null}
    </div>
  );
}
