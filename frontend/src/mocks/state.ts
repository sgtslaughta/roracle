import { DOCS } from "./docs";
import type { ChatDetail } from "@/api/chat";
import { seedUploads, type MockUpload } from "./uploads";
import type { AccessToken } from "@/api/agents";
import type { MockHandoff } from "./agents";
import type { Evidence, Note } from "@/api/notes";
import type { Proposal } from "@/features/proposals/api";
import type { AppNotification } from "@/features/notifications/describe";

export interface MockState {
  votes: Record<string, 1 | -1 | 0>;
  extraTags: Record<string, string[]>;
  removedTags: Record<string, string[]>;
  versions: Record<string, number>;
  uploads: MockUpload[];
  lastAdvance: number;
  admin: boolean;
  /** seeded lazily by mocks/chat.ts (the seed needs pdf/search, which import this module) */
  chats: ChatDetail[] | null;
  chatSeq: number;
  /** chat id -> shares (mocks/chat.ts) */
  shares: Record<string, { users: string[]; groups: string[] }>;
  /** mocks/agents.ts; tokens seeded lazily there (import cycle) */
  tokens: AccessToken[] | null;
  handoffs: Record<string, MockHandoff>;
  /** mocks/notes.ts; seeded lazily there (import cycle) */
  notes: Note[] | null;
  /** mocks/notes.ts; `content` is the file body served by /evidence/{id}/file, `ready` when a pending scan finishes */
  evidence: (Evidence & { content?: string; ready?: number })[] | null;
  /** mocks/proposals.ts and mocks/notifications.ts; seeded lazily there */
  proposals: Proposal[] | null;
  notifications: AppNotification[] | null;
}

const storedAdmin = () => { try { return localStorage.getItem("roracle-mock-admin") === "1"; } catch { return false; } };

const fresh = (): MockState => ({
  votes: {},
  extraTags: {},
  removedTags: {},
  versions: Object.fromEntries(DOCS.map((d) => [d.id, d.ver])),
  uploads: seedUploads(),
  lastAdvance: Date.now(),
  admin: false,
  chats: null,
  chatSeq: 1,
  shares: {},
  tokens: null,
  handoffs: {},
  notes: null,
  evidence: null,
  proposals: null,
  notifications: null,
});

export const state: MockState = { ...fresh(), admin: storedAdmin() };
export const resetMockState = () => Object.assign(state, fresh());
