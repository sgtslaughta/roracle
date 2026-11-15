import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";
import { SignInInProgress, type AuthHooks } from "@/api/client";

type Tok = Pick<User, "access_token" | "expired">;
export interface AuthClient {
  getUser(): Promise<Tok | null>;
  signinSilent(): Promise<Tok | null>;
  signinRedirect(args?: { state?: unknown }): Promise<void>;
}
export interface GateClient extends AuthClient {
  signinRedirectCallback(): Promise<{ state?: unknown }>;
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

/** Only same-origin relative paths survive a sign-in round trip ("/x", never "//host" or absolute URLs). */
export function safeReturnTo(v: unknown): string {
  return typeof v === "string" && v.startsWith("/") && !v.startsWith("//") && !v.startsWith("/\\") ? v : "/";
}
export const currentPath = () => window.location.pathname + window.location.search + window.location.hash;

/**
 * Hooks consumed by apiFetch. One shared in-flight silent renew; one sign-in redirect ever;
 * once a redirect has started no further (unauthenticated) request is sent.
 */
export function createAuthHooks(um: AuthClient): AuthHooks {
  let inflight: Promise<boolean> | null = null;
  let redirect: Promise<void> | null = null;
  const renew = () =>
    (inflight ??= um.signinSilent().then((u) => !!u && !u.expired, () => false).finally(() => { inflight = null; }));
  const signIn = () => (redirect ??= um.signinRedirect({ state: { returnTo: currentPath() } }));
  return {
    async getToken() {
      if (redirect) throw new SignInInProgress();
      const u = await um.getUser();
      if (u && !u.expired) return u.access_token;
      if (await renew()) return (await um.getUser())?.access_token ?? null;
      await signIn();
      throw new SignInInProgress();
    },
    renew,
    signIn,
  };
}
