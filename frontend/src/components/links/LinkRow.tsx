import { ChartColumn, Pencil, Power, Trash2 } from "lucide-react";
import { Link as RouterLink } from "react-router-dom";
import type { Link } from "@/api/types";
import { Button } from "@/components/ui/button";
import { formatCount, formatDateTime } from "@/lib/format";
import { CopyButton } from "./CopyButton";
import { StatusBadge } from "./StatusBadge";

/** One link as a plate. The same markup reflows from a stack (phone) to a row (wide screens). */
export function LinkRow({
  link,
  busy,
  onEdit,
  onToggle,
  onDelete,
}: {
  link: Link;
  busy: boolean;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const name = link.title ?? link.code;
  return (
    <li className="plate p-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-lg font-extrabold leading-tight">{name}</h2>
            <StatusBadge status={link.status} />
          </div>
          <p className="tabular break-all text-sm text-route">{link.short_url}</p>
          <p className="truncate text-sm text-muted-foreground" title={link.destination_url}>
            {link.destination_url}
          </p>
          <p className="text-xs text-muted-foreground">
            Created {formatDateTime(link.created_at)}
            {link.expires_at && <> · Expires {formatDateTime(link.expires_at)}</>}
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <p className="mr-2 text-right">
            <span className="tabular text-2xl font-extrabold">{formatCount(link.total_clicks)}</span>
            <span className="plate-label ml-1">clicks</span>
          </p>
          <CopyButton text={link.short_url} label={`short link ${name}`} />
          <Button asChild variant="secondary" size="sm">
            <RouterLink to={`/app/links/${link.id}`} aria-label={`Analytics for ${name}`}>
              <ChartColumn aria-hidden="true" /> Analytics
            </RouterLink>
          </Button>
          <Button variant="secondary" size="sm" onClick={onEdit} disabled={busy} aria-label={`Edit ${name}`}>
            <Pencil aria-hidden="true" /> Edit
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={onToggle}
            disabled={busy}
            aria-label={`${link.is_active ? "Disable" : "Enable"} ${name}`}
          >
            <Power aria-hidden="true" /> {link.is_active ? "Disable" : "Enable"}
          </Button>
          <Button variant="destructive" size="sm" onClick={onDelete} disabled={busy} aria-label={`Delete ${name}`}>
            <Trash2 aria-hidden="true" /> Delete
          </Button>
        </div>
      </div>
    </li>
  );
}
