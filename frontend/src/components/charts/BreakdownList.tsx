import { formatCount } from "@/lib/format";

export interface BreakdownRow {
  label: string;
  clicks: number;
}

/** A ranked list: a label, a bar scaled to the largest row, and the exact number (never colour alone). */
export function BreakdownList({ title, rows }: { title: string; rows: BreakdownRow[] }) {
  const max = Math.max(1, ...rows.map((r) => r.clicks));
  return (
    <section aria-label={title} className="plate p-5">
      <h3 className="plate-label">{title}</h3>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">Nothing recorded in this period.</p>
      ) : (
        <ul className="mt-3 space-y-3">
          {rows.map((row) => (
            <li key={row.label}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="truncate font-bold" title={row.label}>
                  {row.label}
                </span>
                <span className="tabular shrink-0">{formatCount(row.clicks)}</span>
              </div>
              <div aria-hidden="true" className="mt-1 h-2 rounded-full bg-muted">
                <div className="h-2 rounded-full bg-route" style={{ width: `${(row.clicks / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
