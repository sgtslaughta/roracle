import { useEffect, useState } from "react";
import type { FacetValue } from "@/api/types";
import { INVERTED } from "./RangeFields";

const W = 240, H = 44;
type Props = { buckets: FacetValue[]; min?: number; max?: number; onCommit: (min?: number, max?: number) => void };

/** Year histogram with range sliders. Dragging only updates the caption; the URL is written on release. */
export function YearFacet({ buckets, min, max, onCommit }: Props) {
  const years = buckets.map((b) => Number(b.value)).filter(Number.isFinite);
  const first = years.length ? Math.min(...years) : (min ?? 0), last = years.length ? Math.max(...years) : (max ?? 0);
  const [lo, setLo] = useState(min ?? first);
  const [hi, setHi] = useState(max ?? last);
  const [txt, setTxt] = useState<[string, string]>([String(min ?? first), String(max ?? last)]);
  const [err, setErr] = useState(false);
  useEffect(() => { setLo(min ?? first); setHi(max ?? last); setTxt([String(min ?? first), String(max ?? last)]); setErr(false); }, [min, max, first, last]);
  if (!buckets.length && min === undefined && max === undefined) return null;

  const top = Math.max(1, ...buckets.map((b) => b.count));
  const bw = W / Math.max(1, buckets.length);
  // only a real change writes the URL (a Tab/Shift keyup or a plain click must not)
  const release = () => { if (lo !== (min ?? first) || hi !== (max ?? last)) onCommit(lo, hi); };
  const num = (i: 0 | 1, v: string) => setTxt((t) => (i ? [t[0], v] : [v, t[1]]));
  const commitNums = () => {
    // an untouched end keeps its current value (unset stays unset)
    const val = (t: string, cur: number | undefined, def: number) => (t === String(cur ?? def) ? cur : t.trim() === "" ? undefined : Number(t));
    const a = val(txt[0], min, first), b = val(txt[1], max, last);
    const bad = (n?: number) => n !== undefined && !(Number.isInteger(n) && n >= 0 && n <= 3000);
    if (bad(a) || bad(b) || (a !== undefined && b !== undefined && a > b)) return setErr(true);
    setErr(false);
    if (a !== min || b !== max) onCommit(a, b);
  };
  const nkey = (e: React.KeyboardEvent) => e.key === "Enter" && commitNums();

  return (
    <div className="fgroup yfacet" role="group" aria-label="Year">
      <h4>Year <span className="ycap" aria-live="polite">{lo}–{hi}</span></h4>
      <svg className="yhist" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Publication years histogram" data-first={first} preserveAspectRatio="none">
        {buckets.map((b, i) => {
          const y = Number(b.value), h = Math.max(2, (b.count / top) * (H - 2));
          return (
            <rect
              key={b.value} className="ybar" x={i * bw + 0.5} y={H - h} width={Math.max(1, bw - 1)} height={h}
              data-in={y >= lo && y <= hi || undefined} role="button" tabIndex={0} aria-label={`Year ${b.value}, ${b.count} documents`}
              onClick={() => onCommit(y, y)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onCommit(y, y)}
            />
          );
        })}
      </svg>
      <div className="yrange">
        <input type="range" aria-label="Earliest year" min={first} max={last} value={lo}
          onChange={(e) => setLo(Math.min(Number(e.target.value), hi))} onPointerUp={release} onKeyUp={release} />
        <input type="range" aria-label="Latest year" min={first} max={last} value={hi}
          onChange={(e) => setHi(Math.max(Number(e.target.value), lo))} onPointerUp={release} onKeyUp={release} />
      </div>
      <div className="rrow">
        <input type="number" aria-label="Earliest year" aria-invalid={err || undefined} value={txt[0]} min={0} max={3000}
          onChange={(e) => num(0, e.target.value)} onBlur={commitNums} onKeyDown={nkey} />
        <span aria-hidden>–</span>
        <input type="number" aria-label="Latest year" aria-invalid={err || undefined} value={txt[1]} min={0} max={3000}
          onChange={(e) => num(1, e.target.value)} onBlur={commitNums} onKeyDown={nkey} />
      </div>
      {err && <p className="rerr" role="alert">{INVERTED}</p>}
    </div>
  );
}
