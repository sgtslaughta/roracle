import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate, resetAuthGate } from "./AuthGate";
import { safeReturnTo, type GateClient } from "./oidc";

const mgr = (o: Partial<GateClient> = {}): GateClient => ({
  getUser: async () => null,
  signinSilent: async () => null,
  signinRedirect: vi.fn(async () => {}),
  signinRedirectCallback: vi.fn(async () => ({ state: undefined })),
  ...o,
});
beforeEach(() => { resetAuthGate(); window.history.replaceState({}, "", "/"); });

describe("safeReturnTo", () => {
  it("accepts only same-origin relative paths", () => {
    expect(safeReturnTo("/d/abc?x=1#p=2")).toBe("/d/abc?x=1#p=2");
    for (const bad of ["//evil.com", "https://evil.com", "javascript:alert(1)", "/\\evil.com", "", undefined, 3]) expect(safeReturnTo(bad)).toBe("/");
  });
});

describe("AuthGate", () => {
  it("signed out: shows the signing-in state (not blank) and redirects carrying the deep link", async () => {
    window.history.replaceState({}, "", "/d/abc?x=1#p=2&c=v:2");
    const m = mgr();
    render(<AuthGate manager={m}><p>app</p></AuthGate>);
    expect(screen.getByRole("status")).toHaveTextContent("Signing you in");
    await waitFor(() => expect(m.signinRedirect).toHaveBeenCalledTimes(1));
    expect(m.signinRedirect).toHaveBeenCalledWith({ state: { returnTo: "/d/abc?x=1#p=2&c=v:2" } });
    expect(screen.queryByText("app")).toBeNull();
  });

  it("signed in: renders children without redirecting", async () => {
    const m = mgr({ getUser: async () => ({ access_token: "t", expired: false }) });
    render(<AuthGate manager={m}><p>app</p></AuthGate>);
    expect(await screen.findByText("app")).toBeInTheDocument();
    expect(m.signinRedirect).not.toHaveBeenCalled();
  });

  it("callback restores the original deep link", async () => {
    window.history.replaceState({}, "", "/auth/callback?code=1&state=2");
    const m = mgr({ signinRedirectCallback: vi.fn(async () => ({ state: { returnTo: "/d/abc/v/2#p=3" } })), getUser: async () => ({ access_token: "t", expired: false }) });
    render(<AuthGate manager={m}><p>app</p></AuthGate>);
    await screen.findByText("app");
    expect(window.location.pathname + window.location.hash).toBe("/d/abc/v/2#p=3");
  });

  it.each(["//evil.com/x", "https://evil.com"])("callback ignores a hostile returnTo %s", async (bad) => {
    window.history.replaceState({}, "", "/auth/callback?code=1");
    const m = mgr({ signinRedirectCallback: async () => ({ state: { returnTo: bad } }), getUser: async () => ({ access_token: "t", expired: false }) });
    render(<AuthGate manager={m}><p>app</p></AuthGate>);
    await screen.findByText("app");
    expect(window.location.pathname).toBe("/");
    expect(window.location.hostname).not.toBe("evil.com");
  });

  it("StrictMode double effect handles the callback once and does not show an error", async () => {
    window.history.replaceState({}, "", "/auth/callback?code=1");
    const cb = vi.fn(async () => ({ state: { returnTo: "/library" } }));
    const m = mgr({ signinRedirectCallback: cb, getUser: async () => ({ access_token: "t", expired: false }) });
    render(<StrictMode><AuthGate manager={m}><p>app</p></AuthGate></StrictMode>);
    expect(await screen.findByText("app")).toBeInTheDocument();
    expect(cb).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows an error when the callback fails", async () => {
    window.history.replaceState({}, "", "/auth/callback?code=1");
    render(<AuthGate manager={mgr({ signinRedirectCallback: async () => { throw new Error("bad state"); } })}><p>app</p></AuthGate>);
    expect(await screen.findByRole("alert")).toHaveTextContent("bad state");
  });
});
