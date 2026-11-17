"""Open-loop load generator: requests arrive on a fixed schedule whatever the server does, and latency is
measured from the *scheduled* arrival (no coordinated omission). One `httpx.Client` per worker thread."""

import itertools
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import httpx

from roracle.perf.report import Report
from roracle.perf.synth_pg import det_uuid

Scenario = Literal["search", "search_filtered", "browse", "chat_retrieve", "mcp_search", "doc_read"]
SCENARIOS = ("search", "search_filtered", "browse", "chat_retrieve", "mcp_search", "doc_read")


def build_request(scenario: str, n: int, *, docs: int = 1000, seed: int = 1) -> tuple[str, str, dict]:
    """(method, path, kwargs) of the n-th request of a scenario; deterministic."""
    word = f"w{1 + (n * 7919) % 200}"  # head of the Zipf vocabulary: always has hits
    match scenario:
        case "search":
            return "GET", "/search", {"params": {"q": word, "size": 20}}
        case "search_filtered":
            return "GET", "/search", {"params": {"q": word, "size": 20, "tags": f"tag{n % 500}"}}
        case "browse":
            return "GET", "/search", {"params": {"q": "", "sort": "newest", "size": 20}}
        case "chat_retrieve":  # retrieval half of a chat turn (the LLM is out of scope): wide, notes on
            return "GET", "/search", {"params": {"q": f"{word} w{1 + n % 50}", "size": 50, "notes": "true"}}
        case "mcp_search":
            body = {
                "jsonrpc": "2.0",
                "id": n,
                "method": "tools/call",
                "params": {"name": "search", "arguments": {"query": word}},
            }
            return (
                "POST",
                "/mcp",
                {"json": body, "headers": {"Accept": "application/json, text/event-stream"}},
            )
        case "doc_read":
            return "GET", f"/documents/{det_uuid(seed, 'd', (n * 7919) % max(docs, 1))}", {}
    raise ValueError(f"unknown scenario {scenario!r}")


def run(
    base_url: str,
    tokens: list[str],
    scenario: Scenario,
    *,
    rps: float,
    duration_s: int,
    concurrency: int,
    docs: int = 1000,
    seed: int = 1,
) -> Report:
    local = threading.local()
    lat: list[float] = []
    errors = 0
    lock = threading.Lock()
    counter = itertools.count()

    clients: list[httpx.Client] = []

    def client() -> httpx.Client:  # one per thread; the persona's token travels with each request
        if getattr(local, "c", None) is None:
            local.c = httpx.Client(base_url=base_url, timeout=30)
            with lock:
                clients.append(local.c)
        return local.c

    def fire(n: int, due: float) -> None:
        nonlocal errors
        method, path, kw = build_request(scenario, n, docs=docs, seed=seed)
        try:
            kw["headers"] = {**kw.get("headers", {}), "Authorization": f"Bearer {tokens[n % len(tokens)]}"}
            r = client().request(method, path, **kw)
            ok = r.status_code < 400
        except httpx.HTTPError:
            ok = False
        ms = (time.monotonic() - due) * 1000
        with lock:
            if ok:
                lat.append(ms)
            else:
                errors += 1

    total = int(rps * duration_s)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for _ in range(total):
            n = next(counter)
            due = start + n / rps
            if (wait := due - time.monotonic()) > 0:
                time.sleep(wait)
            pool.submit(fire, n, due)
    for c in clients:
        c.close()
    return Report.of(lat, errors)
