/** A big number with a small label. */
export function StatPlate({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="plate p-5">
      <p className="plate-label">{label}</p>
      <p className="tabular mt-2 text-4xl font-extrabold leading-none">{value}</p>
      {hint && <p className="mt-2 text-sm text-muted-foreground">{hint}</p>}
    </div>
  );
}
