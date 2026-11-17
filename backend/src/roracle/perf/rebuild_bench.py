"""Re-embed / rebuild throughput on the seeded corpus (the budget is >= 1,000 chunks/s with 4 replicas; the perf audit
estimates ~250 chunks/s per real replica, i.e. exactly the budget when replica-bound. To test that OUR pipeline is not the
bottleneck the model is given 400 chunks/s per replica (1,600/s ideal): the measured rate is then the pipeline's own cap).

Re-embed / rebuild throughput on the seeded corpus: the real `reembed._copy` page pipeline (Postgres ACL read, prompts,
embedding calls, bulk write, verify) against the perf index, with an embedding service modelled as `replicas`
replicas that each embed `per_replica_cps` chunks/s, driven by
`slices` parallel slice workers like the production re-embed."""

import threading
import time
from types import SimpleNamespace

from roracle import reembed, search_index
from roracle.config import Settings


class ReplicaLlm:
    """OpenAI-shaped embeddings client in front of `replicas` replicas (a semaphore), service time = n / cps."""

    def __init__(self, dims: int, replicas: int, per_replica_cps: float):
        self.dims, self.cps = dims, per_replica_cps
        self.gate = threading.Semaphore(replicas)
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, model, input):
        with self.gate:
            time.sleep(len(input) / self.cps)
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.1] * self.dims) for i in range(len(input))]
        )


def run(es, sf, settings: Settings, *, source: str, chunks: int, replicas: int = 4, per_replica_cps: float = 400.0,
        slices: int = 4, target: str = "perf-reembed-bench") -> dict:  # fmt: skip
    """Copy `chunks` chunks of `source` into a fresh `target` (perf-*) and report chunks/s."""
    if not target.startswith("perf-"):
        raise ValueError("benchmark target must be a perf-* index")
    if es.indices.exists(index=target):
        es.indices.delete(index=target)
    es.indices.create(
        index=target, mappings=search_index._mapping(settings.embed_dims, settings),
        settings={"index": {**search_index.index_settings(settings)["index"], "number_of_replicas": 0, "refresh_interval": "30s"}},
    )  # fmt: skip
    tgt = {"model": "perf-new", "dims": settings.embed_dims, "index": target}
    llm = ReplicaLlm(settings.embed_dims, replicas, per_replica_cps)
    per_slice = max(1, chunks // slices)
    done = [0] * slices
    errs: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            cursor = None
            q = reembed._slice_query({"i": i, "n": slices})
            while done[i] < per_slice:
                kw = {"query": q} if q else {}
                hits = es.search(
                    index=source, size=min(reembed.BATCH, per_slice - done[i]), sort=[{"chunk_id": "asc"}],
                    search_after=[cursor] if cursor else None, source_excludes=reembed._VECTORS, **kw,
                )["hits"]["hits"]  # fmt: skip
                if not hits:
                    return
                reembed._copy(es, llm, sf, hits, tgt, source, settings)
                done[i] += len(hits)
                cursor = hits[-1]["sort"][0]
        except BaseException as e:  # noqa: BLE001  # reported by the caller
            errs.append(e)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(slices)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    secs = time.perf_counter() - t0
    if errs:
        raise errs[0]
    es.indices.delete(index=target)
    n = sum(done)
    return {"chunks": n, "seconds": round(secs, 1), "chunks_per_s": round(n / secs, 1), "replicas": replicas,
            "per_replica_cps": per_replica_cps, "slices": slices, "embed_concurrency": settings.embed_concurrency}  # fmt: skip
