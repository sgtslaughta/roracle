import { DOCS } from "./docs";
import type { ChatDetail } from "@/api/chat";
import { seedUploads, type MockUpload } from "./uploads";
import type { AccessToken } from "@/api/agents";
import type { MockHandoff } from "./agents";
import type { Evidence, Note } from "@/api/notes";
import type { Proposal } from "@/features/proposals/api";
import type { AppNotification } from "@/features/notifications/describe";
import type { OpsState } from "./adminOps";
import type { DataState } from "./adminData";
import type { EvalMock } from "./eval";
import type { MockDraft } from "./drafts";
import type { AdminLabel, SavedSearch } from "@/api/types";

export interface MockState {
  votes: Record<string, 1 | -1 | 0>;
  extraTags: Record<string, string[]>;
  /** collections created in the mock, and docs added to any collection (id -> doc ids) */
  newCollections: { id: string; name: string }[];
  collItems: Record<string, string[]>;
  /** seeded collection members removed via DELETE /collections/{id}/items/{doc} (id -> doc ids) */
  collRemoved: Record<string, string[]>;
  /** PATCH /documents/{id} bodies applied to non-upload documents */
  docPatch: Record<string, { visibility?: string; share_users?: string[]; share_groups?: string[] }>;
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
  /** mocks/adminOps.ts; seeded lazily there */
  ops: OpsState | null;
  /** mocks/adminData.ts; seeded lazily there */
  adminData: DataState | null;
  /** mocks/drafts.ts; seeded lazily there */
  drafts: MockDraft[] | null;
  /** mocks/eval.ts; seeded lazily there */
  evalData: EvalMock | null;
  /** mocks/labels.ts; seeded lazily there */
  labels: AdminLabel[] | null;
  /** document id / note id -> label id overrides, deleted documents, transferred owners, collection rename/delete */
  docLabels: Record<string, string>;
  noteLabels: Record<string, string>;
  deletedDocs: string[];
  owners: Record<string, string>;
  collRenamed: Record<string, string>;
  collDeleted: string[];
  /** mocks/savedSearches.ts; seeded lazily there */
  savedSearches: SavedSearch[] | null;
}

const storedAdmin = () => { try { return localStorage.getItem("roracle-mock-admin") === "1"; } catch { return false; } };

const fresh = (): MockState => ({
  votes: {},
  extraTags: {},
  newCollections: [],
  collItems: {},
  collRemoved: {},
  docPatch: {},
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
  ops: null,
  adminData: null,
  drafts: null,
  labels: null,
  evalData: null,
  docLabels: {},
  noteLabels: {},
  deletedDocs: [],
  owners: {},
  collRenamed: {},
  collDeleted: [],
  savedSearches: null,
});

export const state: MockState = { ...fresh(), admin: storedAdmin() };
export const resetMockState = () => Object.assign(state, fresh());
