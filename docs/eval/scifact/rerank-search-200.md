# Eval compare: scifact-test (search path)

**Verdict: INSUFFICIENT**

- not the reference set (the gate only judges the reference set)
- no gain: need p_ndcg@10 >= +0.02 or p_recall@10 >= +0.02 with CI lower bound > 0

Baseline run `0509f9c3-bd70-461a-b9ec-521bc7b826c0` 'baseline'; candidate run `db7316da-5de5-4402-88a8-44301377abfa` 'candidate'.

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
| p_ndcg@10 | 0.7651 | 0.7114 | -0.0537 | [-0.0874, -0.0192] | 300 |
| p_recall@10 | 0.8859 | 0.8119 | -0.0740 | [-0.1132, -0.0383] | 300 |
| p_recall@100 | 0.9710 | 0.9683 | -0.0027 | [-0.0100, +0.0013] | 300 |
| p_mrr | 0.7408 | 0.6953 | -0.0455 | [-0.0816, -0.0072] | 300 |
| d_ndcg@10 | 0.7712 | 0.7248 | -0.0464 | [-0.0791, -0.0132] | 300 |
| d_recall@10 | 0.8901 | 0.8413 | -0.0487 | [-0.0847, -0.0144] | 300 |

## By kind

| kind | pairs A | pairs B | p_ndcg@10 A | p_ndcg@10 B | p_recall@10 A | p_recall@10 B |
|---|---|---|---|---|---|---|
| fact | 1200 | 1200 | 0.7651 | 0.7114 | 0.8859 | 0.8119 |

## ACL and latency

| | A | B |
|---|---|---|
| acl_violations | 0 | 0 |
| es_acl_violations | 0 | 0 |
| empty pairs | 0 | 0 |
| latency p50 / p95 ms | 187 / 222 | 426 / 581 |
