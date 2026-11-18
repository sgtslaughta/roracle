import { useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { Connector } from "@/api/admin";
import { ChipInput } from "@/features/ask/ChipInput";
import { invalidText } from "@/features/agents/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useLabels } from "@/features/labels/api";
import { useSaveConnector } from "./api";
import { buildBody, initialForm, isHttpUrl, MASK, type FormState } from "./connectorBody";

export function ConnectorDialog({ connector, open, onOpenChange }: { connector?: Connector; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Radix unmounts closed content, so the form (including a typed secret) is dropped on close */}
      <DialogContent className="w-[min(560px,calc(100vw-32px))] max-h-[calc(100dvh-32px)] overflow-y-auto">
        <ConnectorForm connector={connector} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

const Field = ({ label, help, children }: { label: string; help?: string; children: ReactNode }) => (
  <label>{label}{children}{help && <span className="help">{help}</span>}</label>
);

function ConnectorForm({ connector, onDone }: { connector?: Connector; onDone: () => void }) {
  const editing = !!connector;
  const save = useSaveConnector();
  const [f, setF] = useState<FormState>(() => initialForm(connector));
  const [err, setErr] = useState<string | null>(null);
  const labels = useLabels().data ?? [];
  const includeRef = useRef<() => string[] | null>(null);
  const urlsRef = useRef<() => string[] | null>(null);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((x) => ({ ...x, [k]: v }));
  const text = (k: "name" | "org_unit" | "schedule" | "endpoint" | "bucket" | "prefix" | "region" | "access_key" | "path" | "max_depth" | "max_pages") =>
    ({ value: f[k], onChange: (e: React.ChangeEvent<HTMLInputElement>) => set(k, e.target.value) });

  const submit = () => {
    setErr(null);
    const include = f.kind === "fs" ? includeRef.current?.() ?? null : f.include;
    const start_urls = f.kind === "http" ? urlsRef.current?.() ?? null : f.start_urls;
    if (!include || !start_urls) return; // a pending chip was invalid; ChipInput shows why
    const r = buildBody(f, connector, { include, start_urls });
    if ("error" in r) return setErr(r.error);
    save.mutate({ id: connector?.id, body: r.body }, {
      onSuccess: () => { toast(editing ? "Connector saved." : "Connector created."); onDone(); },
      onError: (e) => setErr(e instanceof ApiError && (e.status === 422 || e.status === 409) ? invalidText(e) : "The connector couldn't be saved. Try again."),
    });
  };

  return (
    <form className="admin-form agent-form" onSubmit={(e) => { e.preventDefault(); submit(); }} noValidate>
      <DialogTitle>{editing ? `Edit ${connector.name}` : "New connector"}</DialogTitle>
      <DialogDescription>Documents it ingests take the visibility below.</DialogDescription>
      <Field label="Name"><input {...text("name")} maxLength={200} autoFocus /></Field>
      <Field label="Kind">
        <select value={f.kind} disabled={editing} onChange={(e) => set("kind", e.target.value as FormState["kind"])}>
          <option value="s3">S3 bucket</option><option value="fs">Folder on the server</option><option value="http">Website</option>
        </select>
      </Field>
      {f.kind === "s3" && (
        <>
          <Field label="Endpoint"><input {...text("endpoint")} placeholder="https://s3.example.org" inputMode="url" /></Field>
          <div className="agent-row">
            <Field label="Bucket"><input {...text("bucket")} /></Field>
            <Field label="Region"><input {...text("region")} /></Field>
          </div>
          <Field label="Prefix"><input {...text("prefix")} placeholder="papers/" /></Field>
          <div className="agent-row">
            <Field label="Access key"><input {...text("access_key")} autoComplete="off" /></Field>
            <Field label="Secret key" help={editing && f.secret_key === MASK ? "Stored. Leave as *** to keep it, type to replace it, clear to remove it." : "Write-only. It is never shown again."}>
              <input type="password" autoComplete="new-password" value={f.secret_key} onChange={(e) => set("secret_key", e.target.value)} />
            </Field>
          </div>
        </>
      )}
      {f.kind === "fs" && (
        <>
          <Field label="Path" help="Must be inside a folder the server allows."><input {...text("path")} placeholder="/data/papers" /></Field>
          <ChipInput label="Include patterns" values={f.include} onChange={(v) => set("include", v)} commitRef={includeRef} placeholder="*.pdf" help="Glob patterns, 1 to 20. Press Enter to add." />
        </>
      )}
      {f.kind === "http" && (
        <>
          <ChipInput
            label="Start URLs" values={f.start_urls} onChange={(v) => set("start_urls", v)} commitRef={urlsRef} placeholder="https://example.org/papers"
            validate={(v) => (isHttpUrl(v) ? null : "Enter an http(s) URL.")} help="1 to 20 URLs. Press Enter to add."
          />
          <div className="agent-row">
            <Field label="Max depth (0 to 3)"><input type="number" min={0} max={3} {...text("max_depth")} /></Field>
            <Field label="Max pages (1 to 1000)"><input type="number" min={1} max={1000} {...text("max_pages")} /></Field>
          </div>
          <label className="ck"><input type="checkbox" checked={f.respect_robots} onChange={(e) => set("respect_robots", e.target.checked)} />Respect robots.txt</label>
          <label className="ck"><input type="checkbox" checked={f.ingest_html} onChange={(e) => set("ingest_html", e.target.checked)} />Ingest HTML pages too</label>
        </>
      )}
      <Field label="Visible to" help={editing && (f.visibility !== connector.visibility || (f.visibility === "org_unit" && f.org_unit.trim() !== connector.org_unit)) ? "Documents it already ingested change visibility too." : undefined}>
        <select value={f.visibility} onChange={(e) => set("visibility", e.target.value as FormState["visibility"])}>
          <option value="private">Only me</option><option value="org_unit">An org unit</option><option value="everyone">Everyone</option>
        </select>
      </Field>
      {f.visibility === "org_unit" && <Field label="Org unit"><input {...text("org_unit")} placeholder="/Mathematics/Spectral Theory" /></Field>}
      {labels.length > 0 && (
        <Field label="Label for new documents" help="Applies to documents it ingests from now on.">
          <select value={f.label_id} onChange={(e) => set("label_id", e.target.value)}>
            <option value="">Default label{labels.find((l) => l.is_default) ? ` (${labels.find((l) => l.is_default)!.name})` : ""}</option>
            {labels.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </Field>
      )}
      <Field label="Run every (minutes)" help="5 to 10080. Leave empty to run only when you press Run now."><input type="number" min={5} max={10080} {...text("schedule")} /></Field>
      <label className="ck"><input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} />Enabled</label>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="foot">
        <Button variant="ghost" type="button" onClick={onDone}>Cancel</Button>
        <Button variant="primary" type="submit" disabled={save.isPending}>{editing ? "Save" : "Create"}</Button>
      </div>
    </form>
  );
}
