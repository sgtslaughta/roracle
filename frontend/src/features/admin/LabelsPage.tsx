import { ArrowDown, ArrowUp, GripVertical, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import type { AdminLabel } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { RowMenu } from "@/components/RowMenu";
import { SensitivityBadge } from "@/components/SensitivityBadge";
import { Button } from "@/components/ui/button";
import { useLabels } from "@/features/labels/api";
import { GATE_TEXT, LabelForm, type GateKey } from "./LabelForm";
import { useDeleteLabel, usePatchLabel, useReorderLabels } from "./labelsApi";

const MAX_LABELS = 20;

/** Admin: define the ordered sensitivity labels. Top = least sensitive. Gates may only switch on as sensitivity rises (the API enforces it). */
export function LabelsPage() {
  const { data, isError } = useLabels();
  const patch = usePatchLabel(), del = useDeleteLabel(), reorder = useReorderLabels();
  const [form, setForm] = useState<AdminLabel | "new" | null>(null);
  const [doomed, setDoomed] = useState<AdminLabel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ from: string; over: string | null } | null>(null);
  const items = data ?? [];
  const fail = (e: unknown) => setError(errorText(e));

  const move = (id: string, to: number) => {
    const ids = items.map((l) => l.id), at = ids.indexOf(id);
    if (to < 0 || to >= ids.length || at < 0) return;
    ids.splice(to, 0, ...ids.splice(at, 1));
    setError(null);
    reorder.mutate(ids, { onError: fail });
  };
  const toggle = (l: AdminLabel, g: GateKey, on: boolean) => {
    setError(null);
    patch.mutate({ id: l.id, gates: { [g]: on } }, { onError: fail });
  };

  if (isError) return <p role="alert">Labels couldn't be loaded. Reload to try again.</p>;
  return (
    <div>
      <div className="uhead-row">
        <p className="sub">Labels mark how a document or note must be handled. They only ever restrict; access still comes from visibility and shares. Drag a row, or use the arrows, to reorder: the bottom is the most sensitive.</p>
        <Button variant="primary" disabled={items.length >= MAX_LABELS} title={items.length >= MAX_LABELS ? `At most ${MAX_LABELS} labels` : undefined} onClick={() => setForm("new")}><Plus aria-hidden />New label</Button>
      </div>
      {error && <p className="chipin-err" role="alert">{error}</p>}
      <ol className="lrows" aria-label="Sensitivity labels, least sensitive first">
        {items.map((l, i) => (
          <li
            key={l.id} data-row className={`lrow${drag?.over === l.id ? " drag-over" : ""}`} draggable
            onDragStart={() => setDrag({ from: l.id, over: null })}
            onDragOver={(e) => { e.preventDefault(); if (drag && drag.over !== l.id) setDrag({ ...drag, over: l.id }); }}
            onDragEnd={() => setDrag(null)}
            onDrop={(e) => { e.preventDefault(); if (drag && drag.from !== l.id) move(drag.from, i); setDrag(null); }}
          >
            <span className="acts">
              <GripVertical aria-hidden />
              <button className="icon-btn" aria-label={`Move ${l.name} up`} disabled={i === 0 || reorder.isPending} onClick={() => move(l.id, i - 1)}><ArrowUp aria-hidden /></button>
              <button className="icon-btn" aria-label={`Move ${l.name} down`} disabled={i === items.length - 1 || reorder.isPending} onClick={() => move(l.id, i + 1)}><ArrowDown aria-hidden /></button>
            </span>
            <div style={{ minWidth: 0 }}>
              <div className="rlabel"><SensitivityBadge label={l} />{l.is_default && <span className="badge-o">Default</span>}</div>
              {l.description && <small className="sub">{l.description}</small>}
              <fieldset className="gates" aria-label={`Gates for ${l.name}`} style={{ border: 0, padding: 0, margin: "4px 0 0" }}>
                {(Object.keys(GATE_TEXT) as GateKey[]).map((g) => (
                  <label key={g} className="ck"><input type="checkbox" checked={l.gates[g]} disabled={patch.isPending} aria-label={`${l.name}: ${GATE_TEXT[g]}`} onChange={(e) => toggle(l, g, e.target.checked)} />{GATE_TEXT[g]}</label>
                ))}
              </fieldset>
            </div>
            <span className="acts">
              <Button onClick={() => setForm(l)}>Edit</Button>
              <RowMenu label={`Actions for ${l.name}`} actions={[
                { label: "Edit", onSelect: () => setForm(l) },
                { label: "Move up", onSelect: () => move(l.id, i - 1) },
                { label: "Move down", onSelect: () => move(l.id, i + 1) },
                { label: "Delete…", danger: true, onSelect: () => setDoomed(l) },
              ]} />
            </span>
          </li>
        ))}
      </ol>
      {form && <LabelForm label={form === "new" ? undefined : form} top={items.at(-1)} onClose={() => setForm(null)} />}
      <ConfirmDialog open={!!doomed} onOpenChange={(o) => !o && setDoomed(null)} title={`Delete “${doomed?.name}”?`} confirm="Delete label" busy={del.isPending}
        onConfirm={() => doomed && del.mutate(doomed.id, { onSuccess: () => { toast("Label deleted."); setDoomed(null); }, onError: (e) => { setDoomed(null); fail(e); } })}>
        Only labels no document, note or connector uses, and that aren't the default, can be deleted.
      </ConfirmDialog>
    </div>
  );
}
