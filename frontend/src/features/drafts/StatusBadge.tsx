import type { DraftStatus } from "@/api/drafts";

const LABEL: Record<DraftStatus, string> = { draft: "Draft", in_review: "In review", submitted: "Submitted" };
const CLS: Record<DraftStatus, string> = { draft: "", in_review: "review", submitted: "done" };

export const StatusBadge = ({ status }: { status: DraftStatus }) => <span className={`pill ${CLS[status]}`}>{LABEL[status]}</span>;
