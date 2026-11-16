import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { invalidText } from "@/features/agents/api";
import { useMergeTags, useTags } from "./dataApi";

const SHOWN = 100;
const MAX_SOURCES = 50;
/** Mirrors backend normalize_tag so "PDE" and "pde" count as the same tag. */
const norm = (s: string) => s.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}\p{M}]+/gu, "-").replace(/^-+|-+$/g, "");

export function TagsPage() {
  const { data, isError } = useTags();
  const merge = useMergeTags();
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (isError) return <p role="alert">Tags couldn't be loaded. Reload to try again.</p>;
  const needle = filter.trim().toLowerCase();
  const matches = (data ?? []).filter((t) => !needle || t.tag.toLowerCase().includes(needle));
  const t = target.trim();
  const problem = !norm(t) ? "Choose a target tag." : picked.some((p) => norm(p) === norm(t)) ? "The target can't be one of the sources." : picked.length > MAX_SOURCES ? `Merge at most ${MAX_SOURCES} tags at once.` : null;
  const toggle = (tag: string) => setPicked((p) => (p.includes(tag) ? p.filter((x) => x !== tag) : [...p, tag]));
  const go = () =>
    merge.mutate({ sources: picked, target: t }, {
      onSuccess: (r) => { toast(`${r.documents} documents updated`); setPicked([]); setTarget(""); setErr(null); },
      onError: (e) => setErr(e instanceof ApiError && e.status === 422 ? invalidText(e) : "The tags couldn't be merged. Try again."),
      onSettled: () => setConfirm(false),
    });

  return (
    <div className="admin-page">
      <h2>Tags</h2>
      <div className="filters">
        <label>Filter tags <input value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
      </div>
      <div className="filters">
        <label>Merge into <input list="tag-targets" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="Existing or new tag" /></label>
        <datalist id="tag-targets">{matches.slice(0, SHOWN).map((x) => <option key={x.tag} value={x.tag} />)}</datalist>
        <Button disabled={!picked.length || !!problem} onClick={() => setConfirm(true)}>Merge {picked.length || ""} selected</Button>
        {picked.length > 0 && problem && <small className="sub">{problem}</small>}
      </div>
      {err && <p role="alert">{err}</p>}
      {data && !matches.length && <p className="sub">No tags match.</p>}
      {matches.length > 0 && (
        <div className="tok-wrap">
          <table className="agent-table admin-table">
            <thead><tr><th>Merge</th><th>Tag</th><th>Documents</th></tr></thead>
            <tbody>
              {matches.slice(0, SHOWN).map((x) => (
                <tr key={x.tag}>
                  <td><input type="checkbox" aria-label={`Select ${x.tag}`} checked={picked.includes(x.tag)} onChange={() => toggle(x.tag)} /></td>
                  <td className="trunc" title={x.tag}>{x.tag}</td><td>{x.documents}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {matches.length > SHOWN && <p className="sub">Showing {SHOWN} of {matches.length}. Filter to narrow the list.</p>}
        </div>
      )}
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Merge tags?" confirm="Merge" onConfirm={go} busy={merge.isPending}>
        {picked.length} {picked.length === 1 ? "tag" : "tags"} will be replaced by “{t}” on every document that has them. This can't be undone.
      </ConfirmDialog>
    </div>
  );
}
