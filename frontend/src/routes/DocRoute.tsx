import { useLocation, useParams } from "@tanstack/react-router";
import { useEffect } from "react";
import { openDoc } from "@/features/reader/useReaderStore";
import { LibraryRoute } from "./LibraryRoute";

/** Deep link /d/:docId[/v/:version]#p=<page>&c=<chunkId>: library behind, document open in the reader. */
export function DocRoute() {
  const { docId, version } = useParams({ strict: false }) as { docId: string; version?: string };
  const hash = useLocation({ select: (l) => l.hash });
  useEffect(() => {
    const h = new URLSearchParams(hash.replace(/^#/, ""));
    const page = Number(h.get("p"));
    openDoc(docId, { page: page > 0 ? page : 1, chunkId: h.get("c") ?? undefined, version: version ? Number(version) : undefined });
  }, [docId, version, hash]);
  return <LibraryRoute />;
}
