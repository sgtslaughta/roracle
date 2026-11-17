"""Stand-in model server for the perf environment: OpenAI-style `/v1/embeddings` and a TEI-style `/rerank`, with a
configurable fixed latency so the measured numbers are "our code + a model that answers in X ms"."""

import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from roracle.perf.synth_es import CLUSTERS, clustered


def vec(text: str, dims: int) -> list[float]:
    """Query embedding: a point near a topic centre of the synthetic corpus (see synth_es.clustered)."""
    h = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
    return clustered(np.random.default_rng(h), h % CLUSTERS, dims).tolist()


def serve(port: int, *, dims: int = 768, embed_ms: float = 10, rerank_ms: float = 50) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            if self.path.endswith("/embeddings"):
                time.sleep(embed_ms / 1000)
                inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
                out = {"object": "list", "model": body.get("model", "perf"), "usage": {"prompt_tokens": 1, "total_tokens": 1},
                       "data": [{"object": "embedding", "index": i, "embedding": vec(t, dims)} for i, t in enumerate(inputs)]}  # fmt: skip
            else:  # /rerank: TEI shape [{index, score}]
                time.sleep(rerank_ms / 1000)
                out = [{"index": i, "score": 1.0 / (1 + i)} for i in range(len(body.get("texts", [])))]
            raw = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
