import { Link } from "react-router-dom";
import { Logo } from "@/components/layout/Logo";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { RouteTrace } from "@/components/landing/RouteTrace";
import { Button } from "@/components/ui/button";
import { useSession } from "@/hooks/useSession";

const FEATURES = [
  {
    label: "Links",
    title: "Short links you control",
    body: "Pick your own alias or take a generated code. Set an expiry date, switch a link off, or delete it.",
  },
  {
    label: "Counts",
    title: "Totals without the bots",
    body: "Crawlers and scripts are recognised and kept out of your numbers.",
  },
  {
    label: "Breakdowns",
    title: "Where the clicks come from",
    body: "Clicks per day for 7, 30 or 90 days, plus referrers, devices and browsers.",
  },
];

export default function Landing() {
  const session = useSession();
  const signedIn = Boolean(session.data);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="mx-auto flex w-full max-w-5xl items-center justify-between px-4 py-4 md:px-8">
        <Link to="/" aria-label="LinkForge home">
          <Logo />
        </Link>
        <nav aria-label="Account" className="flex items-center gap-2">
          <ThemeToggle />
          {signedIn ? (
            <Button asChild size="sm">
              <Link to="/app">Open dashboard</Link>
            </Button>
          ) : (
            <>
              <Button asChild variant="ghost" size="sm">
                <Link to="/login">Sign in</Link>
              </Button>
              <Button asChild size="sm">
                <Link to="/register">Create account</Link>
              </Button>
            </>
          )}
        </nav>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 md:px-8">
        <section className="pt-10 pb-12 md:pt-20 md:pb-16">
          <h1 className="max-w-3xl text-4xl font-extrabold leading-[1.05] tracking-tight md:text-6xl">
            Your link redirects first. The counting happens after.
          </h1>
          <p className="mt-6 max-w-2xl text-lg text-muted-foreground">
            LinkForge shortens a URL and sends every visitor straight to it. The click is counted a
            moment later, so counting never slows a redirect.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            {signedIn ? (
              <Button asChild size="lg">
                <Link to="/app">Open your dashboard</Link>
              </Button>
            ) : (
              <>
                <Button asChild size="lg">
                  <Link to="/register">Create an account</Link>
                </Button>
                <Button asChild variant="secondary" size="lg">
                  <Link to="/login">Sign in</Link>
                </Button>
              </>
            )}
          </div>
        </section>

        <section aria-label="How it works" className="pb-14">
          <RouteTrace />
        </section>

        <section aria-label="Features" className="grid gap-4 pb-20 md:grid-cols-3">
          {FEATURES.map((f) => (
            <article key={f.title} className="plate p-6">
              <p className="plate-label">{f.label}</p>
              <h2 className="mt-2 text-xl font-extrabold leading-tight">{f.title}</h2>
              <p className="mt-2 text-muted-foreground">{f.body}</p>
            </article>
          ))}
        </section>
      </main>

      <footer className="border-t-2 border-line-soft">
        <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center justify-between gap-2 px-4 py-6 text-sm text-muted-foreground md:px-8">
          <p>Counts can undercount during outages. Days are UTC.</p>
          <p>LinkForge</p>
        </div>
      </footer>
    </div>
  );
}
