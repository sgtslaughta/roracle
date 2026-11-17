# Eval compare: scifact-test (chat path)

**Verdict: FAIL**

- citation_support -0.0549 (loss > 0.02)
- not the reference set (the gate only judges the reference set)
- no gain: need p_ndcg@10 >= +0.02 or p_recall@10 >= +0.02 with CI lower bound > 0

Baseline run `3215a5d4-6e39-43dc-894f-ee13c4720e32` 'baseline'; candidate run `df5a79bc-b18d-4ae9-ae3f-75bd1d8f1044` 'candidate'.

## Config diff

| setting | baseline | candidate |
|---|---|---|
| rerank_api | - | tei |
| rerank_depth | - | 200 |
| rerank_model | - | bge-reranker-v2-m3 |
| rerank_url |  | <rerank-url>/rerank |

## Metrics (per-query means over shared pairs; 95% paired-bootstrap CI)

| metric | A | B | Δ | CI | queries |
|---|---|---|---|---|---|
| p_ndcg@10 | 0.7421 | 0.7227 | -0.0194 | [-0.0526, +0.0138] | 300 |
| p_recall@10 | 0.8762 | 0.8380 | -0.0382 | [-0.0740, -0.0018] | 300 |
| p_recall@100 | 0.9717 | 0.9683 | -0.0033 | [-0.0100, +0.0000] | 300 |
| p_mrr | 0.7129 | 0.6997 | -0.0132 | [-0.0503, +0.0232] | 300 |
| d_ndcg@10 | 0.7473 | 0.7258 | -0.0215 | [-0.0543, +0.0099] | 300 |
| d_recall@10 | 0.8839 | 0.8447 | -0.0393 | [-0.0743, -0.0047] | 300 |
| faithfulness | 0.3989 | 0.3953 | -0.0036 | [-0.0661, +0.0589] | 89 |
| citation_support | 0.7051 | 0.6502 | -0.0549 | [-0.1517, +0.0348] | 89 |

## By kind

| kind | pairs A | pairs B | p_ndcg@10 A | p_ndcg@10 B | p_recall@10 A | p_recall@10 B |
|---|---|---|---|---|---|---|
| fact | 1200 | 1200 | 0.7421 | 0.7227 | 0.8762 | 0.8380 |

## ACL and latency

| | A | B |
|---|---|---|
| acl_violations | 0 | 0 |
| es_acl_violations | 0 | 0 |
| empty pairs | 0 | 0 |
| latency p50 / p95 ms | 177 / 214 | 414 / 545 |
