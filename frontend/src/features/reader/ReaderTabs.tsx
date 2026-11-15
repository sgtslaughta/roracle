import { FileText, FileType2, PanelRightClose, X } from "lucide-react";
import { shortCite } from "@/lib/cite";
import { useDoc } from "./api";
import { FocusToggle } from "./FocusToggle";
import { closeReader, closeTab, setActive, type ReaderState } from "./useReaderStore";

function DocTab({ docId, active }: { docId: string; active: boolean }) {
  const { data } = useDoc(docId);
  const Icon = data?.file_type === "Word" ? FileType2 : FileText;
  const label = data ? shortCite(data.authors, data.year) : docId;
  return (
    <div className="rtab" data-active={active}>
      <button role="tab" aria-selected={active} onClick={() => setActive(docId)}>
        <Icon aria-hidden /><span>{label}</span>
      </button>
      <button className="x" aria-label={`Close ${label}`} onClick={() => closeTab(docId)}><X aria-hidden /></button>
    </div>
  );
}

export function ReaderTabs({ state }: { state: ReaderState }) {
  return (
    <div className="rtabs" role="tablist" aria-label="Open documents">
      {state.tabs.map((t) => <DocTab key={t.docId} docId={t.docId} active={t.docId === state.active} />)}
      <span className="rtabs-end">
        <FocusToggle focus={state.focus} />
        <button className="icon-btn" aria-label="Close reader" onClick={closeReader}><PanelRightClose aria-hidden /></button>
      </span>
    </div>
  );
}
