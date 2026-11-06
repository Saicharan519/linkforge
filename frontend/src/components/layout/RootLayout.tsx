import { useEffect, useRef } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { setUnauthorizedHandler } from "@/api/client";
import { SESSION_KEY } from "@/hooks/useSession";

/**
 * Wires the API client's 401 rule to the router and the query cache:
 * the cache is emptied (nothing of the old user may stay visible), and if the user was inside the
 * app they are sent to /login with the page they were on, so they return there after signing in.
 */
export function RootLayout() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  // The handler runs later, outside React: it needs the page the user is on NOW, not the one
  // from when it was registered.
  const here = useRef(location);
  useEffect(() => {
    here.current = location;
  });

  useEffect(() => {
    setUnauthorizedHandler(() => {
      queryClient.clear();
      queryClient.setQueryData(SESSION_KEY, null);
      const { pathname, search } = here.current;
      if (pathname.startsWith("/app")) {
        navigate("/login", { replace: true, state: { from: pathname + search } });
      }
    });
    return () => setUnauthorizedHandler(null);
  }, [queryClient, navigate]);

  return <Outlet />;
}
