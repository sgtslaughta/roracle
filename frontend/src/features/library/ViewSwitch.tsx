import { VIEWS, type View } from "@/lib/searchParams";

const LABEL: Record<View, string> = { list: "List", compact: "Compact", table: "Table" };

export function ViewSwitch({ value, onChange }: { value: View; onChange: (v: View) => void }) {
  return (
    <div className="vswitch" role="radiogroup" aria-label="View">
      {VIEWS.map((v) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}>{LABEL[v]}</button>
      ))}
    </div>
  );
}
