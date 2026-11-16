import { useState } from "react";
import { useDebounced } from "@/lib/useDebounced";
import { Button } from "@/components/ui/button";
import { useAudit } from "./dataApi";
import { fmtDateTime as fmtTime } from "@/lib/dates";
import { copyAction, RowMenu } from "@/components/RowMenu";

const DETAIL_MAX = 160;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const asText = (d: unknown) => (d == null ? "" : typeof d === "string" ? d : JSON.stringify(d));

export function AuditPage() {
  const [action, setAction] = useState("");
  const [user, setUser] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const uid = user.trim();
  const bad = uid !== "" && !UUID.test(uid);
  const q = useAudit(useDebounced(action.trim()), useDebounced(bad ? "" : uid));
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="admin-page">
      <h2>Audit log</h2>
      <div className="filters">
        <label>Action starts with <input value={action} onChange={(e) => setAction(e.target.value)} placeholder="admin." /></label>
        <label>User ID <input value={user} aria-invalid={bad} onChange={(e) => setUser(e.target.value)} placeholder="user uuid" /></label>
        {bad && <small className="sub">User ID must be a UUID.</small>}
      </div>
      {q.isError && <p role="alert">The audit log couldn't be loaded. Reload to try again.</p>}
      {q.data && !rows.length && <p className="sub">No entries match.</p>}
      {rows.length > 0 && (
        <div className="tok-wrap">
          <table className="agent-table admin-table">
            <thead><tr><th>Time</th><th>User</th><th>Action</th><th>Target</th><th>Details</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const full = asText(r.detail), long = full.length > DETAIL_MAX, expanded = open === r.id;
                return (
                  <tr key={r.id}>
                    <td>{fmtTime(r.at)}</td><td>{r.username ?? "–"}</td><td>{r.action}</td>
                    <td className="trunc" title={r.target}>{r.target || "–"}</td>
                    <td className="wrap">
                      {full ? (expanded || !long ? full : `${full.slice(0, DETAIL_MAX)}…`) : "–"}
                      {long && <> <Button variant="ghost" aria-expanded={expanded} onClick={() => setOpen(expanded ? null : r.id)}>{expanded ? "Less" : "More"}</Button></>}
                    </td>
                    <td><RowMenu label={`Actions for entry ${r.id}`} actions={[copyAction("Copy details", full || r.action), ...(r.target ? [copyAction("Copy target", r.target)] : [])]} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {q.hasNextPage && <Button disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>Load older entries</Button>}
        </div>
      )}
    </div>
  );
}
