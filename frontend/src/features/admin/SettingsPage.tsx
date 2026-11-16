import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { SettingValue } from "@/api/admin";
import { Button } from "@/components/ui/button";
import { invalidText } from "@/features/agents/api";
import { ChipInput } from "@/features/ask/ChipInput";
import { humanBytes } from "@/lib/bytes";
import { useSaveSetting, useSettings } from "./dataApi";

/** Backend bounds (runtime.SettingsIn); the server stays authoritative. */
const MiB = 1024 * 1024;
const BOUNDS: Record<string, { min?: number; max?: number; step?: number }> = {
  chat_passages: { min: 4, max: 12 }, chat_note_min_upvotes: { min: 0 }, chat_note_min_ratio: { min: 0, max: 1, step: 0.05 },
  rerank_depth: { min: 10, max: 100 }, near_dup_threshold: { min: 0.5, max: 1, step: 0.01 }, near_dup_summary_threshold: { min: 0.5, max: 1, step: 0.01 },
  max_upload_bytes: { min: MiB, max: 5 * 1024 * MiB }, upload_quota_bytes: { min: 0 }, rate_limit_per_minute: { min: 0 },
};
const BYTES = new Set(["max_upload_bytes", "upload_quota_bytes"]);

function Row({ name, value, source, models }: { name: string; value: SettingValue; source: "env" | "override"; models: string[] }) {
  const [draft, setDraft] = useState<SettingValue | string>(Array.isArray(value) ? value : typeof value === "number" ? String(value) : value);
  const [err, setErr] = useState<string | null>(null);
  const save = useSaveSetting();
  const num = typeof value === "number";
  const parsed: SettingValue = num ? Number(draft) : (draft as SettingValue);
  const empty = num && (draft === "" || !Number.isFinite(Number(draft)));
  const dirty = JSON.stringify(parsed) !== JSON.stringify(value);
  const run = (v: SettingValue | null, done: string) => {
    setErr(null);
    save.mutate({ key: name, value: v }, {
      onSuccess: () => toast(done),
      onError: (e) => setErr(e instanceof ApiError && e.status === 422 ? invalidText(e) : "The setting couldn't be saved. Try again."),
    });
  };
  const b = BOUNDS[name];
  const id = `set-${name}`;
  let input;
  if (typeof value === "boolean") input = <label className="ck"><input id={id} type="checkbox" checked={draft as boolean} onChange={(e) => setDraft(e.target.checked)} />Enabled</label>;
  else if (Array.isArray(value)) input = <ChipInput label={`${name} models`} values={draft as string[]} onChange={setDraft} placeholder="Add a model name" />;
  else if (num) input = <input id={id} type="number" value={draft as string} min={b?.min} max={b?.max} step={b?.step ?? 1} onChange={(e) => setDraft(e.target.value)} />;
  else if (name === "chat_default_model") input = (
    <select id={id} value={draft as string} onChange={(e) => setDraft(e.target.value)}>
      {[...new Set([...models, value as string])].map((m) => <option key={m}>{m}</option>)}
    </select>
  );
  else input = <input id={id} value={draft as string} onChange={(e) => setDraft(e.target.value)} />;
  return (
    <tr>
      <th scope="row">{Array.isArray(value) ? name : <label htmlFor={id}>{name}</label>}</th>
      <td>
        {input}
        {BYTES.has(name) && num && <small className="sub"> {draft !== "" && Number.isFinite(Number(draft)) ? humanBytes(Number(draft)) : ""}</small>}
        {empty && <p className="sub">Enter a number</p>}
        {err && <p role="alert" className="sub">{err}</p>}
      </td>
      <td><span className={`pill ${source === "override" ? "review" : ""}`}>{source}</span></td>
      <td className="acts">
        <Button disabled={!dirty || empty || save.isPending} aria-label={`Save ${name}`} onClick={() => run(parsed, `${name} saved.`)}>Save</Button>
        {source === "override" && <Button variant="ghost" disabled={save.isPending} aria-label={`Reset ${name} to env`} onClick={() => run(null, `${name} reset to env.`)}>Reset to env</Button>}
      </td>
    </tr>
  );
}

export function SettingsPage() {
  const { data, isError } = useSettings();
  if (isError) return <p role="alert">Settings couldn't be loaded. Reload to try again.</p>;
  if (!data) return <div aria-busy="true" />;
  const models = Array.isArray(data.chat_models?.value) ? data.chat_models.value : [];
  return (
    <div className="admin-page">
      <h2>Settings</h2>
      <p className="sub">Overrides win over the server environment. Reset returns a setting to its environment value.</p>
      <div className="tok-wrap">
        <table className="agent-table admin-table">
          <thead><tr><th>Setting</th><th>Value</th><th>Source</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {Object.entries(data).map(([k, s]) => (
              // key includes the server value so a saved row re-seeds its draft
              <Row key={`${k}:${JSON.stringify(s.value)}:${s.source}`} name={k} value={s.value} source={s.source} models={models} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
