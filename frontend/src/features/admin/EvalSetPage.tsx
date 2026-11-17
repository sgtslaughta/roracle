import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import { DirectoryPicker } from "@/components/DirectoryPicker";
import { OverflowList } from "@/components/OverflowList";
import { Button } from "@/components/ui/button";
import type { Person } from "@/api/types";
import { useDeleteQuery, useEvalQueries, useGenerate, usePatchQuery } from "./evalApi";

/** One gold set: approve or delete drafted questions, and draft more from the corpus as a chosen principal. */
export function EvalSetPage({ id }: { id: string }) {
  const { data, isError } = useEvalQueries(id);
  const patch = usePatchQuery(), del = useDeleteQuery(), gen = useGenerate();
  const [n, setN] = useState(20);
  const [who, setWho] = useState<Person | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fail = (e: unknown) => setError(errorText(e));
  if (isError) return <p role="alert">This set couldn't be loaded.</p>;
  const rows = [...(data ?? [])].sort((a, b) => (a.status === b.status ? 0 : a.status === "draft" ? -1 : 1));
  return (
    <div className="admin-page">
      <p><Link to="/admin/eval">← Eval</Link></p>
      <p className="sub">Generated questions are drafts: they stay out of runs until you approve them. The generator only reads documents the chosen principal can see and whose label allows LLM use.</p>
      {error && <p className="chipin-err" role="alert">{error}</p>}
      <form className="filters" onSubmit={(e) => { e.preventDefault(); if (who) { setError(null); gen.mutate({ id, n, principal: who.id }, { onSuccess: () => toast("Question generation queued."), onError: fail }); } }}>
        <label>Questions<input className="inp" type="number" min={1} max={200} value={n} onChange={(e) => setN(Number(e.target.value))} /></label>
        <DirectoryPicker<Person> kind="users" label="Generate as principal" exclude={[]} onPick={setWho} />
        {who && <span>as <b>{who.username}</b></span>}
        <Button type="submit" variant="primary" disabled={!who || gen.isPending || n < 1 || n > 200}>Generate drafts</Button>
      </form>
      <div className="tok-wrap"><table className="agent-table admin-table" aria-label="Queries">
        <thead><tr><th>Status</th><th>Query</th><th>Kind</th><th>Principals</th><th>Gold</th><th /></tr></thead>
        <tbody>{rows.map((q) => (
          <tr key={q.id}>
            <td>{q.status === "draft" ? <span className="badge-o">Draft</span> : "Active"}</td>
            <td className="trunc" title={q.text}>{q.text}</td>
            <td>{q.kind}</td>
            <td><OverflowList className="evchips" items={q.principal_names} max={2} menuLabel={`More principals for ${q.key}`} renderItem={(p) => <span key={p} className="cchip"><span className="cchip-t">{p}</span></span>} /></td>
            <td>{q.relevant.length}</td>
            <td className="acts">
              {q.status === "draft" && <Button onClick={() => { setError(null); patch.mutate({ id: q.id, status: "active" }, { onError: fail }); }}>Approve</Button>}
              <Button onClick={() => { setError(null); del.mutate(q.id, { onError: fail }); }}>Delete</Button>
            </td>
          </tr>
        ))}</tbody>
      </table></div>
      {!rows.length && <p className="sub">No queries yet.</p>}
    </div>
  );
}
