import { Bell, Inbox, Library, MessagesSquare, PenLine, Settings, ShieldCheck, UploadCloud, type LucideIcon } from "lucide-react";

export const NAV: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/library", label: "Library", icon: Library },
  { to: "/ask", label: "Ask", icon: MessagesSquare },
  { to: "/uploads", label: "My uploads", icon: UploadCloud },
  { to: "/proposals", label: "Agent proposals", icon: Inbox },
  { to: "/drafts", label: "Drafts", icon: PenLine },
];

/** Palette-only pages: reachable from the user menu and bell elsewhere, not the sidebar. */
export const EXTRA_PAGES: { to: string; label: string; icon: LucideIcon; admin?: boolean }[] = [
  { to: "/settings", label: "Settings", icon: Settings },
  { to: "/notifications", label: "Notifications", icon: Bell },
  { to: "/admin", label: "Admin", icon: ShieldCheck, admin: true },
];
