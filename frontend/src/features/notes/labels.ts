import { CircleHelp, FlaskConical, GitBranch, MessageSquare, TriangleAlert, type LucideIcon } from "lucide-react";
import type { NoteKind, NoteVisibility } from "@/api/notes";

export const KIND_LABEL: Record<NoteKind, string> = { comment: "Comment", finding: "Finding", correction: "Correction", question: "Question", relation: "Relation" };
export const KIND_ICON: Record<NoteKind, LucideIcon> = { comment: MessageSquare, finding: FlaskConical, correction: TriangleAlert, question: CircleHelp, relation: GitBranch };
export const VIS_LABEL: Record<NoteVisibility, string> = { everyone: "Everyone", shared: "Named people or groups", private: "Only me" };
