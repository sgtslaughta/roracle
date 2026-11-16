import { Link, useBlocker, useParams } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { DraftDetail } from "@/api/drafts";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMe } from "@/features/library/useMe";
import { NotFound } from "@/app/RouterStates";
import { isUuid } from "@/lib/ids";
import { detailCode, draftError, useDraft, useSaveDraft } from "./api";
import { BuildPanel } from "./BuildPanel";
import { ChecksPanel } from "./ChecksPanel";
import { CoauthorsPanel } from "./CoauthorsPanel";
import { DraftEditor } from "./DraftEditor";
import { StatusBadge } from "./StatusBadge";
import { SubmitDialog } from "./SubmitDialog";
import { VersionsPanel } from "./VersionsPanel";

function DraftView({ draft, refetch }: { draft: DraftDetail; refetch: () => Promise<DraftDetail | undefined> }) {
  const me = useMe().data;
  // `base` = the server version the editor text started from; saves send its version so a newer remote save 409s
  const [base, setBase] = useState({ version: draft.latest_version, content: draft.content });
  const [text, setText] = useState(draft.content);
  const [submitting, setSubmitting] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("checks");
  const save = useSaveDraft(draft.id);
  const dirty = text !== base.content;
  useEffect(() => {
    if (draft.latest_version === base.version && draft.content === base.content) return;
    if (text === base.content) { setBase({ version: draft.latest_version, content: draft.content }); setText(draft.content); } // clean: adopt remote changes
    // eslint-disable-next-line react-hooks/exhaustive-deps -- react to fetched data only; a dirty editor keeps its base
  }, [draft.latest_version, draft.content]);
  const adopt = (content: string, version: number) => { setBase({ version, content }); setText(content); };
  const owner = me?.id === draft.owner.id;
  const author = owner || (!!me && draft.coauthors.users.includes(me.id));
  const locked = draft.status === "in_review";

  useBlocker({
    shouldBlockFn: () => dirty && !window.confirm("You have unsaved changes. Leave without saving?"),
    enableBeforeUnload: () => dirty,
  });

  const doSave = () => {
    setError(""); setConflict(false);
    const sent = text;
    save.mutate({ content: sent, base_version: base.version }, {
      onSuccess: (r) => { setBase({ version: r.version, content: sent }); toast("Saved."); },
      onError: (e) => (detailCode(e) === "draft_changed" ? setConflict(true) : setError(draftError(e))),
    });
  };
  const loadLatest = async () => { const c = await refetch(); if (c) adopt(c.content, c.latest_version); setConflict(false); };

  return (
    <section className="view">
      <Link to="/drafts" className="btn-link"><ArrowLeft aria-hidden />All drafts</Link>
      <div className="uhead-row">
        <div>
          <h1>{draft.title}</h1>
          <p className="sub"><StatusBadge status={draft.status} /> {draft.profile ?? "default profile"} · version {draft.latest_version} · owner {draft.owner.username}</p>
        </div>
        <div className="row2">
          <Button variant="primary" onClick={doSave} disabled={!dirty || save.isPending || locked}>{save.isPending ? "Saving…" : "Save"}</Button>
          {dirty && <span className="sub" role="status">Unsaved changes</span>}
          {owner && <Button onClick={() => setSubmitting(true)} disabled={dirty || locked}>Submit…</Button>}
        </div>
      </div>
      {locked && <p className="dnote" role="status">This draft is in review and can't be edited until an admin decides.</p>}
      {draft.review_note && draft.status !== "in_review" && <p className="dnote" role="status">Reviewer note: {draft.review_note}</p>}
      {conflict && (
        <p className="derr" role="alert">Someone saved a newer version. <button className="linkish" onClick={() => void loadLatest()}>Load the latest version (discards your edits)</button></p>
      )}
      {error && <p className="derr" role="alert">{error}</p>}
      <DraftEditor text={text} onChange={setText} readOnly={locked} />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList aria-label="Draft tools">
          <TabsTrigger value="checks">Checks</TabsTrigger>
          <TabsTrigger value="pdf">PDF</TabsTrigger>
          <TabsTrigger value="versions">Versions</TabsTrigger>
          <TabsTrigger value="coauthors">Co-authors</TabsTrigger>
        </TabsList>
        <TabsContent value="checks"><ChecksPanel draft={draft} author={author} /></TabsContent>
        <TabsContent value="pdf" forceMount hidden={tab !== "pdf"}><BuildPanel id={draft.id} title={draft.title} author={author} /></TabsContent>
        <TabsContent value="versions"><VersionsPanel draft={draft} locked={locked} dirty={dirty} base={base} onRestored={adopt} /></TabsContent>
        <TabsContent value="coauthors"><CoauthorsPanel draft={draft} owner={owner} /></TabsContent>
      </Tabs>
      {submitting && <SubmitDialog draft={draft} base={base} onSaved={adopt} open onOpenChange={setSubmitting} />}
    </section>
  );
}

export function DraftPage() {
  const { id } = useParams({ strict: false }) as { id: string };
  const q = useDraft(isUuid(id) ? id : "");
  if (!isUuid(id) || (q.error instanceof ApiError && q.error.status === 404)) return <NotFound />;
  if (q.isError) return <section className="view"><div className="empty" role="alert"><p>Couldn't load this draft.</p></div></section>;
  if (!q.data) return <section className="view"><p className="sub" role="status">Loading draft…</p></section>;
  return <DraftView key={q.data.id} draft={q.data} refetch={async () => (await q.refetch()).data} />;
}
