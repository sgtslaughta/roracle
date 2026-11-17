import { useId, useState } from "react";
import type { Label } from "@/api/types";
import { Button } from "@/components/ui/button";

export const REASON_MIN = 3;

interface Props {
  labels: Label[];
  /** the label being replaced (null: none yet, e.g. a note that inherits) */
  current: Label | null;
  isAdmin: boolean;
  /** options ranked below this are never offered (a note cannot go below its document) */
  floor?: Label;
  onApply: (labelId: string, reason?: string) => void;
  pending?: boolean;
  error?: string | null;
}

/** Owner/author: raise only. Admin: any label, with a reason when lowering from a `reason_on_lower` label. */
export function LabelPicker({ labels, current, isAdmin, floor, onApply, pending, error }: Props) {
  const id = useId();
  const sorted = [...labels].sort((a, b) => a.rank - b.rank);
  const [value, setValue] = useState(current?.id ?? floor?.id ?? sorted[0]?.id ?? "");
  const [reason, setReason] = useState("");
  const sel = sorted.find((l) => l.id === value);
  const lowering = !!current && !!sel && sel.rank < current.rank;
  const required = lowering && current.gates.reason_on_lower;
  const why = (l: Label) => (floor && l.rank < floor.rank ? "below the document label" : current && l.rank < current.rank && !isAdmin ? "only admins can lower a label" : "");
  const hasDisabled = sorted.some((l) => why(l));
  const bad = !sel || sel.id === current?.id || !!why(sel) || (required && reason.trim().length < REASON_MIN);
  return (
    <form className="lpick" onSubmit={(e) => { e.preventDefault(); if (!bad) onApply(value, lowering && reason.trim() ? reason.trim() : undefined); }}>
      <label htmlFor={`${id}-sel`}>Sensitivity label</label>
      <select id={`${id}-sel`} className="sel" value={value} aria-describedby={hasDisabled ? `${id}-hint` : undefined} onChange={(e) => setValue(e.target.value)}>
        {sorted.map((l) => <option key={l.id} value={l.id} disabled={!!why(l)}>{l.name}{why(l) ? ` (${why(l)})` : ""}</option>)}
      </select>
      {hasDisabled && <p id={`${id}-hint`} className="sub">Greyed-out labels can't be chosen: {isAdmin ? "a note can't be less sensitive than its document." : "only admins can lower a label."}</p>}
      {lowering && (
        <>
          <label htmlFor={`${id}-why`}>{required ? "Reason for lowering (required)" : "Reason for lowering (optional)"}</label>
          <textarea id={`${id}-why`} className="ta short" maxLength={500} required={required} value={reason} onChange={(e) => setReason(e.target.value)} />
          {required && reason.trim().length < REASON_MIN && <p className="sub">At least {REASON_MIN} characters.</p>}
        </>
      )}
      {error && <p className="chipin-err" role="alert">{error}</p>}
      <div className="foot"><Button variant="primary" type="submit" disabled={bad || pending}>Apply label</Button></div>
    </form>
  );
}
