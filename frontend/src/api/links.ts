import { api } from "./client";
import type { Link, LinkCreateBody, LinkList, LinkPatchBody, LinkStatus } from "./types";

export interface ListParams {
  q: string;
  status: LinkStatus | undefined;
  page: number;
  pageSize: number;
}

export function listLinks(params: ListParams, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.status) query.set("status", params.status);
  query.set("page", String(params.page));
  query.set("page_size", String(params.pageSize));
  return api<LinkList>(`/links?${query}`, { signal });
}

export const getLink = (id: string, signal?: AbortSignal) =>
  api<Link>(`/links/${encodeURIComponent(id)}`, { signal });

export const createLink = (body: LinkCreateBody) => api<Link>("/links", { method: "POST", body });

export const updateLink = (id: string, body: LinkPatchBody) =>
  api<Link>(`/links/${encodeURIComponent(id)}`, { method: "PATCH", body });

export const deleteLink = (id: string) =>
  api<void>(`/links/${encodeURIComponent(id)}`, { method: "DELETE" });
