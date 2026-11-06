import { api } from "./client";
import type { ApiKey, ApiKeyCreated } from "./types";

// These endpoints accept a browser session only (an API key cannot manage API keys), so the
// normal cookie + X-CSRF-Token rules of the client apply.
export const listApiKeys = (signal?: AbortSignal) => api<ApiKey[]>("/api-keys", { signal });

export const createApiKey = (name: string) => api<ApiKeyCreated>("/api-keys", { method: "POST", body: { name } });

export const revokeApiKey = (id: string) => api<void>(`/api-keys/${encodeURIComponent(id)}`, { method: "DELETE" });
