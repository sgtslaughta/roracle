import type { DocumentDetail } from "@/api/types";
import { SensitivityBadge } from "@/components/SensitivityBadge";
import { Identifiers } from "./Identifiers";
import { fmtDateTime } from "@/lib/dates";

const VISIBILITY: Record<DocumentDetail["visibility"], string> = {
  everyone: "Everyone", org_unit: "Your org unit", private: "Only the owner", shared: "Shared with people and groups",
};

/** AI summary and the paper's own abstract. Both are null for agents on exclude_llm documents. */
export function SummaryTab({ d }: { d: DocumentDetail }) {
  return (
    <div className="refs docinfo">
      <section aria-labelledby="sum-h">
        <h3 id="sum-h">AI summary</h3>
        {d.summary ? <p>{d.summary}</p> : <p className="refnote">No summary yet. One is written when ingest finishes.</p>}
      </section>
      <section aria-labelledby="abs-h">
        <h3 id="abs-h">Abstract</h3>
        {d.abstract ? <p>{d.abstract}</p> : <p className="refnote">No abstract was found in this document.</p>}
      </section>
    </div>
  );
}

/** Everything known about the document, as one definition list. Empty fields are left out. */
export function DetailsTab({ d }: { d: DocumentDetail }) {
  const date = (s?: string | null) => (s ? fmtDateTime(s) : null);
  const rows: [string, React.ReactNode][] = [
    ["Title", d.title || null],
    ["Authors", d.authors.length ? d.authors.join(", ") : null],
    ["Year", d.year],
    ["Venue", d.venue],
    ["Org unit", d.org_unit],
    ["File type", d.file_type?.toUpperCase()],
    ["Pages", d.page_count || null],
    ["References", d.references_count ?? null],
    ["Owner", d.owner.username || null],
    ["Visibility", VISIBILITY[d.visibility]],
    ["Sensitivity", d.label ? <SensitivityBadge label={d.label} /> : null],
    ["Status", d.stage ? (d.stage.charAt(0).toUpperCase() + d.stage.slice(1)).replaceAll("_", " ") : null],
    ["Added", date(d.added_at)],
    ["Tags", d.tags.length ? d.tags.map((t) => `#${t}`).join(" ") : null],
  ];
  return (
    <div className="refs docinfo">
      <dl className="details">
        {rows.filter(([, v]) => v != null && v !== "").map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
      <h3>Identifiers</h3>
      {d.ids && Object.keys(d.ids).length ? <Identifiers ids={d.ids} /> : <p className="refnote">No identifiers found.</p>}
      <h3>Versions</h3>
      <dl className="details">
        {d.versions.map((v) => (
          <div key={v.id}><dt>Version {v.version}{v.is_latest ? " (latest)" : ""}</dt><dd>{v.filename} · {date(v.uploaded_at)}</dd></div>
        ))}
      </dl>
    </div>
  );
}
