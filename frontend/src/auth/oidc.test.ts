import { describe, expect, it, vi } from "vitest";
import { createAuthHooks, type AuthClient } from "./oidc";

const user = (expired: boolean) => ({ access_token: "t", expired });
const client = (o: Partial<AuthClient>): AuthClient => ({ getUser: async () => null, signinSilent: async () => null, signinRedirect: vi.fn(async () => {}), ...o });

describe("auth hooks", () => {
  it("returns a valid token without renewing", async () => {
    const silent = vi.fn();
    const h = createAuthHooks(client({ getUser: async () => user(false), signinSilent: silent }));
    expect(await h.getToken()).toBe("t");
    expect(silent).not.toHaveBeenCalled();
  });

  it("absent token with failing silent renew redirects to sign-in", async () => {
    const redirect = vi.fn(async () => {});
    const h = createAuthHooks(client({ signinSilent: async () => { throw new Error("no session"); }, signinRedirect: redirect }));
    expect(await h.getToken()).toBeNull();
    expect(redirect).toHaveBeenCalledTimes(1);
  });

  it("expired token renews silently", async () => {
    let u = user(true);
    const h = createAuthHooks(client({ getUser: async () => u, signinSilent: async () => (u = user(false)) }));
    expect(await h.getToken()).toBe("t");
  });
});
