import { FileUp } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

export function DropZone({ onFiles, visibleTo }: { onFiles: (files: File[]) => void; visibleTo: string }) {
  const [over, setOver] = useState(false);
  return (
    <label
      className={cn("drop", over && "over")} htmlFor="file-input"
      onDragEnter={(e) => { e.preventDefault(); setOver(true); }}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={(e) => { e.preventDefault(); setOver(false); }}
      onDrop={(e) => { e.preventDefault(); setOver(false); onFiles([...e.dataTransfer.files]); }}
    >
      <span className="ic"><FileUp aria-hidden /></span>
      <span>
        <strong>Drop files here or choose files</strong>
        <span>PDF, Office, Markdown, LaTeX, JATS XML, EPUB, CSV, images (read with OCR) and ZIP or tar archives. Up to 500 MB each. Visible to: {visibleTo}.</span>
      </span>
      <input id="file-input" type="file" multiple className="sr-only" aria-label="Drop files here or choose files" onChange={(e) => { onFiles([...(e.target.files ?? [])]); e.target.value = ""; }} />
    </label>
  );
}
