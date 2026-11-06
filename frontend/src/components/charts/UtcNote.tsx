/** Required wherever daily counts are shown (frontend/CLAUDE.md). */
export function UtcNote() {
  return <p className="text-sm text-muted-foreground">Days are UTC. Counts may undercount during outages.</p>;
}
