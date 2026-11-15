import { DOCS } from "./docs";
import type { ChatDetail } from "@/api/chat";
import { seedUploads, type MockUpload } from "./uploads";

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
});

export const state: MockState = { ...fresh(), admin: storedAdmin() };
export const resetMockState = () => Object.assign(state, fresh());
