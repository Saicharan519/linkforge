import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { DayCount } from "@/api/types";
import { ChartTooltip } from "./ChartTooltip";
import { formatCount, shortDay } from "@/lib/format";

/**
 * Clicks per UTC day. The picture is for sighted users; the table under it carries the same
 * numbers for everyone else (and for anyone who wants exact values).
 */
export function ClicksChart({ series, label }: { series: DayCount[]; label: string }) {
  const total = series.reduce((sum, d) => sum + d.clicks, 0);
  const lastDay = series.at(-1)?.day;

  return (
    <div>
      <div role="img" aria-label={`${label}: ${formatCount(total)} clicks over ${series.length} days`} className="h-60 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={series} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid vertical={false} stroke="var(--line-soft)" />
            <XAxis
              dataKey="day"
              tickFormatter={shortDay}
              tickLine={false}
              axisLine={{ stroke: "var(--line)", strokeWidth: 2 }}
              minTickGap={24}
              tick={{ fill: "var(--muted-foreground)", fontSize: 12, fontFamily: "var(--font-mono)" }}
            />
            <YAxis
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              width={44}
              tick={{ fill: "var(--muted-foreground)", fontSize: 12, fontFamily: "var(--font-mono)" }}
            />
            <Tooltip cursor={{ fill: "var(--muted)" }} content={<ChartTooltip lastDay={lastDay} />} />
            <Bar dataKey="clicks" fill="var(--route)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {total === 0 && <p className="mt-2 text-sm text-muted-foreground">No clicks in this period yet.</p>}
      <details className="mt-3">
        <summary className="cursor-pointer text-sm font-bold">Show the numbers as a table</summary>
        <table className="mt-2 w-full max-w-sm text-sm">
          <caption className="sr-only">{label}, per UTC day</caption>
          <thead>
            <tr className="text-left">
              <th scope="col" className="plate-label py-1">
                Day (UTC)
              </th>
              <th scope="col" className="plate-label py-1 text-right">
                Clicks
              </th>
            </tr>
          </thead>
          <tbody>
            {series.map((d) => (
              <tr key={d.day} className="border-t border-line-soft">
                <td className="tabular py-1">{d.day}</td>
                <td className="tabular py-1 text-right">{formatCount(d.clicks)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
