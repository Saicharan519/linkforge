import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchMe, login, logout, register, type Credentials } from "@/api/auth";
import { ApiError } from "@/api/client";
import type { User } from "@/api/types";

export const SESSION_KEY = ["session"] as const;

/**
 * The signed-in user, `null` when signed out, `undefined` while the first check is running.
 * "Signed out" is an answer, not an error: /auth/me answering 401 resolves to null.
 */
export function useSession() {
  return useQuery<User | null>({
    queryKey: SESSION_KEY,
    queryFn: async ({ signal }) => {
      try {
        return await fetchMe(signal);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: Infinity,
    retry: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Credentials) => login(body),
    onSuccess: (user) => queryClient.setQueryData(SESSION_KEY, user),
  });
}

/** Register creates the account only; the user then signs in (the API does not log in on register). */
export function useRegister() {
  return useMutation({ mutationFn: (body: Credentials) => register(body) });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: logout,
    onSettled: () => {
      // Whatever the server said, this browser is done with the session: drop every cached view.
      queryClient.clear();
      queryClient.setQueryData(SESSION_KEY, null);
    },
  });
}
