import { http, HttpResponse } from "msw";
import type { Link, LinkCreateBody, LinkPatchBody } from "@/api/types";
import { API, apiError } from "./utils";

let counter = 0;

export function makeLink(overrides: Partial<Link> = {}): Link {
  counter++;
  const code = overrides.code ?? `code${String(counter).padStart(3, "0")}`;
  return {
    id: `id-${counter}`,
    code,
    short_url: `https://go.example/${code}`,
    destination_url: `https://example.com/page-${counter}`,
    title: null,
    is_active: true,
    expires_at: null,
    created_at: "2026-03-01T10:00:00Z",
    updated_at: "2026-03-01T10:00:00Z",
    status: "active",
    total_clicks: 0,
    ...overrides,
  };
}

export interface Recorded {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
  csrf: string | null;
}

/**
 * An in-memory stand-in for /api/v1/links, served through MSW at the fetch boundary. It filters,
 * pages, creates, patches and deletes like the real one (as far as the screens can tell), and
 * records every request so tests can assert what the browser sent.
 */
export function fakeLinksApi(initial: Link[]) {
  const state = { links: [...initial], requests: [] as Recorded[], createError: undefined as Response | undefined };

  const record = async (request: Request) => {
    const url = new URL(request.url);
    let body: unknown;
    if (request.method !== "GET" && request.method !== "DELETE") body = await request.json().catch(() => undefined);
    state.requests.push({
      method: request.method,
      path: url.pathname.replace("/api/v1", ""),
      query: url.searchParams,
      body,
      csrf: request.headers.get("x-csrf-token"),
    });
    return body;
  };

  const handlers = [
    http.get(`${API}/links`, async ({ request }) => {
      await record(request);
      const query = new URL(request.url).searchParams;
      const q = (query.get("q") ?? "").toLowerCase();
      const status = query.get("status");
      const page = Number(query.get("page") ?? 1);
      const size = Number(query.get("page_size") ?? 20);
      const matching = state.links.filter(
        (l) =>
          (!status || l.status === status) &&
          (!q || [l.title ?? "", l.code, l.destination_url].some((t) => t.toLowerCase().includes(q))),
      );
      return HttpResponse.json({
        items: matching.slice((page - 1) * size, page * size),
        total: matching.length,
        page,
        page_size: size,
      });
    }),
    http.post(`${API}/links`, async ({ request }) => {
      const body = (await record(request)) as LinkCreateBody;
      if (state.createError) return state.createError.clone();
      const link = makeLink({
        destination_url: body.destination_url,
        title: body.title ?? null,
        expires_at: body.expires_at ?? null,
        ...(body.alias ? { code: body.alias } : {}),
      });
      state.links.unshift(link);
      return HttpResponse.json(link, { status: 201 });
    }),
    http.patch(`${API}/links/:id`, async ({ request, params }) => {
      const body = (await record(request)) as LinkPatchBody;
      const link = state.links.find((l) => l.id === params.id);
      if (!link) return apiError(404, "not_found");
      Object.assign(link, body);
      if (body.is_active !== undefined) link.status = body.is_active ? "active" : "disabled";
      return HttpResponse.json(link);
    }),
    http.delete(`${API}/links/:id`, async ({ request, params }) => {
      await record(request);
      state.links = state.links.filter((l) => l.id !== params.id);
      return new HttpResponse(null, { status: 204 });
    }),
  ];

  return { state, handlers, listRequests: () => state.requests.filter((r) => r.method === "GET") };
}
