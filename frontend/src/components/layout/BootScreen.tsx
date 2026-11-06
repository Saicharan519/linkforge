/**
 * Shown while the first "who am I?" check runs. A route with a node travelling along it:
 * the same signature as everywhere else, quiet, and static for reduced-motion users.
 */
export function BootScreen() {
  return (
    <div role="status" aria-live="polite" className="grid min-h-screen place-items-center">
      <div className="flex w-64 flex-col items-center gap-3">
        <div className="relative h-1 w-full rounded-full bg-line-soft" aria-hidden="true">
          <span className="lf-skeleton absolute -top-1.5 left-0 size-4 rounded-full border-2 border-line bg-signal" />
        </div>
        <p className="plate-label">Checking your session</p>
      </div>
    </div>
  );
}
