import { setupServer } from "msw/node";

// MSW intercepts at the fetch boundary: the ONE thing the tests fake (frontend/CLAUDE.md).
// Each test registers the handlers it needs with server.use(...); an unexpected call fails the test.
export const server = setupServer();
