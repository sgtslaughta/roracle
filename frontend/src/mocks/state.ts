import { DOCS } from "./docs";
import type { ChatDetail } from "@/api/chat";
import { seedUploads, type MockUpload } from "./uploads";
import type { AccessToken } from "@/api/agents";
import type { MockHandoff } from "./agents";
import type { Note } from "@/api/notes";

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
});

export const state: MockState = { ...fresh(), admin: storedAdmin() };
export const resetMockState = () => Object.assign(state, fresh());
