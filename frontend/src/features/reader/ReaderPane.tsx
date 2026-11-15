import { BookOpen, Download, Link as LinkIcon, ListOrdered, MessageSquareQuote, Quote } from "lucide-react";
import { useEffect } from "react";
import { toast } from "sonner";
import { apiFetch, seg } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VoteControl } from "@/features/library/VoteControl";
import { addRecent } from "@/features/palette/recent";
import { useCitedBy, useDoc, useReferences } from "./api";
import { PdfView } from "./PdfView";
import { CitedByTab, NotesTab, ReferencesTab } from "./ReferencesTab";
import { TagBar } from "./TagBar";
import { setVersion, setView, type ReaderTab, type ReaderView } from "./useReaderStore";

export function ReaderPane({ tab, view }: { tab: ReaderTab; view: ReaderView }) {
  const { data: d, isError } = useDoc(tab.docId);
  const refs = useReferences(tab.docId).data;
  const cited = useCitedBy(tab.docId).data;

  useEffect(() => { if (d) addRecent({ id: d.id, title: d.title }); }, [d?.id, d?.title]); // eslint-disable-line react-hooks/exhaustive-deps

  if (isError) return <div className="empty" role="alert"><p>This document isn't available. You may need to request access from its owner.</p></div>;
  if (!d) return <div className="rhead"><p className="sub" role="status">Loading…</p></div>;

  const latest = d.versions.find((v) => v.is_latest)?.version ?? d.versions[0]?.version ?? 1;
  const version = tab.version ?? latest;
  const link = `${window.location.origin}/d/${d.id}/v/${version}#p=${tab.page}${tab.chunkId ? `&c=${tab.chunkId}` : ""}`;

  const download = async () => {
    try {
      const blob = await apiFetch<Blob>(`/documents/${seg(d.id)}/versions/${seg(version)}/file`);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = d.versions.find((v) => v.version === version)?.filename ?? `${d.id}.pdf`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } catch { toast("The original couldn't be downloaded. Try again."); }
  };

  return (
    <>
      <div className="rhead">
        <h2 title={d.title}>{d.title}</h2>
        <p className="citeline trunc" title={`${d.authors.join(", ")}. ${d.year}`}>{d.authors.join(", ")}. {d.venue && <i>{d.venue}</i>}{d.venue && ", "}{d.year}. {d.org_unit}.</p>
        <div className="rtools">
          <VoteControl docId={d.id} votes={d.votes} row />
          <select className="sel" aria-label="Version" value={version} onChange={(e) => setVersion(d.id, Number(e.target.value))}>
            {d.versions.map((v) => <option key={v.id} value={v.version}>Version {v.version}{v.is_latest ? " (latest)" : ""}</option>)}
          </select>
          <Button onClick={() => { void navigator.clipboard?.writeText(link).then(() => toast("Link copied. People without access will be asked to request it."), () => toast(link)); }}>
            <LinkIcon aria-hidden />Copy link
          </Button>
          <button className="icon-btn" aria-label="Download original" onClick={download}><Download aria-hidden /></button>
        </div>
        <Tabs value={view} onValueChange={(v) => setView(v as ReaderView)}>
          <TabsList aria-label="Document sections">
            <TabsTrigger value="doc"><BookOpen aria-hidden />Document</TabsTrigger>
            <TabsTrigger value="refs"><ListOrdered aria-hidden />References{refs && <span className="n">{refs.length}</span>}</TabsTrigger>
            <TabsTrigger value="cited"><Quote aria-hidden />Cited by{cited && <span className="n">{cited.length}</span>}</TabsTrigger>
            <TabsTrigger value="notes"><MessageSquareQuote aria-hidden />Notes</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      {view === "doc" && <PdfView key={`${d.id}:${version}`} tab={tab} version={version} />}
      {view === "refs" && <ReferencesTab docId={d.id} />}
      {view === "cited" && <CitedByTab docId={d.id} />}
      {view === "notes" && <NotesTab />}
      <TagBar docId={d.id} tags={d.tags} ownerId={d.owner.id} />
    </>
  );
}
