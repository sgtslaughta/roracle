/** Thin fetch wrapper: bearer token, JSON, ApiError, and one silent renew on 401. */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

/** Encode one interpolated path segment: `a/b` -> `a%2Fb`. Use for EVERY id placed in an API path. */
export const seg = (v: string | number) => encodeURIComponent(String(v));

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.name = "ApiError";
  }
}

/** Thrown instead of sending a request once a sign-in redirect has started. */
export class SignInInProgress extends Error {
  constructor() { super("Redirecting to sign in"); this.name = "SignInInProgress"; }
}

export interface AuthHooks {
  getToken(): Promise<string | null>;
  /** Try a silent token renew; true when a fresh token is available. */
  renew(): Promise<boolean>;
  /** Redirect to the identity provider. Idempotent: only the first call starts a redirect. */
  signIn(): Promise<void> | void;
}
let hooks: AuthHooks | null = null;
export const setAuthHooks = (h: AuthHooks | null) => { hooks = h; };

/** For transports apiFetch can't serve (XHR upload with progress): the same token, silent renew and sign-in. */
export const auth = {
  token: async () => (await hooks?.getToken()) ?? null,
  renew: async () => (hooks ? hooks.renew() : false),
  signIn: async () => { await hooks?.signIn(); },
};

export type ApiInit = Omit<RequestInit, "body"> & { json?: unknown; body?: BodyInit | null };

async function send(path: string, init: ApiInit): Promise<Response> {
  // belt and braces: never let a dot-segment escape the API prefix
  if (!path.startsWith("/") || path.split("?")[0].split("/").some((s) => s === ".." || s === ".")) throw new ApiError(400, "invalid request path");
  const { json, headers: h, ...rest } = init;
  const headers = new Headers(h);
  const token = await hooks?.getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = rest.body;
  if (json !== undefined) { headers.set("Content-Type", "application/json"); body = JSON.stringify(json); }
  return fetch(`${API_BASE}${path}`, { ...rest, headers, body });
}

/** Build an ApiError from a non-OK response (detail from a JSON body when present). */
export async function errorOf(res: Response): Promise<ApiError> {
  let detail: unknown = res.statusText;
  try { detail = ((await res.json()) as { detail?: unknown }).detail ?? detail; } catch { /* no JSON body */ }
  return new ApiError(res.status, detail);
}

/** Authenticated fetch returning the raw Response (one silent renew on 401, then sign-in). Non-OK is NOT thrown. */
export async function authedFetch(path: string, init: ApiInit = {}): Promise<Response> {
  let res = await send(path, init);
  if (res.status === 401 && hooks) {
    // one silent renew + retry; anything else goes to sign-in
    if (await hooks.renew()) res = await send(path, init);
    if (res.status === 401) await hooks.signIn();
  }
  return res;
}

export async function apiFetch<T = unknown>(path: string, init: ApiInit = {}): Promise<T> {
  const res = await authedFetch(path, init);
  if (!res.ok) throw await errorOf(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("json") ? res.json() : res.blob()) as Promise<T>;
}
