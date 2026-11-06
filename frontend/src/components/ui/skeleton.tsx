import { cn } from "@/lib/utils";

/** A quiet placeholder block. aria-hidden: the surrounding region carries the "loading" label. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("lf-skeleton h-4 w-full", className)} />;
}
