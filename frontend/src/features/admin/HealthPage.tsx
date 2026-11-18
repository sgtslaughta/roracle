import { ApiError } from "@/api/client";
import type { AdminHealth } from "@/api/admin";
import { useHealth } from "./dataApi";
import { humanize } from "./StatusPill";

const ok = (v: string) => v === "ok";
const Pill = ({ good, text }: { good: boolean; text: string }) => <span className={`pill ${good ? "done" : "failed"}`}>{humanize(text)}</span>;

/** A 503 may still carry the health body; show it when it looks like one. */
function bodyOf(e: unknown): AdminHealth | null {
  const d = e instanceof ApiError ? (e.detail as Partial<AdminHealth> | null) : null;
  return d && typeof d === "object" && d.checks && typeof d.checks === "object" ? (d as AdminHealth) : null;
}

export function HealthPage() {
  const q = useHealth();
  const h = q.data ?? (q.isError ? bodyOf(q.error) : null);
  if (!h) {
    return q.isError ? <p role="alert">Health couldn't be checked: the request failed. It retries every 30 seconds.</p> : <div aria-busy="true" />;
  }
  return (
    <div className="admin-page">
      <h2>Health <Pill good={h.status === "ok"} text={h.status} /></h2>
      {q.isError && <p role="alert">The latest check failed; showing the last response.</p>}
      <div className="sumgrid" aria-label="Checks">
        {Object.entries(h.checks).map(([k, v]) => <div className="card stat" key={k}><span>{k === "db" ? "Database" : humanize(k)}</span><Pill good={ok(v)} text={v} /></div>)}
      </div>
      <div className="sumgrid" aria-label="Queue">
        {h.queue ? (
          <>
            <div className="card stat"><span>Queued</span><b>{h.queue.queued}</b></div>
            <div className="card stat"><span>Running</span><b>{h.queue.running}</b></div>
            <div className="card stat"><span>Failed</span><b>{h.queue.failed}</b></div>
            <div className="card stat"><span>Oldest queued</span><b>{h.queue.oldest_queued_s == null ? "–" : `${h.queue.oldest_queued_s} s`}</b></div>
          </>
        ) : <div className="card stat"><span>Queue</span><b>Unavailable</b></div>}
        <div className="card stat"><span>Scheduler tick</span><b>{h.scheduler ? (h.scheduler.tick_queued ? "Queued" : "Idle") : "Unavailable"}</b></div>
      </div>
    </div>
  );
}
