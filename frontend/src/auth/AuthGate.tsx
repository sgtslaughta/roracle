import { useEffect, useState, type ReactNode } from "react";
import { setAuthHooks } from "@/api/client";
import { createAuthHooks, createUserManager, currentPath, safeReturnTo, type GateClient } from "./oidc";

// StrictMode runs effects twice in dev; a second redirect callback would fail (state already consumed).
let boot: Promise<void> | null = null;
export const resetAuthGate = () => { boot = null; };

async function run(um: GateClient) {
  const hooks = createAuthHooks(um);
  setAuthHooks(hooks);
  if (window.location.pathname === "/auth/callback") {
    const u = await um.signinRedirectCallback();
    window.history.replaceState({}, "", safeReturnTo(u.state && (u.state as { returnTo?: unknown }).returnTo));
  }
  let user = await um.getUser();
  if (!user || user.expired) user = await um.signinSilent().catch(() => null);
  if (!user || user.expired) {
    await um.signinRedirect({ state: { returnTo: currentPath() } });
    return new Promise<void>(() => {}); // the page is navigating away; never render children
  }
}

/** Renders children once signed in. No session -> redirect to the identity provider and come back to the same link. */
export function AuthGate({ children, manager }: { children: ReactNode; manager?: GateClient }) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    boot ??= run(manager ?? createUserManager());
    let live = true;
    boot.then(() => live && setReady(true), (e: unknown) => live && setError(e instanceof Error ? e.message : "Sign-in failed"));
    return () => { live = false; };
  }, [manager]);

  if (error) return <div className="empty" role="alert">Sign-in failed: {error}. Reload to try again.</div>;
  if (!ready) return <div className="empty" role="status">Signing you in…</div>;
  return <>{children}</>;
}
