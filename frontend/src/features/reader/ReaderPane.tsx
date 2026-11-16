import { useLocation, useNavigate } from "@tanstack/react-router";
import { BookOpen, FolderPlus, Share2, Download, Link as LinkIcon, ListOrdered, MessageSquarePlus, MessageSquareQuote, Quote, Sparkles } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { toast } from "sonner";
import { docTitle } from "@/lib/docTitle";
import { AddTitle } from "@/features/uploads/TitlePrompt";
import { CiteLine } from "@/components/CiteLine";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AddToCollectionDialog } from "@/features/library/AddToCollection";
import { VoteControl } from "@/features/library/VoteControl";
import { useOwnerOrAdmin } from "@/features/library/useMe";
import { focusComposer, unignore } from "@/features/ask/useAskContext";
import { addRecent } from "@/features/palette/recent";
import { ContinueInAgent } from "@/features/agents/ContinueInAgent";
import { downloadOriginal, useCitedBy, useDoc, useReferences } from "./api";
import { SharingDialog } from "./SharingDialog";
const PdfView = lazy(() => import("./PdfView"));
import { NotesTab } from "@/features/notes/NotesTab";
import { notesUi, useNotes } from "@/features/notes/api";
import { CitedByTab, ReferencesTab } from "./ReferencesTab";
import { TagBar } from "./TagBar";
import { setVersion, setView, type ReaderTab, type ReaderView } from "./useReaderStore";

export function ReaderPane({ tab, view }: { tab: ReaderTab; view: ReaderView }) {
  const { data: d, isError } = useDoc(tab.docId);
  const refs = useReferences(tab.docId).data;
  const cited = useCitedBy(tab.docId).data;
  const notes = useNotes(tab.docId).data;
  const adding = notesUi.use((s) => s.adding === tab.docId);
  const [collecting, setCollecting] = useState(false);
  const [sharing, setSharing] = useState(false);
  const canManage = useOwnerOrAdmin(d?.owner.id);
  const navigate = useNavigate();
  const onAskRoute = useLocation({ select: (l) => l.pathname === "/ask" || l.pathname.startsWith("/ask/") });

  useEffect(() => { if (d) addRecent({ id: d.id, title: d.title.trim() || "Untitled document" }); }, [d?.id, d?.title]); // eslint-disable-line react-hooks/exhaustive-deps

  if (isError) return <div className="empty" role="alert"><p>This document isn't available. You may need to request access from its owner.</p></div>;
  if (!d) return <div className="rhead"><p className="sub" role="status">Loading…</p></div>;

  const latest = d.versions.find((v) => v.is_latest)?.version ?? d.versions[0]?.version ?? 1;
  const version = tab.version ?? latest;
  const link = `${window.location.origin}/d/${d.id}/v/${version}#p=${tab.page}${tab.chunkId ? `&c=${tab.chunkId}` : ""}`;

  const filename = d.versions.find((v) => v.version === version)?.filename ?? `${d.id}.pdf`;
  const title = docTitle({ title: d.title, filename: d.versions.find((v) => v.version === version)?.filename }); // not the .pdf fallback
  const download = () => { downloadOriginal(d.id, version, filename).catch(() => toast("The original couldn't be downloaded. Try again.")); };

  return (
    <>
      <div className="rhead">
        <h2 title={title}>{title}</h2>
        {canManage && !d.title.trim() && <div className="tdet title"><span className="msg">This document has no title.</span><AddTitle documentId={d.id} name={title} /></div>}
        <CiteLine authors={d.authors} venue={d.venue} year={d.year} org={d.org_unit} />
        <div className="rtools">
          <Button variant="primary" onClick={() => { unignore(d.id); if (!onAskRoute) void navigate({ to: "/ask" }); focusComposer(); }}>
            <Sparkles aria-hidden />Ask about this
          </Button>
          <VoteControl docId={d.id} votes={d.votes} row />
          <select className="sel" aria-label="Version" value={version} onChange={(e) => setVersion(d.id, Number(e.target.value))}>
            {d.versions.map((v) => <option key={v.id} value={v.version}>Version {v.version}{v.is_latest ? " (latest)" : ""}</option>)}
          </select>
          <Button onClick={() => { void navigator.clipboard?.writeText(link).then(() => toast("Link copied. People without access will be asked to request it."), () => toast(link)); }}>
            <LinkIcon aria-hidden />Copy link
          </Button>
          <ContinueInAgent source={() => ({
            kind: "document", sourceId: d.id, docIds: [d.id], title: title.slice(0, 200), goal: `Work with “${title}”`,
            brief: `Goal: work with “${title}”. I'm reading page ${tab.page} of version ${version}.`,
            summary: "Hands this document to an agent, with its notes, references and cited-by links.",
          })} />
          <Button onClick={() => setCollecting(true)}><FolderPlus aria-hidden />Add to collection</Button>
          {canManage && <Button onClick={() => setSharing(true)}><Share2 aria-hidden />Sharing</Button>}
          <Button
            aria-pressed={adding} disabled={!adding && version !== latest}
            title={version !== latest ? "Switch to the latest version to anchor a note" : "Then click a passage on the page"} onClick={() => {
            notesUi.set((s) => ({ ...s, adding: adding ? null : d.id }));
            if (!adding) setView("doc");
          }}><MessageSquarePlus aria-hidden />Add note</Button>
          <button className="icon-btn" aria-label="Download original" onClick={download}><Download aria-hidden /></button>
        </div>
        <Tabs value={view} onValueChange={(v) => setView(v as ReaderView)}>
          <TabsList aria-label="Document sections">
            <TabsTrigger value="doc"><BookOpen aria-hidden />Document</TabsTrigger>
            <TabsTrigger value="refs"><ListOrdered aria-hidden />References{refs && <span className="n">{refs.length}</span>}</TabsTrigger>
            <TabsTrigger value="cited"><Quote aria-hidden />Cited by{cited && <span className="n">{cited.length}</span>}</TabsTrigger>
            <TabsTrigger value="notes"><MessageSquareQuote aria-hidden />Notes{notes && <span className="n">{notes.filter((n) => !n.parent_id).length}</span>}</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      {sharing && <SharingDialog doc={d} open onOpenChange={setSharing} />}
      {collecting && <AddToCollectionDialog docId={d.id} open onOpenChange={setCollecting} />}
      {view === "doc" && (
        <Suspense fallback={<div className="pages"><p className="sub" role="status">Loading document…</p></div>}>
          <PdfView key={`${d.id}:${version}`} tab={tab} version={version} filename={filename} onDownload={download} />
        </Suspense>
      )}
      {view === "refs" && <ReferencesTab docId={d.id} />}
      {view === "cited" && <CitedByTab docId={d.id} />}
      {view === "notes" && <NotesTab docId={d.id} docOwnerId={d.owner.id} version={version} latest={latest} />}
      <TagBar docId={d.id} tags={d.tags} ownerId={d.owner.id} />
    </>
  );
}
