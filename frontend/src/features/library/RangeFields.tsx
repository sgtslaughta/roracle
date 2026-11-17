import { useEffect, useId, useState } from "react";

export const INVERTED = "Earliest must be before latest";
type Props = {
  label: string;
  kind: "number" | "date";
  min?: number | string;
  max?: number | string;
  onCommit: (min?: number | string, max?: number | string) => void;
};

const parse = (kind: Props["kind"], t: string): number | string | undefined | null => {
  if (t.trim() === "") return undefined;
  if (kind === "date") return /^\d{4}-\d{2}-\d{2}$/.test(t) ? t : null;
  const n = Number(t);
  return Number.isInteger(n) && n >= 0 ? n : null;
};

/** Two inputs; commits on blur or Enter. Invalid or inverted input shows an error and is not committed. */
export function RangeFields({ label, kind, min, max, onCommit }: Props) {
  const id = useId();
  const [lo, setLo] = useState(min == null ? "" : String(min));
  const [hi, setHi] = useState(max == null ? "" : String(max));
  const [err, setErr] = useState(false);
  useEffect(() => { setLo(min == null ? "" : String(min)); setHi(max == null ? "" : String(max)); setErr(false); }, [min, max]);

  const commit = (a = lo, b = hi) => {
    const x = parse(kind, a), y = parse(kind, b);
    if (x === null || y === null || (x !== undefined && y !== undefined && x > y)) return setErr(true);
    setErr(false);
    if (x !== min || y !== max) onCommit(x, y);
  };
  const field = (name: string, v: string, set: (s: string) => void) => (
    <input
      type={kind} min={kind === "number" ? 0 : undefined} aria-label={name} aria-invalid={err || undefined} aria-describedby={err ? `${id}-e` : undefined}
      value={v} onChange={(e) => set(e.target.value)} onBlur={() => commit()} onKeyDown={(e) => e.key === "Enter" && commit()}
    />
  );
  return (
    <div className="rfield" role="group" aria-label={label}>
      <h4>{label}</h4>
      <div className="rrow">{field("Earliest", lo, setLo)}<span aria-hidden>–</span>{field("Latest", hi, setHi)}</div>
      {err && <p id={`${id}-e`} className="rerr" role="alert">{INVERTED}</p>}
    </div>
  );
}
