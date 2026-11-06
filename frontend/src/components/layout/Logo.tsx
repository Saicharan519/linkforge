import { cn } from "@/lib/utils";

/** The mark is the signature in miniature: two nodes joined by a route. */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-lg font-extrabold tracking-tight", className)}>
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden="true">
        <rect width="32" height="32" rx="7" fill="var(--foreground)" />
        <path
          d="M6 22 H15 L17 10 H26"
          fill="none"
          stroke="var(--background)"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="6" cy="22" r="3.2" fill="#ffc400" />
        <circle cx="26" cy="10" r="3.2" fill="#ffc400" />
      </svg>
      LinkForge
    </span>
  );
}
