import { describe, expect, it, vi } from "vitest";
import { SignInInProgress } from "@/api/client";
import { UserManager } from "oidc-client-ts";
import { toast } from "sonner";
import { createAuthHooks, signOut, type AuthClient } from "./oidc";

vi.mock("sonner", () => ({ toast: vi.fn() }));

const user = (expired: boolean) => ({ access_token: "t", expired });
const client = (o: Partial<AuthClient>): AuthClient => ({ getUser: async () => null, signinSilent: async () => null, signinRedirect: vi.fn(async () => {}), ...o });

describe("auth hooks", () => {
  it("returns a valid token without renewing", async () => {
    const silent = vi.fn();
    const h = createAuthHooks(client({ getUser: async () => user(false), signinSilent: silent }));
    expect(await h.getToken()).toBe("t");
    expect(silent).not.toHaveBeenCalled();
  });

  it("absent token with failing silent renew redirects once and sends no request", async () => {
    const redirect = vi.fn(async () => {});
    const h = createAuthHooks(client({ signinSilent: async () => { throw new Error("no session"); }, signinRedirect: redirect }));
    await expect(h.getToken()).rejects.toBeInstanceOf(SignInInProgress);
    await expect(h.getToken()).rejects.toBeInstanceOf(SignInInProgress);
    await h.signIn();
    expect(redirect).toHaveBeenCalledTimes(1);
  });

  it("concurrent renews share one in-flight silent sign-in", async () => {
    const silent = vi.fn(() => new Promise<{ access_token: string; expired: boolean }>((r) => setTimeout(() => r(user(false)), 20)));
    const h = createAuthHooks(client({ signinSilent: silent }));
    const out = await Promise.all([h.renew(), h.renew(), h.renew()]);
    expect(out).toEqual([true, true, true]);
    expect(silent).toHaveBeenCalledTimes(1);
  });

  it("expired token renews silently", async () => {
    let u = user(true);
    const h = createAuthHooks(client({ getUser: async () => u, signinSilent: async () => (u = user(false)) }));
    expect(await h.getToken()).toBe("t");
  });
});

describe("signOut", () => {
  it("a failed sign-out toasts and leaves sign-in working (no hang on the next 401)", async () => {
    vi.spyOn(UserManager.prototype, "signoutRedirect").mockRejectedValue(new Error("idp down"));
    await expect(signOut()).resolves.toBeUndefined();
    expect(toast).toHaveBeenCalledWith("Couldn't sign out. Try again.");
    const redirect = vi.fn(async () => {});
    await createAuthHooks(client({ signinRedirect: redirect })).signIn();
    expect(redirect).toHaveBeenCalledTimes(1);
  });
});
