import { isDocId } from "@/lib/ids";
import { Bot, FileText, Link2Off, Terminal } from "lucide-react";
import { useState } from "react";
import { ApiError } from "@/api/client";
import { agentPrompt, handoffLink } from "@/api/agents";
import { Button } from "@/components/ui/button";
import { useDoc } from "@/features/reader/api";
import { openDoc } from "@/features/reader/useReaderStore";
import { useHandoff } from "./api";
import { copyText } from "./HandoffDialog";
import { AUDIENCE_LABEL } from "./HandoffForm";
import { fmtDate, fmtDateTime } from "@/lib/dates";
import { SensitivityBadge } from "@/components/SensitivityBadge";
import type { HandoffManifest } from "@/api/agents";
import { RowSkeleton } from "@/components/RowSkeleton";

const PAGE = 10;

function DocRow({ id }: { id: string }) {
  if (!isDocId(id)) return <li><span className="hdoc"><FileText aria-hidden /><span className="trunc">Document not available</span></span></li>;
  return <DocLink id={id} />;
}

function LabelledDoc({ d }: { d: NonNullable<HandoffManifest["documents"]>[number] }) {
  return (
    <li>
      <button className="hdoc" onClick={() => openDoc(d.id)} title={d.title}>
        <FileText aria-hidden /><span className="trunc">{d.title || "Untitled document"}</span>
        <SensitivityBadge label={d.label} className="hdoc-lbl" />
      </button>
      {d.text_withheld && <small className="sub"> Text withheld by its label</small>}
    </li>
  );
}

function DocLink({ id }: { id: string }) {
  const { data, isError } = useDoc(id);
  const title = data?.title ?? (isError ? "Document not available" : "Loading…");
  return (
    <li>
      <button className="hdoc" disabled={!data} onClick={() => openDoc(id)} title={title}>
        <FileText aria-hidden /><span className="trunc">{title}</span>
      </button>
    </li>
  );
}

function failure(e: unknown): string {
  if (e instanceof ApiError && e.status === 410)
    return String(e.detail).includes("revoked") ? "This handoff was revoked by the person who made it." : "This handoff has expired. Ask whoever shared it for a new one.";
  if (e instanceof ApiError && e.status === 404) return "This handoff was not found or not shared with you. Check the code, or ask whoever shared it.";
  return "This handoff couldn't be loaded. Try again.";
}

/** /h/$code: what an agent would load, for people. All handoff text is plain text. */
export function HandoffLanding({ code }: { code: string }) {
  const { data: m, error } = useHandoff(code);
  const [shown, setShown] = useState(PAGE);
  if (error) return <div className="empty" role="alert"><Link2Off aria-hidden /><p>{failure(error)}</p></div>;
  if (!m) return <RowSkeleton label="Loading handoff…" />;
  const n = m.documents?.length ?? m.doc_ids.length;
  return (
    <article className="hland">
      <div className="uhead-row">
        <div>
          <p className="sub"><Bot aria-hidden /> Handoff <code>{m.display}</code></p>
          <h1>{m.title || `Handoff ${m.display}`}</h1>
          <p className="sub">From {m.created_by}{m.created_via === "mcp" ? " (via an agent)" : ""}. {AUDIENCE_LABEL[m.audience]}. Expires {fmtDate(m.expires_at)}.</p>
        </div>
        <div className="row2">
          <Button onClick={() => void copyText(handoffLink(m.url), "Handoff link copied")}>Copy link</Button>
          <Button variant="primary" onClick={() => void copyText(agentPrompt(m.display, m.goal), "Agent prompt copied. Paste it into your agent.")}><Terminal aria-hidden />Copy agent prompt</Button>
        </div>
      </div>
      {m.goal && <section><h2>Goal</h2><p className="pre">{m.goal}</p></section>}
      {m.brief && <section><h2>Brief</h2><p className="pre">{m.brief}</p></section>}
      <section>
        <h2>Documents{n > 0 && <span className="n">{n}</span>}</h2>
        {n ? <ul className="hdocs">{m.documents ? m.documents.slice(0, shown).map((d) => <LabelledDoc key={d.id} d={d} />) : m.doc_ids.slice(0, shown).map((id) => <DocRow key={id} id={id} />)}</ul> : <p className="sub">No documents you can see.</p>}
        {n > shown && <Button variant="ghost" onClick={() => setShown(shown + PAGE)}>Show {Math.min(PAGE, n - shown)} more</Button>}
        {m.passages.length > 0 && <p className="sub">{m.passages.length} cited passage{m.passages.length > 1 ? "s" : ""} included for the agent.</p>}
        {m.withheld > 0 && <p className="sub">{m.withheld} source{m.withheld > 1 ? "s" : ""} withheld: you don't have access to {m.withheld > 1 ? "them" : "it"}.</p>}
      </section>
      {m.notes.length > 0 && (
        <section>
          <h2>Notes{m.parts.notes > 1 && <span className="n">latest 20</span>}</h2>
          <ol className="hnotes">
            {m.notes.map((x, i) => (
              <li key={i}><small>{x.by}{x.via === "mcp" ? " (agent)" : ""}, {fmtDateTime(x.at)}</small><p className="pre">{x.text}</p></li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}
