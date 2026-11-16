import { useState } from "react";
import type { DraftDetail } from "@/api/drafts";
import type { Visibility } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { VIS_LABEL } from "@/features/uploads/status";
import { VIS_ORDER } from "@/features/uploads/UploadOptions";
import { draftError, useSaveDraft, useSubmitDraft } from "./api";
import { readAi, withAi } from "./frontMatter";

const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/** Submit: the backend reads AI disclosure from the front matter, so the form writes it there (a new version) before submitting. */
export function SubmitDialog({ draft, base, onSaved, open, onOpenChange }: { draft: DraftDetail; base: { version: number; content: string }; onSaved: (content: string, version: number) => void; open: boolean; onOpenChange: (o: boolean) => void }) {
  const ai0 = readAi(base.content);
  const [used, setUsed] = useState(ai0?.used ?? false);
  const [tools, setTools] = useState(ai0?.tools.join(", ") ?? "");
  const [description, setDescription] = useState(ai0?.description ?? "");
  const [visibility, setVisibility] = useState<Visibility>("everyone");
  const [users, setUsers] = useState("");
  const [groups, setGroups] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const save = useSaveDraft(draft.id), submit = useSubmitDraft(draft.id);
  const busy = save.isPending || submit.isPending;
  const invalid = used && !description.trim();

  const go = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    const ai = { used, tools: used ? list(tools).slice(0, 20) : [], description: used ? description.trim() : "" };
    try {
      const same = ai0 && ai0.used === ai.used && ai0.description === ai.description && ai0.tools.join(",") === ai.tools.join(",");
      if (!same) {
        const content = withAi(base.content, ai);
        const r = await save.mutateAsync({ content, base_version: base.version });
        onSaved(content, r.version);
      }
      const r = await submit.mutateAsync({ visibility, share_users: visibility === "shared" ? list(users) : [], share_groups: visibility === "shared" ? list(groups) : [] });
      setResult(r.status === "in_review" ? "Submitted for review. An admin will approve it before it is published." : "Submitted and published.");
    } catch (err) {
      setError(draftError(err));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(520px,calc(100vw-32px))]">
        <DialogTitle>Submit draft</DialogTitle>
        <DialogDescription>Latest saved version (v{base.version}) must pass validation and the citation check.</DialogDescription>
        {result ? (
          <>
            <p role="status">{result}</p>
            <div className="foot"><Button variant="primary" onClick={() => onOpenChange(false)}>Done</Button></div>
          </>
        ) : (
          <form className="dform" onSubmit={go}>
            <fieldset><legend>AI assistance</legend>
              <label className="chk"><input type="checkbox" checked={used} onChange={(e) => setUsed(e.target.checked)} />AI tools were used in writing this draft</label>
              {used && (
                <>
                  <label>Tools (comma separated)<input className="inp" value={tools} onChange={(e) => setTools(e.target.value)} /></label>
                  <label>How were they used?<textarea className="inp" rows={3} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
                </>
              )}
            </fieldset>
            <label>Visible to
              <select className="inp" value={visibility} onChange={(e) => setVisibility(e.target.value as Visibility)}>
                {VIS_ORDER.map((v) => <option key={v} value={v}>{VIS_LABEL[v]}</option>)}
              </select>
            </label>
            {visibility === "shared" && (
              <>
                <label>User ids (comma separated)<input className="inp" value={users} onChange={(e) => setUsers(e.target.value)} /></label>
                <label>Org units (comma separated)<input className="inp" value={groups} onChange={(e) => setGroups(e.target.value)} /></label>
              </>
            )}
            {error && <p role="alert" className="derr">{error}</p>}
            <div className="foot">
              <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={busy || invalid}>Submit</Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
