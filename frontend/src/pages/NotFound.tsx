import { Link } from "react-router-dom";
import { Logo } from "@/components/layout/Logo";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { Button } from "@/components/ui/button";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export default function NotFound() {
  useDocumentTitle("Page not found");
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between px-4 py-3 md:px-8">
        <Link to="/" aria-label="LinkForge home">
          <Logo />
        </Link>
        <ThemeToggle />
      </header>
      <main className="grid flex-1 place-items-center px-4 pb-16">
        <div className="plate max-w-md p-8">
          {/* a route that ends short of its destination */}
          <div aria-hidden="true" className="mb-5 flex items-center">
            <span className="h-0.5 w-12 bg-line" />
            <span className="h-0.5 w-6 border-t-2 border-dashed border-line-soft" />
            <span className="size-3.5 rounded-full border-2 border-line bg-plate" />
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight">Page not found</h1>
          <p className="mt-2 text-muted-foreground">
            There is nothing at this address. A short link opens on its own short-link address, not here.
          </p>
          <Button asChild className="mt-6">
            <Link to="/">Go to the home page</Link>
          </Button>
        </div>
      </main>
    </div>
  );
}
