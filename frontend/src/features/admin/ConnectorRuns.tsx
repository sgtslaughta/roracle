import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { ConnectorRun } from "@/api/admin";
import { useRun, useRuns } from "./api";
import { fmtTime } from "./ConnectorsPage";
import { StatusPill } from "./StatusPill";
import { copyAction, RowMenu } from "@/components/RowMenu";

/** Longest log shown; the tail is what matters for a failed run. */
export const LOG_CAP = 200_000;
const STAT_LABELS: [keyof NonNullable<ConnectorRun["stats"]>, string][] = [
  ["seen", "seen"], ["ingested", "ingested"], ["versions", "new versions"], ["unchanged", "unchanged"], ["duplicate", "duplicates"], ["too_large", "too large"], ["errors", "errors"],
];
const statsText = (s: ConnectorRun["stats"]) => STAT_LABELS.filter(([k]) => typeof s?.[k] === "number").map(([k, l]) => `${s![k]} ${l}`).join(", ") || "No stats";

/** Run history (keyset, newest first) and the selected run's plain-text log. */
export function ConnectorRuns({ id }: { id: string }) {
  const runs = useRuns(id);
  const [sel, setSel] = useState<number | null>(null);
  const rows = runs.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="admin-page">
      <h2>Runs</h2>
      {runs.isError && <p role="alert">Runs couldn't be loaded. Reload to try again.</p>}
      {runs.data && !rows.length && <p className="sub">No runs yet.</p>}
      {rows.length > 0 && (
        <div className="tok-wrap">
          <table className="agent-table admin-table">
            <thead><tr><th>Status</th><th>Started</th><th>Finished</th><th>Stats</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><StatusPill status={r.status} /></td>
                  <td>{fmtTime(r.started_at)}</td>
                  <td>{r.finished_at ? fmtTime(r.finished_at) : "Not finished"}</td>
                  <td className="wrap">{statsText(r.stats)}</td>
                  <td>
                    <RowMenu kebab={false} label={`Actions for run ${r.id}`} actions={[{ label: sel === r.id ? "Hide log" : "View log", onSelect: () => setSel(sel === r.id ? null : r.id) }, copyAction("Copy run ID", String(r.id))]} />
                    <Button variant="ghost" aria-pressed={sel === r.id} onClick={() => setSel(sel === r.id ? null : r.id)}>{sel === r.id ? "Hide log" : "View log"}</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {runs.hasNextPage && <Button disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>Load older runs</Button>}
        </div>
      )}
      {sel != null && <RunLog id={id} runId={sel} />}
    </div>
  );
}

function RunLog({ id, runId }: { id: string; runId: number }) {
  const { data, isError } = useRun(id, runId);
  if (isError) return <p role="alert">The log couldn't be loaded.</p>;
  if (!data) return <p className="sub" aria-busy="true">Loading log…</p>;
  const log = data.log ?? "";
  const cut = log.length > LOG_CAP;
  return (
    <section aria-label={`Log for run ${runId}`}>
      <h2>Run {runId} log</h2>
      {data.status === "running" && <p className="sub">Still running. This updates automatically.</p>}
      {cut && <p className="sub">Showing the last {LOG_CAP / 1000} KB of {Math.round(log.length / 1000)} KB.</p>}
      {/* plain text only: log lines come from remote systems */}
      <pre className="runlog">{log ? (cut ? log.slice(-LOG_CAP) : log) : "The log is empty."}</pre>
    </section>
  );
}
