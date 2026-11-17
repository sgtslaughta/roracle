import { Copy } from "lucide-react";
import { toast } from "sonner";

const LABELS: [string, string][] = [
  ["doi", "DOI"], ["arxiv", "arXiv"], ["pmid", "PMID"], ["pmcid", "PMCID"], ["isbn", "ISBN"],
  ["acl", "ACL Anthology"], ["openreview", "OpenReview"], ["issn", "ISSN"], ["eissn", "eISSN"],
];

/** Plain-text identifiers with a copy button each. Never links out (air-gapped). */
export function Identifiers({ ids }: { ids?: Record<string, string> }) {
  const rows = LABELS.filter(([k]) => ids?.[k]);
  if (!rows.length) return null;
  return (
    <dl className="rids" aria-label="Identifiers">
      {rows.map(([k, label]) => (
        <div key={k} className="rid">
          <dt>{label}</dt>
          <dd>{ids![k]}</dd>
          <button type="button" className="rid-copy" aria-label={`Copy ${label}`}
            onClick={() => { void navigator.clipboard?.writeText(ids![k]).then(() => toast(`${label} copied`), () => toast("Copying isn't available here.")); }}>
            <Copy aria-hidden size={12} />
          </button>
        </div>
      ))}
    </dl>
  );
}
