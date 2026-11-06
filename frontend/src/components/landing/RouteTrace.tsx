import type { CSSProperties } from "react";

interface Station {
  title: string;
  detail: string;
  mono?: boolean;
  signal?: boolean;
}

// What really happens (ARCHITECTURE §2): the API answers the redirect from cache or database and
// replies 302; the click is put on a Redis stream and a worker stores it afterwards.
const REDIRECT: Station[] = [
  { title: "A visitor opens the link", detail: "lf.example/Ab3xK9", mono: true },
  { title: "LinkForge answers", detail: "It finds the destination, cache first, and sends a 302." },
  { title: "The destination loads", detail: "The visitor is gone. Nothing below has to finish first.", signal: true },
];

const AFTERWARDS: Station[] = [
  { title: "The click is queued", detail: "One small message on a Redis stream." },
  { title: "A worker stores it", detail: "Bots are marked. The click is saved to the database." },
  { title: "Your totals update", detail: "Daily counts and breakdowns include it.", signal: true },
];

function Lane({ label, stations, after, delay }: { label: string; stations: Station[]; after?: boolean; delay: number }) {
  return (
    <div>
      <h3 className="plate-label mb-4">{label}</h3>
      <ol
        className={after ? "trace trace-after" : "trace"}
        style={{ "--lane-delay": `${delay}s` } as CSSProperties}
      >
        {stations.map((s, i) => (
          <li key={s.title} data-signal={s.signal ? "" : undefined} style={{ "--i": i } as CSSProperties}>
            <p className="font-extrabold leading-tight">{s.title}</p>
            <p className={s.mono ? "tabular mt-1 text-sm text-route" : "mt-1 text-sm text-muted-foreground"}>{s.detail}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** The Landing signature: one click, drawn as a route. Illustration only, not live data. */
export function RouteTrace() {
  return (
    <figure aria-label="How one click travels" className="plate p-6 md:p-8">
      <figcaption className="plate-label mb-6">Example, not live data</figcaption>
      <div className="space-y-10">
        <Lane label="The redirect" stations={REDIRECT} delay={0} />
        <Lane label="Afterwards" stations={AFTERWARDS} after delay={1.4} />
      </div>
    </figure>
  );
}
