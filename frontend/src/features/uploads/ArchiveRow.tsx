import type { UploadArchive } from "@/api/types";

export type ArchiveResult = { name: string; result: UploadArchive };

/** Outcome of an unpacked archive: how many papers were added and why the others were not. */
export function ArchiveRow({ name, result }: ArchiveResult) {
  const added = result.documents.length;
  const skipped = result.skipped.length;
  return (
    <div className="trow" data-testid="archive-row">
      <span className="vis">
        <strong>{name}</strong>: {added} {added === 1 ? "paper" : "papers"} added, {skipped} skipped
        {skipped > 0 && (
          <details>
            <summary>Why were files skipped?</summary>
            <ul>{result.skipped.map((s, i) => <li key={i} title={s.reason}>{s.name}: {s.reason}</li>)}</ul>
          </details>
        )}
      </span>
    </div>
  );
}
