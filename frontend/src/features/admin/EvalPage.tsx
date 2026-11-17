import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import { Button } from "@/components/ui/button";
import { EvalTrend } from "./EvalTrend";
import { useCreateSet, useEvalRun, useEvalRuns, useEvalSets, usePatchSet, useStartRun, type EvalPath, type Scores } from "./evalApi";

export const TREND_METRICS = ["p_ndcg@10", "p_recall@10", "p_recall@100", "p_mrr", "faithfulness", "citation_support"];
const f = (v: number | undefined) => (typeof v === "number" ? v.toFixed(3) : "–");

function ScoreTable({ title, rows }: { title: string; rows: Record<string, Scores> }) {
  const keys = Object.keys(rows);
  if (!keys.length) return null;
  return (
    <div className="tok-wrap"><table className="agent-table admin-table" aria-label={title}>
      <thead><tr><th>{title}</th><th>nDCG@10</th><th>recall@10</th><th>MRR</th></tr></thead>
      <tbody>{keys.map((k) => <tr key={k}><td>{k}</td><td>{f(rows[k]["p_ndcg@10"])}</td><td>{f(rows[k]["p_recall@10"])}</td><td>{f(rows[k].p_mrr)}</td></tr>)}</tbody>
    </table></div>
  );
}

function RunDetail({ id }: { id: string }) {
  const { data, isError } = useEvalRun(id);
  if (isError) return <p role="alert">The run couldn't be loaded.</p>;
  if (!data) return <div aria-busy="true" />;
  return (
    <div className="admin-page" aria-label="Run detail">
      <h2>Run {data.started_at.slice(0, 16).replace("T", " ")} · {data.status}</h2>
      {data.error && <p className="chipin-err" role="alert">{data.error}</p>}
      <ScoreTable title="By kind" rows={data.by_kind} />
      <ScoreTable title="By principal" rows={data.by_principal} />
      <h2>Worst pairs</h2>
      <div className="tok-wrap"><table className="agent-table admin-table" aria-label="Worst pairs">
        <thead><tr><th>Query</th><th>Principal</th><th>recall@10</th><th>Top documents</th></tr></thead>
        <tbody>{data.worst.map((w) => (
          <tr key={w.query_key + w.principal}><td className="trunc" title={w.text}>{w.text}</td><td>{w.principal}</td><td>{f(w["p_recall@10"])}</td><td className="trunc" title={w.top_docs.join(" · ")}>{w.top_docs.join(" · ")}</td></tr>
        ))}</tbody>
      </table></div>
    </div>
  );
}

/** Admin: retrieval-quality sets, runs and the metric trend. Everything here is admin-only (it can name private documents). */
export function EvalPage() {
  const sets = useEvalSets();
  const [setId, setSetId] = useState("");
  const runs = useEvalRuns(setId);
  const create = useCreateSet(), patch = usePatchSet(), start = useStartRun();
  const [name, setName] = useState("");
  const [path, setPath] = useState<EvalPath>("chat");
  const [judge, setJudge] = useState(false);
  const [metrics, setMetrics] = useState<string[]>(["p_ndcg@10", "p_recall@10"]);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fail = (e: unknown) => setError(errorText(e));
  const items = sets.data ?? [];
  const run = () => {
    setError(null);
    start.mutate({ set_id: setId, path, judge: judge && path === "chat" }, { onSuccess: () => toast("Run queued."), onError: fail });
  };
  if (sets.isError) return <p role="alert">Eval sets couldn't be loaded. Reload to try again.</p>;
  return (
    <div className="admin-page">
      <p className="sub">Gold sets measure retrieval per principal. Only admins see them, since queries and gold documents can reveal private material.</p>
      {error && <p className="chipin-err" role="alert">{error}</p>}
      <h2>Sets</h2>
      <div className="tok-wrap"><table className="agent-table admin-table" aria-label="Eval sets">
        <thead><tr><th>Name</th><th>Active</th><th>Drafts</th><th /></tr></thead>
        <tbody>{items.map((s) => (
          <tr key={s.id}>
            <td><Link to="/admin/eval/sets/$id" params={{ id: s.id }}>{s.name}</Link> {s.is_reference && <span className="badge-o">Reference</span>}</td>
            <td>{s.queries}</td><td>{s.drafts}</td>
            <td className="acts">{!s.is_reference && <Button onClick={() => { setError(null); patch.mutate({ id: s.id, is_reference: true }, { onError: fail }); }}>Make reference</Button>}</td>
          </tr>
        ))}</tbody>
      </table></div>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); if (name.trim()) { setError(null); create.mutate({ name: name.trim() }, { onSuccess: () => setName(""), onError: fail }); } }}>
        <label>New set<input className="inp" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} /></label>
        <Button type="submit" variant="primary" disabled={!name.trim() || create.isPending}>Create set</Button>
      </form>

      <h2>Runs</h2>
      <div className="filters">
        <label>Set<select value={setId} onChange={(e) => setSetId(e.target.value)}><option value="">All sets</option>{items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label>Path<select value={path} onChange={(e) => setPath(e.target.value as EvalPath)}><option value="chat">chat</option><option value="search">search</option></select></label>
        <label className="ck"><input type="checkbox" checked={judge && path === "chat"} disabled={path !== "chat"} onChange={(e) => setJudge(e.target.checked)} />Judge answers</label>
        <Button variant="primary" disabled={!setId || start.isPending} title={setId ? undefined : "Choose a set first"} onClick={run}>Run now</Button>
      </div>
      <fieldset className="filters" aria-label="Trend metrics" style={{ border: 0, padding: 0 }}>
        {TREND_METRICS.map((m) => (
          <label key={m} className="ck"><input type="checkbox" checked={metrics.includes(m)} onChange={(e) => setMetrics(e.target.checked ? [...metrics, m] : metrics.filter((x) => x !== m))} />{m}</label>
        ))}
      </fieldset>
      <EvalTrend runs={runs.data ?? []} metrics={metrics} />
      <div className="tok-wrap"><table className="agent-table admin-table" aria-label="Eval runs">
        <thead><tr><th>Started</th><th>Set</th><th>Path</th><th>Trigger</th><th>Status</th><th>nDCG@10</th><th>recall@10</th><th /></tr></thead>
        <tbody>{[...(runs.data ?? [])].reverse().map((r) => (
          <tr key={r.id}>
            <td>{r.started_at.slice(0, 16).replace("T", " ")}</td><td>{r.set_name}</td><td>{r.path}</td><td>{r.label || r.trigger}</td><td>{r.status}</td>
            <td>{f(r.metrics?.overall["p_ndcg@10"])}</td><td>{f(r.metrics?.overall["p_recall@10"])}</td>
            <td className="acts"><Button onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? "Hide" : "Details"}</Button></td>
          </tr>
        ))}</tbody>
      </table></div>
      {open && <RunDetail id={open} />}
    </div>
  );
}
