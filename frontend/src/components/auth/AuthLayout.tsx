import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Logo } from "@/components/layout/Logo";
import { ThemeToggle } from "@/components/layout/ThemeToggle";

/** Frame shared by sign-in and register: logo, one plate, a link to the other screen. */
export function AuthLayout({
  title,
  intro,
  children,
  footer,
}: {
  title: string;
  intro?: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between px-4 py-3 md:px-8">
        <Link to="/" aria-label="LinkForge home">
          <Logo />
        </Link>
        <ThemeToggle />
      </header>
      <main className="grid flex-1 place-items-center px-4 pb-16">
        <div className="w-full max-w-md">
          <div className="plate p-6 md:p-8">
            {/* the signature in small: a route that ends in a Signal node */}
            <div aria-hidden="true" className="mb-5 flex items-center">
              <span className="h-0.5 w-12 bg-line" />
              <span className="size-3.5 rounded-full border-2 border-line bg-signal" />
            </div>
            <h1 className="text-3xl font-extrabold tracking-tight">{title}</h1>
            {intro && <p className="mt-2 text-muted-foreground">{intro}</p>}
            <div className="mt-6">{children}</div>
          </div>
          <p className="mt-5 text-center text-sm text-muted-foreground">{footer}</p>
        </div>
      </main>
    </div>
  );
}

/** A form-level message (a failure that belongs to no single field, or a success notice). */
export function FormMessage({ tone, children }: { tone: "error" | "ok"; children: ReactNode }) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={
        tone === "error"
          ? "rounded-lg border-2 border-stop bg-plate p-3 text-sm font-semibold text-stop"
          : "rounded-lg border-2 border-go bg-plate p-3 text-sm font-semibold text-go"
      }
    >
      {children}
    </div>
  );
}
