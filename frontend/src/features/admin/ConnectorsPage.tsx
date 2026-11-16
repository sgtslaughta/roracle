import { Link } from "@tanstack/react-router";
import { Pencil, Play, Plug, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { Connector } from "@/api/admin";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { isUuid } from "@/lib/ids";
import { useConnectors, useDeleteConnector, useRunConnector, useSaveConnector } from "./api";
import { ConnectorDialog } from "./ConnectorForm";
import { StatusPill } from "./StatusPill";
import { RowMenu } from "@/components/RowMenu";

const SHOWN = 50;
export const fmtTime = (s: string | null | undefined) => (s ? new Date(s).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "Never");
export const scheduleText = (m: number | null) => (m ? `Every ${m} min` : "Manual");

/** Run now: 202 queued; 409 (already queued or running) shows the server's reason. */
export function useRunNow() {
  const run = useRunConnector();
  return {
    pending: run.isPending,
    runNow: (c: Pick<Connector, "id" | "name">) =>
      run.mutate(c.id, {
        onSuccess: () => toast(`Run queued for ${c.name}.`),
        onError: (e) => toast(e instanceof ApiError ? e.message : "The run couldn't be started. Try again."),
      }),
  };
}

export function ConnectorsPage() {
  const { data, isError } = useConnectors();
  const save = useSaveConnector();
  const del = useDeleteConnector();
  const { runNow, pending } = useRunNow();
  const [editing, setEditing] = useState<Connector | "new" | null>(null);
  const [target, setTarget] = useState<Connector | null>(null);
  const [shown, setShown] = useState(SHOWN);

  const setEnabled = (c: Connector, enabled: boolean) =>
    save.mutate({ id: c.id, body: { enabled } }, { onError: (e) => toast(e instanceof ApiError ? e.message : "The change couldn't be saved. Try again.") });
  const doDelete = () => {
    if (!target) return;
    del.mutate(target.id, {
      onSuccess: () => toast(`Deleted ${target.name}. Its documents stay in the library.`),
      onError: () => toast("The connector couldn't be deleted. Try again."),
      onSettled: () => setTarget(null),
    });
  };

  return (
    <div className="admin-page">
      <div className="uhead-row">
        <p className="sub">Sources that rOracle pulls documents from on a schedule or on demand.</p>
        <Button variant="primary" onClick={() => setEditing("new")}><Plus aria-hidden />New connector</Button>
      </div>
      {isError && <p role="alert">Connectors couldn't be loaded. Reload to try again.</p>}
      {data && !data.length && <div className="empty"><Plug aria-hidden /><p>No connectors yet. Create one to start ingesting documents.</p></div>}
      {data && data.length > 0 && (
        <div className="tok-wrap">
          <table className="agent-table admin-table">
            <thead><tr><th>Name</th><th>Kind</th><th>Enabled</th><th>Schedule</th><th>Last run</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {data.slice(0, shown).map((c) => (
                <tr key={c.id} className={c.enabled ? undefined : "dim"}>
                  <td className="trunc" title={c.name}>{isUuid(c.id) ? <Link to="/admin/connectors/$id" params={{ id: c.id }}>{c.name}</Link> : c.name}</td>
                  <td>{c.kind}</td>
                  <td><input type="checkbox" aria-label={`Enabled: ${c.name}`} checked={c.enabled} disabled={save.isPending} onChange={(e) => setEnabled(c, e.target.checked)} /></td>
                  <td>{scheduleText(c.schedule_minutes)}</td>
                  <td>{c.last_run ? <><StatusPill status={c.last_run.status} /> <small>{fmtTime(c.last_run.started_at)}</small></> : "Never run"}</td>
                  <td>
                    <RowMenu kebab={false} label={`Actions for ${c.name}`} actions={[
                      { label: "Run now", onSelect: () => runNow(c) },
                      { label: "Edit", onSelect: () => setEditing(c) },
                      { label: c.enabled ? "Disable" : "Enable", onSelect: () => setEnabled(c, !c.enabled) },
                      { label: "Delete", danger: true, onSelect: () => setTarget(c) },
                    ]} />
                    <span className="acts">
                      <Button variant="ghost" aria-label={`Run ${c.name} now`} title="Run now" disabled={pending} onClick={() => runNow(c)}><Play aria-hidden /></Button>
                      <Button variant="ghost" aria-label={`Edit ${c.name}`} title="Edit" onClick={() => setEditing(c)}><Pencil aria-hidden /></Button>
                      <Button variant="ghost" aria-label={`Delete ${c.name}`} title="Delete" onClick={() => setTarget(c)}><Trash2 aria-hidden /></Button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.length > shown && <Button onClick={() => setShown((n) => n + SHOWN)}>Show more ({data.length - shown} hidden)</Button>}
        </div>
      )}
      {editing && <ConnectorDialog open connector={editing === "new" ? undefined : editing} onOpenChange={(o) => { if (!o) setEditing(null); }} />}
      <ConfirmDialog open={!!target} onOpenChange={(o) => { if (!o) setTarget(null); }} title="Delete this connector?" confirm="Delete" onConfirm={doDelete} busy={del.isPending}>
        “{target?.name}” and its run history are removed. Documents it already ingested stay in the library.
      </ConfirmDialog>
    </div>
  );
}
