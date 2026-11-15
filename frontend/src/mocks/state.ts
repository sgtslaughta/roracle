import { DOCS } from "./docs";
import { seedUploads, type MockUpload } from "./uploads";

export interface MockState {
  votes: Record<string, 1 | -1 | 0>;
  extraTags: Record<string, string[]>;
  removedTags: Record<string, string[]>;
  versions: Record<string, number>;
  uploads: MockUpload[];
  lastAdvance: number;
}

const fresh = (): MockState => ({
  votes: {},
  extraTags: {},
  removedTags: {},
  versions: Object.fromEntries(DOCS.map((d) => [d.id, d.ver])),
  uploads: seedUploads(),
  lastAdvance: Date.now(),
});

export const state: MockState = fresh();
export const resetMockState = () => Object.assign(state, fresh());
