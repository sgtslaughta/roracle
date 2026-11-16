import { docTitle } from "@/lib/docTitle";
import { fmtDateTime } from "@/lib/dates";
import { copyAction, RowMenu } from "@/components/RowMenu";
import { openDoc } from "@/features/reader/useReaderStore";
import { humanBytes } from "@/lib/bytes";
import { Check, CircleAlert, FileText, FileType2, Globe, GitCompare, Loader, Lock, Pencil, Users } from "lucide-react";
import type { UploadItem } from "@/api/types";
import { cn } from "@/lib/utils";
import { NearDupPrompt } from "./NearDupPrompt";
import { AddTitle, TitlePrompt } from "./TitlePrompt";
import { pipelineDone, PILL, STAGE_LABELS, viewStatus, VIS_LABEL } from "./status";

const PILL_ICON = { done: Check, running: Loader, failed: CircleAlert, review: GitCompare, title: Pencil } as const;
const VIS_ICON = { everyone: Globe, private: Lock, org_unit: Users, shared: Users } as const;
const CELL = { running: "run", failed: "err", review: "wait", title: "wait", done: "" } as const;

export function UploadRow({ u }: { u: UploadItem }) {
  const st = viewStatus(u);
  const name = docTitle(u);
  const done = pipelineDone(u.stage);
  const Pill = PILL_ICON[st];
  const Vis = VIS_ICON[u.visibility];
  const Type = /\.docx?$/i.test(u.filename) ? FileType2 : FileText;
  return (
    <div className="trow">
      <div className="fname">
        <span className="ft"><Type aria-hidden /></span>
        <div style={{ minWidth: 0 }}>
          <b title={name}>{name}</b>
          {u.title?.trim() && <small className="fn" title={u.filename}>{u.filename}</small>}
          <small>{humanBytes(u.size)}. {fmtDateTime(u.uploaded_at)}</small>
        </div>
      </div>
      <div className="pipe" aria-label="Progress">
        {STAGE_LABELS.map((l, i) => <div key={l} className={cn(i < done ? "ok" : i === done ? CELL[st] : "")}>{l}</div>)}
      </div>
      <span><span className={cn("pill", st)}><Pill aria-hidden />{PILL[st]}</span></span>
      <span className="vis">
        <Vis aria-hidden />{VIS_LABEL[u.visibility]}
        <RowMenu label={`Actions for ${name}`} actions={[
          ...(st === "done" ? [{ label: "Open document", onSelect: () => openDoc(u.document_id) }] : []),
          copyAction("Copy filename", u.filename),
        ]} />
      </span>
      {st === "failed" && (
        <div className="tdet failed"><span className="msg"><CircleAlert aria-hidden />{u.error ?? "Processing failed."}</span></div>
      )}
      {st === "review" && <NearDupPrompt u={u} />}
      {st === "title" && <TitlePrompt u={u} />}
      {st === "done" && !u.title?.trim() && <div className="tdet title"><span className="msg"><Pencil aria-hidden />This file has no title.</span><AddTitle documentId={u.document_id} name={u.filename} /></div>}
    </div>
  );
}
