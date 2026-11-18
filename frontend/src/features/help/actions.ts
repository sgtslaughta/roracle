import type { QueryClient } from "@tanstack/react-query";
import type { useNavigate } from "@tanstack/react-router";
import type { DocumentDetail } from "@/api/types";
import type { HandoffSource } from "@/features/agents/HandoffDialog";
import { focusComposer, unignore } from "@/features/ask/useAskContext";
import { setDismissed } from "@/features/onboarding/api";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import { docTitle } from "@/lib/docTitle";
import { closeHelp } from "./helpStore";
import type { TrySeg } from "./parse";

/** A dialog to show once help has closed. */
export type Pending = { kind: "handoff"; source: HandoffSource } | { kind: "token" };

export interface TryCtx {
  navigate: ReturnType<typeof useNavigate>;
  qc: QueryClient;
  /** Document open in the reader, if any. */
  activeDoc: string | null;
  setPending(p: Pending): void;
}

/** The handoff action needs an open paper. */
export const needsDoc = (seg: TrySeg) => seg.action === "handoff";

function docSource(id: string, qc: QueryClient): HandoffSource {
  const d = qc.getQueryData<DocumentDetail>(["doc", id]);
  const title = d ? docTitle({ title: d.title }) : "this paper";
  return {
    kind: "document", sourceId: id, docIds: [id], title: title.slice(0, 200), goal: `Work with “${title}”`,
    brief: `Goal: work with “${title}”.`,
    summary: "Hands this document to an agent, with its notes, references and cited-by links.",
  };
}

export function runTry(seg: TrySeg, ctx: TryCtx) {
  const { navigate, qc, activeDoc } = ctx;
  switch (seg.action) {
    case "search": closeHelp(); void navigate({ to: "/library", search: { q: seg.q } }); break;
    case "ask":
      closeHelp();
      if (activeDoc) unignore(activeDoc);
      void navigate({ to: "/ask" });
      focusComposer();
      break;
    case "uploads": closeHelp(); void navigate({ to: "/uploads" }); break;
    case "handoff":
      if (!activeDoc) return;
      closeHelp();
      ctx.setPending({ kind: "handoff", source: docSource(activeDoc, qc) });
      break;
    case "token": closeHelp(); ctx.setPending({ kind: "token" }); break;
    case "card": closeHelp(); void setDismissed(false, qc).then(() => navigate({ to: "/library" })).catch((e) => toast(errorText(e))); break;
    case "docs": window.open("/docs/", "_blank", "noopener"); break;
  }
}
