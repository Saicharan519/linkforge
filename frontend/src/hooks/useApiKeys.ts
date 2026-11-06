import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createApiKey, listApiKeys, revokeApiKey } from "@/api/apiKeys";
import { changePassword } from "@/api/auth";

const keysKey = ["api-keys"] as const;

export const useApiKeys = () => useQuery({ queryKey: keysKey, queryFn: ({ signal }) => listApiKeys(signal) });

export function useCreateApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => createApiKey(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keysKey }),
  });
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => revokeApiKey(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keysKey }),
  });
}

export const useChangePassword = () => useMutation({ mutationFn: changePassword });
