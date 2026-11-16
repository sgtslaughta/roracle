import { X } from "lucide-react";
import { type Ref, useId, useImperativeHandle, useState } from "react";
import { OverflowList } from "@/components/OverflowList";
import { Button } from "@/components/ui/button";

interface Props { label: string; help?: string; values: string[]; onChange: (v: string[]) => void; validate?: (v: string) => string | null; placeholder?: string; commitRef?: Ref<() => string[] | null> }

/** Type a value and press Enter (or Add); values show as removable chips, at most 6 inline. */
export function ChipInput({ label, help, values, onChange, validate, placeholder, commitRef }: Props) {
  const id = useId();
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  /** Commit pending text; returns the resulting values, or null when the text is invalid. */
  const commit = (): string[] | null => {
    const v = text.trim();
    if (!v) return values;
    const bad = validate?.(v) ?? null;
    setErr(bad);
    if (bad) return null;
    const next = values.includes(v) ? values : [...values, v];
    onChange(next);
    setText("");
    return next;
  };
  useImperativeHandle(commitRef, () => commit);
  const add = () => { commit(); };
  const chip = (v: string) => (
    <span key={v} className="cchip" title={v}>
      <span className="cchip-t">{v}</span>
      <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}><X aria-hidden /></button>
    </span>
  );
  return (
    <div className="chipin">
      <label htmlFor={id}>{label}</label>
      <div className="chipin-row">
        <input
          id={id} value={text} placeholder={placeholder} aria-invalid={!!err} aria-describedby={`${id}-h`}
          onChange={(e) => { setText(e.target.value); setErr(null); }}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
        />
        <Button onClick={add}>Add</Button>
      </div>
      {err ? <p className="chipin-err" role="alert" id={`${id}-h`}>{err}</p> : help && <p className="chipin-help" id={`${id}-h`}>{help}</p>}
      {values.length > 0 && <OverflowList items={values} max={6} renderItem={chip} menuLabel={`More ${label.toLowerCase()}`} getLabel={(v) => v} />}
    </div>
  );
}
