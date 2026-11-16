import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { apiFetch, seg } from "@/api/client";
import { SafeMarkdown } from "@/components/SafeMarkdown";
import { Button } from "@/components/ui/button";

const MD = /\.(md|markdown)$/i;
const TXT = /\.(txt|text)$/i;

/** No PDF rendition (404): Markdown and plain text render from the original file, anything else offers a download. */
export function TextFallback({ docId, version, filename, onDownload }: { docId: string; version: number; filename: string; onDownload: () => void }) {
  const md = MD.test(filename);
  const textual = md || TXT.test(filename);
  const text = useQuery({
    queryKey: ["doc-text", docId, version],
    enabled: textual,
    staleTime: Infinity,
    queryFn: async () => (await apiFetch<Blob>(`/documents/${seg(docId)}/versions/${seg(version)}/file`)).text(),
  });
  if (textual && text.data != null) {
    return <div className="pages"><div className="doctext">{md ? <SafeMarkdown text={text.data} /> : <pre>{text.data}</pre>}</div></div>;
  }
  if (textual && !text.isError) return <div className="pages"><p className="sub" role="status">Loading document…</p></div>;
  return (
    <div className="pages">
      <div className="empty" role="alert">
        <p>Preview not available for this file. Download the original to open it.</p>
        <Button onClick={onDownload}><Download aria-hidden />Download</Button>
      </div>
    </div>
  );
}
