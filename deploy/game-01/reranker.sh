#!/usr/bin/env bash
# Cross-encoder reranker (HF TEI + BAAI/bge-reranker-v2-m3) on game-01. Idempotent; air-gap friendly.
#   reranker.sh pull     connected host: download pinned model + sha256 manifest, save TEI image
#   reranker.sh install  copy bundle to $HOST:/srv/roracle/models, docker load
#   reranker.sh run      (re)start the container on $HOST, bound to 127.0.0.1:28080
#   reranker.sh health   /health + smoke /rerank (relevant text must rank first)
set -euo pipefail

HOST="${HOST:-game-01}"
BUNDLE="${BUNDLE:-./reranker-bundle}"
MODEL_REPO=BAAI/bge-reranker-v2-m3
MODEL_REV=953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e   # HF commit; bump deliberately
MODEL_DIR=bge-reranker-v2-m3
IMAGE=ghcr.io/huggingface/text-embeddings-inference:89-1.9   # Ada (4090); pin by digest in the mirror
PORT=28080
REMOTE_MODELS=/srv/roracle/models
FILES=(config.json model.safetensors sentencepiece.bpe.model special_tokens_map.json tokenizer.json tokenizer_config.json)

pull() {
  mkdir -p "$BUNDLE"
  hf download "$MODEL_REPO" --revision "$MODEL_REV" --local-dir "$BUNDLE/$MODEL_DIR" "${FILES[@]}"
  (cd "$BUNDLE/$MODEL_DIR" && sha256sum "${FILES[@]}" >SHA256SUMS)
  docker pull "$IMAGE"
  docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE" | tee "$BUNDLE/image.digest"
  [ -s "$BUNDLE/tei.tar" ] || docker save "$IMAGE" -o "$BUNDLE/tei.tar"
}

install() {
  [ -d "$BUNDLE/$MODEL_DIR" ] || { echo "run pull first" >&2; exit 1; }
  ssh "$HOST" "sudo mkdir -p $REMOTE_MODELS && sudo chown \$USER $REMOTE_MODELS"
  rsync -a --partial "$BUNDLE/$MODEL_DIR" "$HOST:$REMOTE_MODELS/"
  ssh "$HOST" "cd $REMOTE_MODELS/$MODEL_DIR && sha256sum -c SHA256SUMS"
  ssh "$HOST" docker load <"$BUNDLE/tei.tar"
}

run() {
  ssh "$HOST" "docker rm -f roracle-reranker >/dev/null 2>&1 || true; docker run -d --name roracle-reranker \
    --restart unless-stopped --gpus all -p 127.0.0.1:$PORT:80 -v $REMOTE_MODELS:/data:ro -e HF_HUB_OFFLINE=1 \
    $IMAGE --model-id /data/$MODEL_DIR --dtype float16 --max-client-batch-size 256 \
    --max-batch-tokens 65536 --auto-truncate"
}

health() {  # on game-01 (or through the tunnel: URL=http://localhost:21480)
  local url="${URL:-http://localhost:$PORT}" r
  curl -fsS "$url/health" >/dev/null
  r=$(curl -fsS "$url/rerank" -H 'content-type: application/json' ${KEY:+-H "Authorization: Bearer $KEY"} \
    -d '{"query":"Which organelle produces ATP?","texts":["Stock markets rose.","Mitochondria produce most cellular ATP."]}')
  python3 -c 'import json,sys; r=json.loads(sys.argv[1]); assert max(r,key=lambda x:x["score"])["index"]==1, r; print("rerank ok")' "$r"
}

case "${1:-}" in pull) pull ;; install) install ;; run) run ;; health) health ;;
  *) echo "usage: $0 pull|install|run|health" >&2; exit 2 ;; esac
