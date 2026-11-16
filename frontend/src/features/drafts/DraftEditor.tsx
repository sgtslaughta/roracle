import { useState } from "react";
import { SafeMarkdown } from "@/components/SafeMarkdown";
import { stripFrontMatter } from "./frontMatter";

/** Plain textarea + sanitized preview. Preview goes through SafeMarkdown only (no raw HTML, no images, safe links). */
export function DraftEditor({ text, onChange, readOnly }: { text: string; onChange: (t: string) => void; readOnly: boolean }) {
  const [preview, setPreview] = useState(false);
  return (
    <div className="deditor">
      <div className="seg" role="group" aria-label="Editor mode">
        <button aria-pressed={!preview} onClick={() => setPreview(false)}>Edit</button>
        <button aria-pressed={preview} onClick={() => setPreview(true)}>Preview</button>
      </div>
      {preview
        ? <div className="dpreview" role="region" aria-label="Preview"><SafeMarkdown text={stripFrontMatter(text)} /></div>
        : <textarea className="inp dtext" aria-label="Draft content" spellCheck value={text} readOnly={readOnly} onChange={(e) => onChange(e.target.value)} />}
    </div>
  );
}
