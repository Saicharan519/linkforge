import { CircleCheck, CircleSlash, Clock } from "lucide-react";
import type { LinkStatus } from "@/api/types";

// Colour is never the only signal: every status also has its own icon and word.
const STATUS = {
  active: { label: "Active", icon: CircleCheck, tone: "text-go border-go" },
  disabled: { label: "Disabled", icon: CircleSlash, tone: "text-muted-foreground border-line-soft" },
  expired: { label: "Expired", icon: Clock, tone: "text-stop border-stop" },
} as const;

export function StatusBadge({ status }: { status: LinkStatus }) {
  const { label, icon: Icon, tone } = STATUS[status];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border-2 px-2 py-0.5 text-xs font-bold ${tone}`}>
      <Icon className="size-3.5" aria-hidden="true" />
      {label}
    </span>
  );
}
