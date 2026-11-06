import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { routes } from "@/routes";

export const USER = { id: "u-1", email: "ada@example.com", created_at: "2026-01-02T03:04:05Z" };

export const API = "http://localhost:3000/api/v1";

/** What /auth/me answers for a visitor with no session. */
export const meSignedOut = () =>
  http.get(`${API}/auth/me`, () =>
    HttpResponse.json({ error: { code: "unauthorized", message: "sign in" } }, { status: 401 }),
  );

export const meSignedIn = () => http.get(`${API}/auth/me`, () => HttpResponse.json(USER));

/** An API error response as the server sends it. */
export const apiError = (
  status: number,
  code: string,
  message = "x",
  details?: { field: string; message: string }[],
) => HttpResponse.json({ error: { code, message, ...(details ? { details } : {}) } }, { status });

/** Renders the real route tree at `path`, with a fresh query cache that never retries. */
export function renderApp(entry: string | { pathname: string; state?: unknown }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [entry] });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...view, router, queryClient };
}
