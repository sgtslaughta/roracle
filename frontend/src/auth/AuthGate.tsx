import { useEffect, useState, type ReactNode } from "react";
import { setAuthHooks } from "@/api/client";
import { createAuthHooks, createUserManager } from "./oidc";

/** Renders children once signed in. No session -> redirect to the identity provider (never a blank page). */
export function AuthGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const um = createUserManager();
    setAuthHooks(createAuthHooks(um));
    (async () => {
      if (window.location.pathname === "/auth/callback") {
        await um.signinRedirectCallback();
        window.history.replaceState({}, "", "/");
      }
      let user = await um.getUser();
      if (!user || user.expired) user = await um.signinSilent().catch(() => null);
      if (!user || user.expired) { await um.signinRedirect(); return; }
      setReady(true);
    })().catch((e: unknown) => setError(e instanceof Error ? e.message : "Sign-in failed"));
  }, []);

  if (error) return <div className="empty" role="alert">Sign-in failed: {error}. Reload to try again.</div>;
  if (!ready) return <div className="empty" role="status">Signing you in…</div>;
  return <>{children}</>;
}
