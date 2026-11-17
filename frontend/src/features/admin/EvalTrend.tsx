import type { EvalRunItem } from "./evalApi";

export const TREND_COLOURS = ["var(--primary)", "var(--terra)", "var(--good)", "var(--apricot)", "var(--rose)"];
const W = 640, H = 220, L = 40, R = 12, T = 12, B = 28;

/** Inline-SVG trend: one line per metric across runs (oldest first), y fixed to 0..1. Colour is never the only cue: each series has its own marker label in the legend and every point carries a tooltip. */
export function EvalTrend({ runs, metrics }: { runs: EvalRunItem[]; metrics: string[] }) {
  const pts = runs.filter((r) => r.metrics);
  const x = (i: number) => L + (pts.length < 2 ? (W - L - R) / 2 : (i * (W - L - R)) / (pts.length - 1));
  const y = (v: number) => T + (1 - Math.min(Math.max(v, 0), 1)) * (H - T - B);
  return (
    <figure className="evtrend">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Trend of ${metrics.join(", ") || "no metrics"} over ${pts.length} runs`}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" /><text x={L - 6} y={y(t) + 4} textAnchor="end" className="tick">{t.toFixed(2)}</text></g>
        ))}
        {pts.map((r, i) => <text key={r.id} x={x(i)} y={H - 8} textAnchor="middle" className="tick">{r.started_at.slice(5, 10)}</text>)}
        {metrics.map((m, k) => {
          const c = TREND_COLOURS[k % TREND_COLOURS.length];
          const have = pts.map((r, i) => ({ r, i, v: r.metrics!.overall[m] })).filter((p) => typeof p.v === "number");
          return (
            <g key={m} data-testid={`series-${m}`} stroke={c} fill={c}>
              <polyline fill="none" strokeWidth="2" points={have.map((p) => `${x(p.i)},${y(p.v)}`).join(" ")} />
              {have.map((p) => (
                <circle key={p.r.id} data-testid={`pt-${m}`} cx={x(p.i)} cy={y(p.v)} r="3.5"><title>{`${m} ${p.v.toFixed(3)} · ${p.r.label || p.r.trigger} · ${p.r.started_at.slice(0, 10)}`}</title></circle>
              ))}
            </g>
          );
        })}
      </svg>
      <figcaption className="evlegend">
        {metrics.map((m, k) => <span key={m}><i style={{ background: TREND_COLOURS[k % TREND_COLOURS.length] }} aria-hidden />{m}</span>)}
      </figcaption>
    </figure>
  );
}
