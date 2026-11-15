import type { ChatDetail, CitationOk, RetrievedPassage } from "@/api/chat";
import { DOCS, type MockDoc } from "./docs";
import { chunkId } from "./search";
import { docling } from "./pdf";

const T0 = "2026-11-10T09:00:00Z";
const T1 = "2026-11-10T09:00:30Z";

/** Citation + retrieved passage for paragraph (page index p, chunk c) of a sample doc. Ids match the mock docling chunks. */
export function passageOf(d: MockDoc, p: number, c: number, n: number) {
  const chunk_id = chunkId(d, p, c);
  const text = d.pages[p][c];
  const version_id = `${d.id}-v${d.ver}`;
  const bbox = docling(d).chunks.find((x) => x.chunk_id === chunk_id)?.bbox ?? null;
  const cite: CitationOk = { n, doc_id: d.id, version_id, version: d.ver, chunk_id, title: d.title, page: p + 1, bbox, quote: text };
  const retrieved: RetrievedPassage = { n, doc_id: d.id, version_id, chunk_id, title: d.title, page: p + 1, score: 0, text };
  return { cite, retrieved };
}

const doc = (i: number) => DOCS[i - 1];
// prototype SEED sources: [doc, page index, chunk index]
const SRC: [number, number, number][] = [[1, 0, 0], [1, 2, 0], [2, 1, 0], [2, 0, 1], [2, 2, 0], [4, 1, 0]];
const EXTRA: [number, number, number][] = [[1, 1, 0], [2, 1, 1]];

const ANSWER =
  "Partly. For random $d$-regular graphs, the spectral gap $d - \\lambda_2$ is at least $d - 2\\sqrt{d-1} - \\varepsilon$ with high probability.[1] " +
  "That proof relies on the Ihara–Bass identity, which has no exact hypergraph analogue, so it does not carry over directly.[2]\n\n" +
  "Ito replaces the identity with a diffusion process on hyperedges[3] and proves the Cheeger-type bound $h^2/2 \\le \\gamma \\le 2h$, " +
  "which confirms the conjectured analogue for $k$-uniform hypergraphs up to a factor of $(k-1)$.[4] " +
  "The constant for random hypergraphs is not optimal, but experiments put it within 8% of the conjecture.[5]\n\n" +
  "The 2016 survey by Novak still lists the hypergraph case as open.[6] Readers have marked that survey as outdated, so prefer Ito 2023 for current results.";

export function seedChats(): ChatDetail[] {
  const all = [...SRC, ...EXTRA].map(([di, p, c], i) => passageOf(doc(di), p, c, i + 1));
  const retrieved = all.map((x, i) => ({ ...x.retrieved, score: Math.round((0.93 - i * 0.07) * 100) / 100 }));
  const citations = all.slice(0, 5).map((x) => x.cite);
  const viewer = { tabs: [], ignored: [], selection: null };
  const mk = (id: string, title: string, is_owner: boolean): ChatDetail => ({
    id, title, scope: "all", model: "frontier-large", pins: [], excludes: [], created_at: T0, updated_at: T1, is_owner,
    messages: [
      { id: `${id}-m1`, role: "user", content: "Does the spectral-gap lower bound for random regular graphs extend to hypergraphs?", viewer, citations: [], retrieved: [], status: "complete", model: null, created_at: T0 },
      { id: `${id}-m2`, role: "assistant", content: ANSWER, citations: [...citations, { n: 6, available: false }], retrieved, status: "complete", model: "frontier-large", created_at: T1, rating: null },
    ],
  });
  return [mk("c1", "Spectral gap in hypergraphs", true), mk("shared-1", "Shared: spectral gap in hypergraphs", false)];
}
