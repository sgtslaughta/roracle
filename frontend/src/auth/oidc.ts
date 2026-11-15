import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";
import type { AuthHooks } from "@/api/client";

export interface AuthClient {
  getUser(): Promise<Pick<User, "access_token" | "expired"> | null>;
  signinSilent(): Promise<Pick<User, "access_token" | "expired"> | null>;
  signinRedirect(): Promise<void>;
}

export function createUserManager() {
  return new UserManager({
    authority: import.meta.env.VITE_OIDC_AUTHORITY ?? "http://localhost:8080/realms/roracle",
    client_id: import.meta.env.VITE_OIDC_CLIENT_ID ?? "roracle-web",
    redirect_uri: `${window.location.origin}/auth/callback`,
    silent_redirect_uri: `${window.location.origin}/auth/silent`,
    post_logout_redirect_uri: window.location.origin,
    response_type: "code",
    scope: "openid profile",
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
}

/** Hooks consumed by apiFetch. An expired or absent token renews silently, else goes to sign-in. */
export function createAuthHooks(um: AuthClient): AuthHooks {
  const renew = async () => {
    try { const u = await um.signinSilent(); return !!u && !u.expired; } catch { return false; }
  };
  return {
    async getToken() {
      const u = await um.getUser();
      if (u && !u.expired) return u.access_token;
      if (await renew()) return (await um.getUser())?.access_token ?? null;
      await um.signinRedirect();
      return null;
    },
    renew,
    signIn: () => um.signinRedirect(),
  };
}
