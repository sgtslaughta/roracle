import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { apiFetch, seg } from "@/api/client";
import { SafeMarkdown } from "@/components/SafeMarkdown";
import { Button } from "@/components/ui/button";

const MD = /\.(md|markdown)$/i;
const MAX_TEXT = 2 * 1024 * 1024;
const TXT = /\.(txt|text|csv|tsv|json|xml|nxml|tex|bib)$/i; // shown as plain text, never as HTML
const IMG = /\.(png|jpe?g|webp|gif|bmp)$/i; // tiff is not shown by browsers
/** Types that never have a PDF to show (only PDFs and converted office files do): skip the /pdf request and its 404. */
export const NO_PDF = /\.(md|markdown|txt|text|csv|tsv|json|xml|nxml|tex|bib|png|jpe?g|webp|gif|bmp|tiff?|epub|html?)$/i;

/** The original image, fetched with the session (an <img src> cannot send the token). */
function ImageView({ docId, version, filename, onDownload, blocked }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const blob = useQuery({
    queryKey: ["doc-image", docId, version],
    staleTime: Infinity,
    queryFn: () => apiFetch<Blob>(`/documents/${seg(docId)}/versions/${seg(version)}/file?inline=true`),
  });
  useEffect(() => {
    if (!blob.data) return;
    const u = URL.createObjectURL(blob.data);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [blob.data]);
  if (url) return <div className="pages"><img src={url} alt={filename} style={{ maxWidth: "100%", height: "auto" }} /></div>;
  if (!blob.isError) return <div className="pages"><p className="sub" role="status">Loading document…</p></div>;
  return <Unavailable onDownload={onDownload} blocked={blocked} />;
}

function Unavailable({ onDownload, blocked }: Pick<Props, "onDownload" | "blocked">) {
  return (
    <div className="pages">
      <div className="empty" role="alert">
        <p>Preview not available for this file. Download the original to open it.</p>
        <Button onClick={onDownload} disabled={!!blocked} title={blocked}><Download aria-hidden />Download</Button>
      </div>
    </div>
  );
}

type Props = { docId: string; version: number; filename: string; onDownload: () => void; blocked?: string };

/** No PDF rendition: Markdown and text-like files render from the original file, images show as pictures, anything else offers a download. */
export function TextFallback(props: Props) {
  if (IMG.test(props.filename)) return <ImageView {...props} />;
  return <TextView {...props} />;
}

function TextView({ docId, version, filename, onDownload, blocked }: Props) {
  const md = MD.test(filename);
  const textual = md || TXT.test(filename);
  const text = useQuery({
    queryKey: ["doc-text", docId, version],
    enabled: textual,
    staleTime: Infinity,
    queryFn: async () => {
      const blob = await apiFetch<Blob>(`/documents/${seg(docId)}/versions/${seg(version)}/file?inline=true`);
      return blob.size > MAX_TEXT ? null : blob.text(); // too large to render: offer the download
    },
  });
  if (textual && text.data != null) {
    return <div className="pages"><div className="doctext">{md ? <SafeMarkdown text={text.data} /> : <pre>{text.data}</pre>}</div></div>;
  }
  if (textual && text.data === undefined && !text.isError) return <div className="pages"><p className="sub" role="status">Loading document…</p></div>;
  return <Unavailable onDownload={onDownload} blocked={blocked} />;
}
