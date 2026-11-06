import type { ReactNode } from "react";
import { CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

/** Nothing here yet: says what the screen is for and offers the one next step. */
export function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="plate flex flex-col items-start gap-3 p-8">
      {/* the signature: a route that has not gone anywhere yet */}
      <div aria-hidden="true" className="flex items-center">
        <span className="size-3.5 rounded-full border-2 border-line bg-plate" />
        <span className="h-0.5 w-16 bg-line-soft" />
      </div>
      <h2 className="text-xl font-extrabold">{title}</h2>
      <p className="max-w-md text-muted-foreground">{body}</p>
      {action}
    </div>
  );
}

/** A failed load: what happened in plain words and a way to try again. */
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="plate flex flex-col items-start gap-3 border-stop p-6">
      <p className="flex items-start gap-2 font-bold text-stop">
        <CircleAlert className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
        {message}
      </p>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

/** Placeholder rows while a list loads. One labelled status region, the blocks are decoration. */
export function LoadingRows({ label, rows = 4 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-label={label} className="space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="plate space-y-2 p-4">
          <Skeleton className="h-5 w-1/3" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ))}
    </div>
  );
}
