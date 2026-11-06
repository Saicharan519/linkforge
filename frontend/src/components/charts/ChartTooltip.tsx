import type { DayCount } from "@/api/types";
import { formatCount } from "@/lib/format";

interface Props {
  active?: boolean;
  payload?: { value?: number; payload?: DayCount }[];
  /** The last day of the series is today (the API's ranges end today), so its count is partial. */
  lastDay: string | undefined;
}

export function ChartTooltip({ active, payload, lastDay }: Props) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="plate px-3 py-2 text-sm">
      {/* the day exactly as the API sent it (UTC date) */}
      <p className="tabular font-bold">{point.day}</p>
      <p>
        {formatCount(point.clicks)} {point.clicks === 1 ? "click" : "clicks"}
        {point.day === lastDay && <span className="text-muted-foreground"> · today so far</span>}
      </p>
    </div>
  );
}
