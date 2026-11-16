import { useState } from "react";
import { apiFetch, seg } from "@/api/client";
import type { DraftDetail, VersionBody } from "@/api/drafts";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { OverflowList } from "@/components/OverflowList";
import { RowMenu } from "@/components/RowMenu";
import { Button } from "@/components/ui/button";
import { SafeMarkdown } from "@/components/SafeMarkdown";
import { draftError, useDraftVersion, useSaveDraft } from "./api";
import { stripFrontMatter } from "./frontMatter";

/** Version history. The API has no restore endpoint: restore = GET that version, then PUT its text as a new version. */
export function VersionsPanel({ draft, base, locked, dirty, onRestored }: { draft: DraftDetail; base: { version: number }; locked: boolean; dirty: boolean; onRestored: (content: string, version: number) => void }) {
  const [viewing, setViewing] = useState<number | null>(null);
  const [restoring, setRestoring] = useState<number | null>(null);
  const [error, setError] = useState("");
  const shown = useDraftVersion(draft.id, viewing);
  const save = useSaveDraft(draft.id);

  const restore = async (n: number) => {
    setError("");
    try {
      const v = await apiFetch<VersionBody>(`/drafts/${seg(draft.id)}/versions/${seg(n)}`);
      const r = await save.mutateAsync({ content: v.content, base_version: base.version });
      onRestored(v.content, r.version);
    } catch (e) { setError(draftError(e)); }
    setRestoring(null);
  };

  return (
    <div className="dpanel">
      <OverflowList className="vlist" items={draft.versions} max={8} menuLabel="Older versions" moreLabel={(n) => `+${n} older`}
        renderItem={(v) => (
          <div className="vrow" data-row key={v.version}>
            <button className="linkish" aria-pressed={viewing === v.version} onClick={() => setViewing(v.version)}>Version {v.version}</button>
            <small>{v.author.username} · {new Date(v.created_at).toLocaleString()}{v.version === draft.latest_version ? " · latest" : ""}</small>
            <RowMenu label={`Actions for version ${v.version}`} actions={[
              { label: "View", onSelect: () => setViewing(v.version) },
              ...(!locked && v.version !== draft.latest_version ? [{ label: "Restore", onSelect: () => setRestoring(v.version) }] : []),
            ]} />
          </div>
        )} />
      {error && <p role="alert" className="derr">{error}</p>}
      {viewing !== null && (
        <div className="vview" aria-label={`Version ${viewing}`}>
          {shown.isPending ? <p className="sub" role="status">Loading…</p> : shown.isError ? <p role="alert" className="derr">{draftError(shown.error)}</p>
            : <SafeMarkdown text={stripFrontMatter(shown.data.content)} />}
          {!locked && viewing !== draft.latest_version && <Button onClick={() => setRestoring(viewing)}>Restore this version</Button>}
        </div>
      )}
      <ConfirmDialog open={restoring !== null} onOpenChange={(o) => !o && setRestoring(null)} title={`Restore version ${restoring}?`} confirm="Restore" busy={save.isPending}
        onConfirm={() => restoring !== null && void restore(restoring)}>
        This saves version {restoring} as a new latest version.{dirty && " Your unsaved edits will be replaced."}
      </ConfirmDialog>
    </div>
  );
}
