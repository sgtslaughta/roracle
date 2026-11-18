# Reference set `own-corpus-v1`

The reference set for the P13 gates (`is_reference`): LLM-generated questions over the SciFact corpus (5,191 BEIR
abstracts imported by `roracle eval import-beir`) in all three scopes, so `--scope fact|multihop|global` each have
enough queries for a verdict. **LLM-curated, not admin-reviewed; review it on Admin -> Eval.** The `scifact-test`
set (BEIR qrels) is unchanged apart from losing the reference flag (only one set can hold it).

## How it was generated

`roracle eval generate` runs the same `eval_generate` job as the Eval page (`POST /api/admin/eval/sets/{id}/generate`,
now with a `kind`), as the non-admin owner of the SciFact documents (`eval_owner`), batches of 15 seeds:

```text
uv run roracle eval generate --set own-corpus-v1 --principal eval_owner --kind fact -n 15
uv run roracle eval generate --set own-corpus-v1 --principal eval_owner --kind multihop -n 15
uv run roracle eval generate --set own-corpus-v1 --principal eval_owner --kind global -n 15
uv run roracle eval curate --set own-corpus-v1 --principals eval_p1,eval_p2,eval_p3
```

- Model: `qwen2.5-14b-ctx16k` (Qwen2.5 14B with a 16k context, Ollama) via `ROR_EVAL_JUDGE_MODEL`, temperature 0.3;
  the first 107 `fact` drafts came from `qwen2.5:14b` (the default chat model) before the switch.
- Only documents the principal can see, that are LLM-allowed and searchable are sampled; the label gate is re-read in
  Postgres before any text goes to the model (`eval/generate.py`).
- `fact`: one random chunk of a random document; the question carries one verbatim quote (passage-level gold).
- `multihop`: a seed chunk plus the best BM25 chunk of another visible document; the question must combine both and
  carries a verbatim quote from each (two passage-level gold items).
- `global`: a seed chunk plus up to 9 BM25-similar documents; the model writes a library-level theme question
  ("What do the papers in the library report about ...?", which the router sends to the global path) and names the
  passages that answer it, at least 3 (document-level gold, no quote: scored by document recall).
- Quotes are checked to be verbatim in their passage; replies that fail parsing are dropped.

## Verification rule (`roracle eval curate`)

Each draft goes to the same model at temperature 0 with its anchors: the chunk holding each gold quote (a 1,500-character
window centred on the quote) or the head of each gold document. It is kept only if:

- **answerable**: the judge says the passages state the answer;
- **unambiguous**: the judge says it does not need hidden context (`needs_context` false: no unnamed 'this study' or
  'the experiment'), and it does not match the deictic pattern (`passage`, `the first study`, `one study`, ...);
- `multihop` only: no single passage answers it fully (`single_passage` false).

Near-duplicates (word-set Jaccard >= 0.8 with an earlier kept question of the same kind) and failures are deleted.
Survivors are activated and widened to `eval_p1..3` where they can see every gold document (they share all SciFact
documents), so every query has 4 non-admin principals.

## Counts

| Scope | Generated | Lost (first judge, see below) | Verified | Rejected | Duplicates | **Kept (active)** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fact` | 107 | 28 | 79 | 2 | 0 | **77** |
| `multihop` | 265 | 24 (+1 kept) | 240 | 171 | 0 | **70** |
| `global` | 148 | 0 | 148 | 59 | 5 | **84** |
| total | 520 | 52 | 467 | 232 | 5 | **231** |

- 231 active queries x 4 non-admin principals (`eval_owner`, `eval_p1..3`) = 924 query-principal pairs.
- A first judge prompt (an `unambiguous` yes/no, anchors cut at 1,200 characters) rejected nearly every sound
  question; its 52 deletions are counted as lost, not as rejections. The rule above replaced it.
- `multihop`: the first 149 drafts used a 'combine A and B' prompt that produced speculative 'how might X relate to Y'
  bridges; the stated-fact prompt now in `generate_kinds.py` produced the other 116. The pass over 124 old + 11 new
  drafts kept 23; the pass over the remaining 105 new drafts kept 46. Multihop questions here join two stated facts from two BM25-related abstracts, often on loosely related
  topics: they test fetching two documents for one question, not deep reasoning chains.
- `global` gold is the 3-10 documents the generator named; other relevant documents in the corpus are not labelled,
  so document recall is a lower bound.

## Running the gate on it

```text
uv run roracle eval compare --set own-corpus-v1 --path chat --scope <all|fact|multihop|global> --judge \
  --baseline "k=v,..." --candidate "k=v,..." --out docs/eval/<date>-<feature>.md
```

On the chat path the judge only runs with `--judge`; without it every chat verdict stays INSUFFICIENT
(`faithfulness missing`).

## Baseline (2026-11-18, chat path, embeddinggemma index, no reranker)

`uv run roracle eval run --set own-corpus-v1 --path chat --label own-corpus-v1-baseline-chat` (run
`041b99e1-6f66-40f8-a647-8c958a9dcafc`): 924 pairs, 0 empty, `acl_violations` 0, `es_acl_violations` 0, p95 190 ms.

| Scope | Queries | p_ndcg@10 | p_recall@10 | d_recall@10 | d_recall@40 | p_recall@100 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fact` | 77 | 0.732 | 0.792 | 0.987 | 1.000 | 0.805 |
| `multihop` | 70 | 0.737 | 0.764 | 1.000 | 1.000 | 0.764 |
| `global` | 84 | 0.653 | 0.726 | 0.749 | 0.911 | 0.945 |

`gate.verdict` on this run against itself reports no size or principal shortfall in any scope (231 >= 200 queries,
each scope >= 50, 4 principals, all non-admin); the remaining reasons are the missing judge and the absent gain.

- Document recall for `fact`/`multihop` is near the ceiling (LLM questions reuse the passage's words), so gains
  there will show in passage metrics and in `global`.
- Passage recall stalls near 0.8 even at depth 100 while the document is found: the chat path cuts each passage to
  `chat/retrieve.py::MAX_CHARS` (1,200) characters, and the generator quotes from the whole chunk (up to 4,000), so a
  quote past character 1,200 is never credited on the chat path (the chat model never sees it either). Both runs of a
  compare share this ceiling; the search path does not cut.
