import { LayoutDashboard, Link2, LogOut, Settings } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useLogout, useSession } from "@/hooks/useSession";
import { cn } from "@/lib/utils";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { to: "/app", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/app/links", label: "Links", icon: Link2, end: false },
  { to: "/app/settings", label: "Settings", icon: Settings, end: true },
] as const;

/**
 * Signed-in frame. On wide screens the navigation is a vertical route: each screen is a station
 * on the line, the current one is the filled Signal node. On narrow screens it is one row.
 */
export function AppShell() {
  const session = useSession();
  const logout = useLogout();
  const navigate = useNavigate();

  const signOut = () => logout.mutate(undefined, { onSettled: () => navigate("/login", { replace: true }) });

  return (
    <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]">
      <aside className="border-b-2 border-line bg-plate md:sticky md:top-0 md:flex md:h-screen md:flex-col md:border-r-2 md:border-b-0">
        <div className="flex items-center justify-between px-4 py-3 md:px-5 md:py-5">
          <NavLink to="/app" aria-label="LinkForge dashboard">
            <Logo />
          </NavLink>
          <div className="md:hidden">
            <ThemeToggle />
          </div>
        </div>

        <nav aria-label="Main" className="px-4 pb-3 md:flex-1 md:px-5 md:pb-0 md:pt-4">
          <ul className="relative flex gap-1 md:flex-col md:gap-2">
            {/* the route line that the stations sit on (wide screens only) */}
            <span
              aria-hidden="true"
              className="absolute top-3 bottom-3 left-[0.6875rem] hidden w-0.5 bg-line-soft md:block"
            />
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <li key={to} className="relative">
                <NavLink
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-3 rounded-lg px-2 py-2 text-sm font-bold hover:bg-muted",
                      isActive ? "text-foreground" : "text-muted-foreground",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <span
                        aria-hidden="true"
                        className={cn(
                          "relative hidden size-[1.4rem] shrink-0 rounded-full border-2 border-line transition-colors md:block",
                          isActive ? "bg-signal" : "bg-plate",
                        )}
                      />
                      <Icon className="size-4 md:hidden" aria-hidden="true" />
                      {label}
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="hidden border-t-2 border-line-soft p-5 md:block">
          <p className="plate-label">Signed in as</p>
          <p className="mt-1 truncate text-sm font-bold" title={session.data?.email}>
            {session.data?.email}
          </p>
          <div className="mt-3 flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={signOut} disabled={logout.isPending}>
              <LogOut aria-hidden="true" /> Sign out
            </Button>
            <ThemeToggle />
          </div>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="flex items-center justify-between gap-3 border-b-2 border-line-soft px-4 py-2 md:hidden">
          <p className="truncate text-sm font-bold">{session.data?.email}</p>
          <Button variant="secondary" size="sm" onClick={signOut} disabled={logout.isPending}>
            <LogOut aria-hidden="true" /> Sign out
          </Button>
        </header>
        <main className="mx-auto w-full max-w-6xl p-4 md:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
