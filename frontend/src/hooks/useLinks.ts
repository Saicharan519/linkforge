import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createLink, deleteLink, getLink, listLinks, updateLink, type ListParams } from "@/api/links";
import type { LinkCreateBody, LinkPatchBody } from "@/api/types";

export const linksKey = ["links"] as const;

export function useLinks(params: ListParams) {
  return useQuery({
    queryKey: [...linksKey, "list", params],
    queryFn: ({ signal }) => listLinks(params, signal),
    // Keep showing the previous page while the next one loads, so paging and filtering do not flash.
    placeholderData: keepPreviousData,
  });
}

export function useLink(id: string) {
  return useQuery({
    queryKey: [...linksKey, "one", id],
    queryFn: ({ signal }) => getLink(id, signal),
  });
}

/** Any change to a link can change lists, one link, and the totals on the dashboard. */
function useInvalidateAfterWrite() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: linksKey }),
      queryClient.invalidateQueries({ queryKey: ["analytics"] }),
    ]);
}

export function useCreateLink() {
  const invalidate = useInvalidateAfterWrite();
  return useMutation({ mutationFn: (body: LinkCreateBody) => createLink(body), onSuccess: invalidate });
}

export function useUpdateLink() {
  const invalidate = useInvalidateAfterWrite();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: LinkPatchBody }) => updateLink(id, body),
    onSuccess: invalidate,
  });
}

export function useDeleteLink() {
  const invalidate = useInvalidateAfterWrite();
  return useMutation({ mutationFn: (id: string) => deleteLink(id), onSuccess: invalidate });
}
