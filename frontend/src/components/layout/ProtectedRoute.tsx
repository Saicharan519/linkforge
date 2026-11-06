import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useSession } from "@/hooks/useSession";
import { BootScreen } from "./BootScreen";

/** Everything under /app needs a signed-in user; others are sent to /login and returned after. */
export function ProtectedRoute() {
  const session = useSession();
  const location = useLocation();

  if (session.isPending) return <BootScreen />;

  if (session.isError) {
    // Not "signed out" (that resolves to null): the server could not be reached or failed.
    return (
      <div role="alert" className="mx-auto mt-24 max-w-md plate p-6">
        <h1 className="text-xl font-extrabold">Can't check your session</h1>
        <p className="mt-2 text-muted-foreground">
          The server did not answer. Reload the page to try again.
        </p>
      </div>
    );
  }

  if (!session.data) {
    const from = location.pathname + location.search;
    return <Navigate to="/login" replace state={{ from }} />;
  }

  return <Outlet />;
}
