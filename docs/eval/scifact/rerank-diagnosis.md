# Rerank diagnosis (scifact-test, 300 q, doc-level, offline; scripts diag_dump.py/diag_eval.py)

Adapter (retrieval.py): correct. TEI scores mapped by index (_rerank_scores), batches of rerank_max_batch merged in order, raw query (no embed prefix), passage text only (no title), cut at 2000 chars (chunks avg 1270, so no truncation), no prefixes. Chunk text does not start with the title (0.07%).

## size=200 (chat-depth)

| variant | nDCG@10 | d | R@10 | d |
|---|---|---|---|---|
| a fused baseline | 0.7673 | +0.0000 | 0.8834 | +0.0000 |
| b rerank only (text) | 0.7171 | -0.0502 | 0.8223 | -0.0611 |
| c rerank title+text | 0.7458 | -0.0216 | 0.8529 | -0.0305 |
| d RRF fused+rerank(text) | 0.7678 | +0.0005 | 0.8799 | -0.0035 |
| d RRF fused+rerank(title+text) | 0.7739 | +0.0066 | 0.8932 | +0.0098 |
| e blend a=0.3 (text) | 0.7807 | +0.0134 | 0.8982 | +0.0148 |
| e blend a=0.3 (title+text) | 0.7823 | +0.0150 | 0.8942 | +0.0108 |
| e blend a=0.5 (text) | 0.7725 | +0.0052 | 0.8899 | +0.0065 |
| e blend a=0.5 (title+text) | 0.7788 | +0.0115 | 0.8932 | +0.0098 |
| e blend a=0.7 (text) | 0.7576 | -0.0097 | 0.8810 | -0.0024 |
| e blend a=0.7 (title+text) | 0.7639 | -0.0034 | 0.8826 | -0.0008 |
| f rerank top20 only (text) | 0.7500 | -0.0173 | 0.8799 | -0.0035 |
| f rerank top50 only (text) | 0.7406 | -0.0267 | 0.8703 | -0.0131 |
| f rerank top100 only (text) | 0.7302 | -0.0371 | 0.8513 | -0.0321 |

chunk chars mean/med/max 1274.167426287299 1287 2590
text startswith title frac 0.0006637461834594451

Size=100 (search path): see below

size=100: baseline nDCG 0.7712/R@10 0.8901; rerank-only -0.0464/-0.0487; title+text -0.0240/-0.0372; RRF(title+text) +0.0049/+0.0048; blend a=0.3 text +0.0098/+0.0082, title+text +0.0111/+0.0042; a=0.5 title+text +0.0087/+0.0048; a=0.7 negative; top20/50/100-only -0.019/-0.031/-0.042 nDCG.

Root causes: (1) baseline already strong (hybrid BM25+embeddinggemma+summary kNN, nDCG 0.77 > published rerank-only SciFact ~0.72); pure-rerank order discards that signal. (2) Chunks split abstracts (~1270 chars, no title), so the reranker scores half-abstracts without title; adding title recovers about +0.025 nDCG. (3) Scores reduce to best-chunk per doc. Blending preserves fused signal: best a=0.3, gains small (+0.01-0.015, below the 0.02 gate threshold).
Recommendation: send "title\ntext" to the reranker; do not replace order with rerank order; blend normalized rerank score (min-max per query) with normalized fused score at ~0.3 (or RRF fused+rerank). Expect gain <= +0.015 nDCG on SciFact, so gate may still not PASS; judge on a set where first stage is weaker.
Gate: scifact-test is_reference set true in roracle_eval (DB update).
