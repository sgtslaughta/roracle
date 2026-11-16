import { Link } from "@tanstack/react-router";
import { ArrowLeft, Play } from "lucide-react";
import type { Connector } from "@/api/admin";
import { Button } from "@/components/ui/button";
import { isUuid } from "@/lib/ids";
import { useConnector } from "./api";
import { fmtDateTime as fmtTime } from "@/lib/dates";
import { scheduleText, useRunNow } from "./ConnectorsPage";
import { ConnectorRuns } from "./ConnectorRuns";
import { StatusPill } from "./StatusPill";

const VIS = { private: "Only the admin who created it", org_unit: "An org unit", everyone: "Everyone" } as const;
const val = (v: unknown) => (Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? (v ? "yes" : "no") : v == null || v === "" ? "–" : String(v));

function Summary({ c }: { c: Connector }) {
  return (
    <dl className="cfg">
      <dt>Kind</dt><dd>{c.kind}</dd>
      <dt>Enabled</dt><dd>{c.enabled ? "yes" : "no"}</dd>
      <dt>Visible to</dt><dd>{VIS[c.visibility] ?? c.visibility}{c.visibility === "org_unit" && ` (${c.org_unit})`}</dd>
      <dt>Schedule</dt><dd>{scheduleText(c.schedule_minutes)}</dd>
      <dt>Next run</dt><dd>{fmtTime(c.next_run_at)}</dd>
      <dt>Last run</dt><dd>{c.last_run ? <><StatusPill status={c.last_run.status} /> {fmtTime(c.last_run.started_at)}</> : "Never"}</dd>
      {Object.entries(c.config).map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{val(v)}</dd></div>)}
    </dl>
  );
}

/** `/admin/connectors/$id`: config summary, Run now, and run history. An id that isn't a UUID sends no request. */
export function ConnectorDetail({ id }: { id: string }) {
  const ok = isUuid(id);
  const { data: c, isError } = useConnector(ok ? id : undefined);
  const { runNow, pending } = useRunNow();
  return (
    <div className="admin-page">
      <Link to="/admin/connectors" className="menu-link"><ArrowLeft aria-hidden />All connectors</Link>
      {(!ok || isError) && <p role="alert">This connector doesn't exist or couldn't be loaded.</p>}
      {c && (
        <>
          <div className="uhead-row">
            <h2>{c.name}</h2>
            <Button variant="primary" disabled={pending} onClick={() => runNow(c)}><Play aria-hidden />Run now</Button>
          </div>
          <Summary c={c} />
          <ConnectorRuns id={c.id} />
        </>
      )}
    </div>
  );
}
