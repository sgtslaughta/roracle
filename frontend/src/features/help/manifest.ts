import { BookOpen, Bot, Keyboard, Rocket, Shield, type LucideIcon } from "lucide-react";

export type Group = "Start here" | "Using rOracle" | "Agents" | "Reference" | "Admin";

export const GROUPS: { name: Group; icon: LucideIcon }[] = [
  { name: "Start here", icon: Rocket },
  { name: "Using rOracle", icon: BookOpen },
  { name: "Agents", icon: Bot },
  { name: "Reference", icon: Keyboard },
  { name: "Admin", icon: Shield },
];

export interface Topic {
  id: string;
  group: Group;
  /** Path from the docs root, e.g. `user/chat.md`. */
  file: string;
  /** Route path prefixes that open this topic by default. */
  routes?: string[];
  section?: string;
  admin?: boolean;
  /** Hidden when personal access tokens are disabled site-wide. */
  pats?: boolean;
}

const u = (id: string, group: Group, extra: Partial<Topic> = {}): Topic => ({ id, group, file: `user/${id}.md`, ...extra });
const a = (name: string, extra: Partial<Topic> = {}): Topic => ({ id: `admin-${name}`, group: "Admin", file: `admin/${name}.md`, admin: true, ...extra });

export const TOPICS: Topic[] = [
  u("getting-started", "Start here"),
  u("first-hour", "Start here"),
  u("sharing-help", "Start here"),
  u("library-search", "Using rOracle", { routes: ["/library"] }),
  u("reader", "Using rOracle", { routes: ["/d/"] }),
  u("chat", "Using rOracle", { routes: ["/ask"] }),
  u("notes-annotations", "Using rOracle"),
  u("labels", "Using rOracle"),
  u("uploads", "Using rOracle", { routes: ["/uploads"] }),
  u("collections-sharing", "Using rOracle"),
  u("writing-drafts", "Using rOracle", { routes: ["/drafts"] }),
  u("agents-and-tokens", "Agents", { routes: ["/settings"] }),
  u("agents-mcp-setup", "Agents", { pats: true }),
  u("handoffs-inbox", "Agents", { routes: ["/proposals", "/notifications", "/h/"] }),
  u("agents-troubleshooting", "Agents", { pats: true }),
  u("keyboard-shortcuts", "Reference"),
  { id: "glossary", group: "Reference", file: "glossary.md" },
  a("README", { routes: ["/admin"] }),
  a("labels", { routes: ["/admin/labels"] }),
  a("runtime-settings", { routes: ["/admin/settings"] }),
  a("users-groups", { routes: ["/admin/users"] }),
  a("connectors", { routes: ["/admin/connectors"] }),
  a("audit", { routes: ["/admin/audit"] }),
  a("eval", { routes: ["/admin/eval"] }),
  a("index-maintenance", { routes: ["/admin/jobs", "/admin/health"] }),
  a("quotas-limits"),
  a("writing-profiles"),
];

const DEFAULT_TOPIC = "getting-started";

export const visibleTopics = (isAdmin: boolean, patsEnabled: boolean): Topic[] =>
  TOPICS.filter((t) => (isAdmin || !t.admin) && (patsEnabled || !t.pats));

export function topicForPath(pathname: string): string {
  let best = DEFAULT_TOPIC, len = 0;
  for (const t of TOPICS) for (const r of t.routes ?? []) if (pathname.startsWith(r) && r.length > len) { best = t.id; len = r.length; }
  return best;
}

export const findTopic = (id: string, isAdmin: boolean, patsEnabled: boolean): Topic | undefined =>
  visibleTopics(isAdmin, patsEnabled).find((t) => t.id === id);
