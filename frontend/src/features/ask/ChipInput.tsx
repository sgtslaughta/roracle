import { X } from "lucide-react";
import { useId, useState } from "react";
import { OverflowList } from "@/components/OverflowList";
import { Button } from "@/components/ui/button";

interface Props { label: string; help?: string; values: string[]; onChange: (v: string[]) => void; validate?: (v: string) => string | null; placeholder?: string }

/** Type a value and press Enter (or Add); values show as removable chips, at most 6 inline. */
export function ChipInput({ label, help, values, onChange, validate, placeholder }: Props) {
  const id = useId();
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const add = () => {
    const v = text.trim();
    if (!v) return;
    const bad = validate?.(v) ?? null;
    setErr(bad);
    if (bad) return;
    if (!values.includes(v)) onChange([...values, v]);
    setText("");
  };
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
