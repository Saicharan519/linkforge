import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { makeOverview } from "./analyticsData";

// MSW intercepts at the fetch boundary: the ONE thing the tests fake (frontend/CLAUDE.md).
// Each test registers the handlers it needs with server.use(...); an unexpected call fails the test.
//
// Defaults: the signed-in screens fetch their data as soon as they appear, and many tests land on
// one without being about it (for example the page a sign-in returns to). Those reads get an
// empty but valid answer; tests about a screen override them with server.use(...). Writes
// (POST, PATCH, DELETE) have no default: an unexpected write always fails the test.
const BASE = "http://localhost:3000/api/v1";
export const server = setupServer(
  http.get(`${BASE}/analytics/overview`, () => HttpResponse.json(makeOverview())),
  http.get(`${BASE}/links`, () => HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 })),
  http.get(`${BASE}/api-keys`, () => HttpResponse.json([])),
);
