import { Inbox, Library, MessagesSquare, PenLine, UploadCloud, type LucideIcon } from "lucide-react";

export const NAV: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/library", label: "Library", icon: Library },
  { to: "/ask", label: "Ask", icon: MessagesSquare },
  { to: "/uploads", label: "My uploads", icon: UploadCloud },
  { to: "/proposals", label: "Agent proposals", icon: Inbox },
  { to: "/drafts", label: "Drafts", icon: PenLine },
];
