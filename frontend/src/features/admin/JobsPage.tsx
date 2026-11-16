import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { AdminJob, JobStatus } from "@/api/admin";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { useJobs, useJobsSummary, useRetryFailed, useRetryJob } from "./api";
import { fmtDateTime as fmtTime } from "@/lib/dates";
import { StatusPill } from "./StatusPill";
import { copyAction, RowMenu } from "@/components/RowMenu";

const STATUSES: JobStatus[] = ["queued", "running", "failed", "done"];
const ERR_MAX = 200;
const oneLine = (s: string | null) => (s ?? "").replace(/\s+/g, " ").trim();

export function JobsPage() {
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [confirm, setConfirm] = useState(false);
  const sum = useJobsSummary();
  const jobs = useJobs(status, kind);
  const retry = useRetryJob();
  const retryAll = useRetryFailed();
  const rows = jobs.data?.pages.flatMap((p) => p.items) ?? [];
  const kinds = Object.keys(sum.data?.jobs ?? {}).sort();

  const retryOne = (j: AdminJob) =>
    retry.mutate(j.id, {
      onSuccess: () => toast(`Job ${j.id} queued again.`),
      onError: (e) => toast(e instanceof ApiError ? e.message : "The job couldn't be retried. Try again."),
    });
  const doRetryAll = () =>
    retryAll.mutate(kind, {
      onSuccess: (r) => toast(r.retried ? `Queued ${r.retried} failed ${r.retried === 1 ? "job" : "jobs"} again.` : "No failed jobs to retry."),
      onError: (e) => toast(e instanceof ApiError ? e.message : "The jobs couldn't be retried. Try again."),
      onSettled: () => setConfirm(false),
    });

  return (
    <div className="admin-page">
      <h2>Queue</h2>
      {sum.isError && <p role="alert">The summary couldn't be loaded.</p>}
      {sum.data && (
        <div className="sumgrid" aria-label="Job counts">
          {kinds.map((k) => (
            <div className="card" key={k}><b>{k}</b>: {STATUSES.filter((s) => sum.data.jobs[k]?.[s]).map((s) => `${sum.data.jobs[k][s]} ${s}`).join(", ") || "none"}</div>
          ))}
          {!kinds.length && <div className="card">No jobs yet.</div>}
        </div>
      )}
      {sum.data && Object.keys(sum.data.versions).length > 0 && (
        <div className="sumgrid" aria-label="Versions by stage">
          {Object.entries(sum.data.versions).sort().map(([s, n]) => <div className="card" key={s}>{s}: <b>{n}</b></div>)}
        </div>
      )}
      <div className="filters">
        <label>Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>{STATUSES.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <label>Kind
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All</option>{kinds.map((k) => <option key={k}>{k}</option>)}
          </select>
        </label>
        <Button onClick={() => setConfirm(true)}><RotateCcw aria-hidden />Retry all failed</Button>
      </div>
      {jobs.isError && <p role="alert">Jobs couldn't be loaded. Reload to try again.</p>}
      {jobs.data && !rows.length && <p className="sub">No jobs match.</p>}
      {rows.length > 0 && (
        <div className="tok-wrap">
          <table className="agent-table admin-table">
            <thead><tr><th>ID</th><th>Kind</th><th>Status</th><th>Tries</th><th>File</th><th>Error</th><th>Updated</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((j) => (
                <tr key={j.id}>
                  <td>{j.id}</td><td>{j.kind}</td><td><StatusPill status={j.status} /></td><td>{j.attempts}</td>
                  <td className="trunc" title={j.filename}>{j.filename ?? "–"}</td>
                  <td className="wrap" title={oneLine(j.last_error)}>{oneLine(j.last_error).slice(0, ERR_MAX) || "–"}</td>
                  <td>{fmtTime(j.updated_at)}</td>
                  <td>
                    <RowMenu kebab={false} label={`Actions for job ${j.id}`} actions={[
                      ...(j.status === "failed" ? [{ label: "Retry", onSelect: () => retryOne(j) }] : []),
                      copyAction("Copy job ID", String(j.id)),
                      ...(j.last_error ? [copyAction("Copy error", oneLine(j.last_error))] : []),
                    ]} />
                    {j.status === "failed" && <Button variant="ghost" aria-label={`Retry job ${j.id}`} title="Retry" disabled={retry.isPending} onClick={() => retryOne(j)}><RotateCcw aria-hidden /></Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {jobs.hasNextPage && <Button disabled={jobs.isFetchingNextPage} onClick={() => void jobs.fetchNextPage()}>Load older jobs</Button>}
        </div>
      )}
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Retry all failed jobs?" confirm="Retry all" onConfirm={doRetryAll} busy={retryAll.isPending}>
        Every failed {kind ? `“${kind}” ` : ""}job goes back in the queue with a fresh attempt budget.
      </ConfirmDialog>
    </div>
  );
}
