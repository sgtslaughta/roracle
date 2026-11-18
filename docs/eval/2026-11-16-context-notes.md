# Eval: LLM contextual chunk notes (index-time)

**Verdict: PENDING** (not run on own-corpus-v1; runs in the target network, see the last section). `context_notes` stays
`false` in `config.py` until this report holds a PASS (plan: default flip rule, Task 5).

## Reproduce

```text
export ROR_CONTEXT_MODEL=roracle-context          # Ollama variant, num_ctx 16384 (deploy/game-01/README.md)
uv run roracle rebuild --what context --into chunks-<slug>-shadow   # worker builds it; live search untouched
uv run roracle eval compare --set <reference set> --path chat \
  --baseline "" --candidate-index chunks-<slug>-shadow \
  --out docs/eval/2026-11-16-context-notes.md
uv run roracle rebuild --drop chunks-<slug>-shadow                  # afterwards
```

(The compare command overwrites this file with the gate table.)

## On PASS

Set `context_notes: bool = True` in `config.py`, set `ROR_CONTEXT_MODEL` per site, then adopt with
`roracle rebuild --what context` (active index; one full re-embed because `EMBED_PROMPT_VERSION` is 2).

## Run in the target network

Not run on own-corpus-v1 (2026-11-18): the single GPU here was too slow, so this gate runs where there is more capacity.
Reference set, single-model-name rule, shadow-index build, exact compare command and expected durations:
[Run the pending gates in the target network](README.md#run-the-pending-gates-in-the-target-network).
The feature stays off until this file holds a PASS.
