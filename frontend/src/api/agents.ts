/** P7 contract: personal access tokens (JWT-only management) and agent handoffs. */

export type TokenScopes = ["read"] | ["read", "write"];

export interface AccessToken {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  expires_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
}
/** POST /me/tokens response: the secret `token` is returned exactly once. */
export interface CreatedToken extends AccessToken { token: string }
export interface CreateTokenBody { name: string; scopes: TokenScopes; expires_in_days: number }

export type HandoffKind = "chat" | "collection" | "search" | "document" | "agent";
export type Audience = "creator" | "shared" | "authenticated";
export interface HandoffShare { kind: "user" | "group"; principal: string }

export interface CreateHandoffBody {
  kind: HandoffKind;
  source_id?: string;
  title: string;
  goal: string;
  brief: string;
  doc_ids: string[];
  audience: Audience;
  shares?: HandoffShare[];
  expires_in_days: number;
}
export type PatchHandoffBody = Partial<Pick<CreateHandoffBody, "title" | "goal" | "brief" | "audience" | "shares" | "expires_in_days">>;

export interface HandoffNote { at: string; by: string; text: string; via: string }
/** A citation-shaped passage, already redacted for the viewer. */
export interface HandoffPassage { doc_id: string; title?: string; page?: number; quote?: string; [k: string]: unknown }

export interface HandoffManifest {
  code: string;
  display: string;
  url: string;
  uri: string;
  kind: HandoffKind;
  title: string;
  goal: string;
  brief: string;
  source: Record<string, string>;
  doc_ids: string[];
  passages: HandoffPassage[];
  notes: HandoffNote[];
  withheld: number;
  parts: { transcript: number; notes: number };
  audience: Audience;
  expires_at: string;
  created_at: string;
  created_by: string;
  created_via: "ui" | "mcp";
}

/** GET /handoffs row (mine, light). */
export interface HandoffRow {
  code: string;
  display: string;
  kind: HandoffKind;
  title: string;
  audience: Audience;
  expires_at: string;
  revoked_at: string | null;
  created_at: string;
  created_via: "ui" | "mcp";
}

export const agentPrompt = (display: string, goal: string) =>
  `Using the rOracle MCP server, load handoff ${display} and continue: ${goal}`.trimEnd();

/** The manifest `url` is relative unless the server knows its public base URL. */
export const handoffLink = (url: string) => (/^https?:\/\//i.test(url) ? url : location.origin + url);
