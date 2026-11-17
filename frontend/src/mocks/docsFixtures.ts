// Extra demo rows for the documentation screenshots (scripts/docs/screenshots). Browser mock mode only:
// mocks/browser.ts calls seedDocsFixtures() before the worker starts; the vitest server never does.
import type { HandoffManifest } from "@/api/agents";
import { dataOf, ENV } from "./adminData";
import { tokens } from "./agents";
import { all as proposals } from "./proposals";
import { DOCS } from "./docs";
import { docLabel } from "./labels";
import { state } from "./state";

const DAY = 86_400_000;
const iso = (ms = 0) => new Date(Date.now() + ms).toISOString();

function handoff(code: string, title: string, o: Partial<HandoffManifest> = {}, revoked = false) {
  const m: HandoffManifest = {
    code, display: `${code.slice(0, 4)}-${code.slice(4)}`, url: `/h/${code.slice(0, 4)}-${code.slice(4)}`, uri: `roracle://handoff/${code}`,
    kind: "search", title, goal: "", brief: "", source: {}, doc_ids: [], passages: [], notes: [], withheld: 0, parts: { transcript: 0, notes: 0 },
    audience: "creator", expires_at: iso(7 * DAY), created_at: iso(-3 * DAY), created_by: "researcher", created_via: "ui", ...o,
  };
  state.handoffs[code] = { m, revoked_at: revoked ? iso(-DAY) : null, shares: [] };
}

const ADMIN_UUID = "9f3c2d1e-5b7a-4c68-8e21-0a4d6b7c8e90";

export function seedDocsFixtures() {
  // the audit filter only accepts a UUID (stock rows use "u-admin"); also realistic, newest-first rows and the real rate-limit default
  const rows: [string, string, unknown][] = [
    ["admin.settings_update", "/admin/settings", { changes: { rerank_depth: { old: 40, new: 50 } } }],
    ["admin.settings_update", "/admin/settings", { changes: { chat_passages: { old: 8, new: 10 }, rerank_depth: { old: 40, new: 60 }, near_dup_threshold: { old: 0.92, new: 0.9 }, max_upload_bytes: { old: 524288000, new: 1073741824 }, chat_note_min_ratio: { old: 0.7, new: 0.75 } }, reason: "tuning after the Q4 eval run" }],
    ["admin.tags_merge", "pde", { merged: ["partial-differential-equations"], documents: 9 }],
    ["admin.label_update", "Confidential", { changes: { block_export: { old: false, new: true } } }],
  ];
  dataOf().audit.forEach((a, i) => {
    const [action, target, detail] = rows[i % rows.length];
    // newest first: ids already fall with i, so the times must too
    Object.assign(a, { user_id: ADMIN_UUID, at: new Date(Date.UTC(2026, 10, 16, 15) - i * 3_600_000 * 5).toISOString(), action, target, detail });
  });
  ENV.rate_limit_per_minute = 0; // config.py default: off
  tokens().push(
    { id: "tok-2", name: "Notebook agent", prefix: "ror_m8Zt", scopes: ["read", "write"], expires_at: iso(-2 * DAY), revoked_at: null, last_used_at: iso(-9 * DAY), created_at: iso(-40 * DAY) },
    { id: "tok-3", name: "CI helper", prefix: "ror_x4Nd", scopes: ["read"], expires_at: iso(30 * DAY), revoked_at: iso(-DAY), last_used_at: iso(-3 * DAY), created_at: iso(-20 * DAY) },
  );
  // the stock finding carries an HTML-escaping probe, and there is no label proposal
  const ps = proposals();
  ps[0].payload.body = "Re-ran the k = 3 sweep to n = 2·10⁵: the gap stays about 6% above the bound.";
  ps.unshift({
    id: "p-label", kind: "label", client: "claude-code", status: "pending", annotation_id: null, created_at: "2026-11-15T09:41:00Z", decided_at: null,
    payload: { document_id: DOCS[2].id, body: "", label_id: "l-internal" }, document: { id: DOCS[2].id, title: DOCS[2].title },
  });
  const ids = DOCS.slice(0, 3).map((d) => d.id);
  handoff("K7MX4PQ2", "Spectral gap of random hypergraphs", {
    kind: "chat", goal: "Find out whether the lower bound extends to hypergraphs and list the open questions.",
    brief: "Start from the Chen and Aldana result, then read Ito 2023. Note anything the survey calls open.",
    doc_ids: ids, documents: ids.map((id, i) => ({ id, title: DOCS.find((d) => d.id === id)!.title, label: docLabel(id), text_withheld: i === 1 })),
    audience: "shared", withheld: 2, withheld_by_reason: { unavailable: 2, label_text: 1 }, parts: { transcript: 4, notes: 1 },
    notes: [{ at: iso(-DAY), by: "claude-code", text: "Checked the hypergraph case: the conjectured factor (k-1) appears in Ito 2023, Theorem 2.", via: "mcp" }],
  });
  handoff("R3TD9WNH", "Reading list for the thesis", { kind: "collection", audience: "authenticated", expires_at: iso(25 * DAY) });
  handoff("B6QF2JVA", "Solver benchmarks, Q3", { kind: "search", expires_at: iso(-2 * DAY), created_at: iso(-12 * DAY) });
  handoff("W2HC8MZE", "Old brief", { kind: "document", created_at: iso(-20 * DAY) }, true);
}
