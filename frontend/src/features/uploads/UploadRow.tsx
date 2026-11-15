import { Check, CircleAlert, FileText, FileType2, Globe, GitCompare, Loader, Lock, Users } from "lucide-react";
import type { UploadItem } from "@/api/types";
import { cn } from "@/lib/utils";
import { NearDupPrompt } from "./NearDupPrompt";
import { fmtSize, pipelineDone, PILL, STAGE_LABELS, viewStatus, VIS_LABEL } from "./status";

const PILL_ICON = { done: Check, running: Loader, failed: CircleAlert, review: GitCompare } as const;
const VIS_ICON = { everyone: Globe, private: Lock, org_unit: Users, shared: Users } as const;
const CELL = { running: "run", failed: "err", review: "wait", done: "" } as const;

export function UploadRow({ u }: { u: UploadItem }) {
  const st = viewStatus(u);
  const done = pipelineDone(u.stage);
  const Pill = PILL_ICON[st];
  const Vis = VIS_ICON[u.visibility];
  const Type = /\.docx?$/i.test(u.filename) ? FileType2 : FileText;
  return (
    <div className="trow">
      <div className="fname">
        <span className="ft"><Type aria-hidden /></span>
        <div style={{ minWidth: 0 }}>
          <b title={u.filename}>{u.filename}</b>
          <small>{fmtSize(u.size)}. {new Date(u.uploaded_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</small>
        </div>
      </div>
      <div className="pipe" aria-label="Progress">
        {STAGE_LABELS.map((l, i) => <div key={l} className={cn(i < done ? "ok" : i === done ? CELL[st] : "")}>{l}</div>)}
      </div>
      <span><span className={cn("pill", st)}><Pill aria-hidden />{PILL[st]}</span></span>
      <span className="vis"><Vis aria-hidden />{VIS_LABEL[u.visibility]}</span>
      {st === "failed" && (
        <div className="tdet failed"><span className="msg"><CircleAlert aria-hidden />{u.error ?? "Processing failed."}</span></div>
      )}
      {st === "review" && <NearDupPrompt u={u} />}
    </div>
  );
}
