import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/test/server";
import { revokeApiKey } from "./apiKeys";

describe("revokeApiKey", () => {
  it("escapes the id in the path", async () => {
    const seen: string[] = [];
    server.use(
      http.delete("http://localhost:3000/api/v1/api-keys/*", ({ request }) => {
        seen.push(new URL(request.url).pathname);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await revokeApiKey("a/b c");
    expect(seen).toEqual(["/api/v1/api-keys/a%2Fb%20c"]);
  });
});
