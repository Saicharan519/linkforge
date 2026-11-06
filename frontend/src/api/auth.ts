import { api } from "./client";
import type { User } from "./types";

export interface Credentials {
  email: string;
  password: string;
}

// At boot a 401 just means "not signed in": no redirect. On login a 401 means "wrong email or
// password": the form shows it, so no redirect either.
export const fetchMe = (signal?: AbortSignal) =>
  api<User>("/auth/me", { redirectOn401: false, signal });

export const login = (body: Credentials) =>
  api<User>("/auth/login", { method: "POST", body, redirectOn401: false });

export const register = (body: Credentials) =>
  api<User>("/auth/register", { method: "POST", body, redirectOn401: false });

export const changePassword = (body: { old_password: string; new_password: string }) =>
  api<void>("/auth/change-password", { method: "POST", body });

export const logout = () => api<void>("/auth/logout", { method: "POST" });
