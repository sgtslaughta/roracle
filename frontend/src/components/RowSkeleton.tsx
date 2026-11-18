/** Placeholder rows at the final row height while a list loads; the label is for screen readers. */
export function RowSkeleton({ n = 3, label = "Loading…" }: { n?: number; label?: string }) {
  return (
    <div className="rowskel" role="status" aria-busy="true">
      <span className="sr-only">{label}</span>
      {Array.from({ length: n }, (_, i) => <i key={i} aria-hidden />)}
    </div>
  );
}
