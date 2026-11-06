import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { makeOverview } from "./analyticsData";

// MSW intercepts at the fetch boundary: the ONE thing the tests fake (frontend/CLAUDE.md).
// Each test registers the handlers it needs with server.use(...); an unexpected call fails the test.
//
// The only default: the dashboard is where a successful sign-in lands, so many tests end up on it
// without being about it. Its data request gets a harmless answer; tests about the dashboard
// override it with server.use(...).
export const server = setupServer(
  http.get("http://localhost:3000/api/v1/analytics/overview", () => HttpResponse.json(makeOverview())),
);
