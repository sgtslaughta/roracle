import { useLocation, useParams } from "@tanstack/react-router";
import { FileX } from "lucide-react";
import { useEffect } from "react";
import { openDoc } from "@/features/reader/useReaderStore";
import { isChunkId, isDocId, isVersion } from "@/lib/ids";
import { LibraryRoute } from "./LibraryRoute";

/** Deep link /d/:docId[/v/:version]#p=<page>&c=<chunkId>: library behind, document open in the reader. */
export function DocRoute() {
  const { docId, version } = useParams({ strict: false }) as { docId: string; version?: string };
  const hash = useLocation({ select: (l) => l.hash });
  const valid = isDocId(docId) && (version === undefined || isVersion(version));
  useEffect(() => {
    if (!valid) return;
    const h = new URLSearchParams(hash.replace(/^#/, ""));
    const page = Number(h.get("p"));
    const c = h.get("c");
    openDoc(docId, { page: Number.isInteger(page) && page > 0 ? page : 1, chunkId: isChunkId(c) ? c : undefined, version: version ? Number(version) : undefined });
  }, [valid, docId, version, hash]);
  if (!valid)
    return (
      <section className="view">
        <div className="empty" role="alert">
          <FileX aria-hidden />
          <h1 style={{ fontSize: 18 }}>Document not found</h1>
          <p>This link doesn't point to a document. Check the address, or search the library.</p>
        </div>
      </section>
    );
  return <LibraryRoute />;
}
