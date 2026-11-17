# game-01 reranker (TEI + bge-reranker-v2-m3)

Cross-encoder behind rOracle retrieval (ADR-0015 §3). One RTX 4090; serves `POST /rerank` on
`127.0.0.1:28080` only (never exposed). Budget: p95 <= 800 ms for 200 passages.

## Install (air-gapped)

1. Connected host: `./reranker.sh pull` (pinned HF commit, `SHA256SUMS`, image tar + digest in `reranker-bundle/`).
2. Carry the bundle over; `./reranker.sh install` (rsync to `/srv/roracle/models`, verify sums, `docker load`).
3. `./reranker.sh run`, then on game-01 `./reranker.sh health`.
The container runs `HF_HUB_OFFLINE=1` with the model dir mounted read-only: nothing downloads at runtime.
Env: `HOST` (default `game-01`), `BUNDLE`, `URL`/`KEY` for `health` through a tunnel.

## Dev access

SSH tunnel `ssh -N -L 21480:127.0.0.1:28080 game-01` (like Ollama on 21434), then
`ROR_RERANK_URL=http://localhost:21480/rerank ROR_RERANK_API=tei ROR_RERANK_MODEL=bge-reranker-v2-m3`.
Live test: `uv run pytest -m integration tests/test_rerank_live.py -s` (skips without the endpoint).

## Batching

TEI runs with `--max-client-batch-size 512`; rOracle splits requests to `ROR_RERANK_MAX_BATCH` (256) texts, so any
`rerank_depth` works. `ROR_RERANK_TIMEOUT_S` (3 s) is one wall-clock budget for the whole rerank call.

## Auth

TEI has no auth; if exposed beyond the tunnel/mesh put a reverse proxy in front and set `ROR_RERANK_API_KEY`
(sent as `Authorization: Bearer`). Never logged.

## Failure behaviour

Timeout (`ROR_RERANK_TIMEOUT_S`, 3 s), HTTP error or malformed reply: fused order is kept and
`roracle_rerank_fallback_total{reason=timeout|http|malformed}` increments. Latency: `roracle_rerank_seconds`.

## Fallback server

llama.cpp `/v1/rerank` with `ROR_RERANK_API=cohere` (no adapter needed).

## Context-note model (LLM contextual chunk notes, P13 T5)

Notes are one LLM call per chunk with the whole document (up to `ROR_CONTEXT_DOC_CHARS`, 24000) in a stable
prefix, so the Ollama model needs `num_ctx >= 16384` (the default 4096 silently truncates the document).
Create a variant on game-01 (operator step; the app only references it by name):

```bash
cat > Modelfile.roracle-context <<'M'
FROM qwen2.5:14b
PARAMETER num_ctx 16384
PARAMETER temperature 0
M
ollama create roracle-context -f Modelfile.roracle-context
```

Then `ROR_CONTEXT_MODEL=roracle-context` (unset = feature off; `ROR_CONTEXT_NOTES` stays `false` until the
gate in `docs/eval/2026-11-16-context-notes.md` is PASS). Build the A/B shadow index with
`roracle rebuild --what context --into chunks-<slug>-shadow`; drop it with `roracle rebuild --drop <name>`.
`exclude_llm` documents never reach this model (heading path instead).
