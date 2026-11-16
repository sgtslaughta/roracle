import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { DocumentDetail } from "@/api/types";
import type { Viewer } from "@/api/chat";
import { getReaderState, type ReaderState } from "@/features/reader/useReaderStore";

const MAX = 3;

/** Reader state -> the `viewer` body of POST /chats/{id}/messages. Ignored docs are listed only in `ignored`. */
/** P4 caps a quoted selection at 4000 characters. */
const MAX_TEXT = 4000;

export function buildViewer(
  reader: Pick<ReaderState, "tabs" | "active">,
  ignored: string[],
  versionIdOf: (docId: string, version?: number) => string | null,
  selection: Viewer["selection"] = null,
): Viewer {
  const open = reader.tabs.filter((t) => !ignored.includes(t.docId));
  const ordered = [...open.filter((t) => t.docId === reader.active), ...open.filter((t) => t.docId !== reader.active)];
  return {
    tabs: ordered.slice(0, MAX).map((t) => ({ doc_id: t.docId, version_id: versionIdOf(t.docId, t.version), page: t.page })),
    ignored,
    selection: selection && { ...selection, text: selection.text.slice(0, MAX_TEXT) },
  };
}

/** Returns `getViewer()`: resolves reader tabs and cached version ids at call time (call it when sending). */
export function useViewer() {
  const qc = useQueryClient();
  return useCallback((ignored: string[] = [], selection: Viewer["selection"] = null): Viewer => {
    const versionIdOf = (docId: string, version?: number) => {
      const vs = qc.getQueryData<DocumentDetail>(["doc", docId])?.versions;
      const v = version === undefined ? vs?.find((x) => x.is_latest) : vs?.find((x) => x.version === version);
      return v?.id ?? null;
    };
    return buildViewer(getReaderState(), ignored, versionIdOf, selection);
  }, [qc]);
}
