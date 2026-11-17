# ruff: noqa: F811
"""P13 T6: unit filters, expand_units (R11) and the chat path (fake ES, real Postgres)."""

from types import SimpleNamespace

from api_seed import seed
from test_chat_retrieve import FakeLLM, hit, me, other  # noqa: F401

from roracle.auth import Principal
from roracle.chat.retrieve import rank_for_chat
from roracle.labels.policy import Policy
from roracle.retrieval import Passage, _filters, expand_units
from roracle.search_acl import acl_filter


def unit_hit(doc, ver, kind="section", key="s0", sources=(0, 1, 2), text="summary of things"):
    h = hit(doc, ver, text=text)
    h["_source"].update(
        chunk_id=f"{ver.id}:{key}", unit=kind, source_chunks=[f"{ver.id}:{n}" for n in sources], pages=[1, 2]
    )
    return h


class UnitES:
    """Main queries return every hit; `ids` queries honour ids + doc/version pins like real ES would."""

    def __init__(self, hits):
        self.hits, self.calls = list(hits), []

    def search(self, **kw):
        self.calls.append(kw)
        if "knn" in kw:
            return {"hits": {"hits": []}}
        flt = kw["query"]["bool"]["filter"]
        ids = next((f["ids"]["values"] for f in flt if "ids" in f), None)
        if ids is None:
            return {"hits": {"hits": list(self.hits)}}
        pins = {k: f["term"][k] for f in flt for k in ("doc_id", "version_id") if k in f.get("term", {})}
        return {
            "hits": {
                "hits": [
                    h for h in self.hits
                    if h["_source"]["chunk_id"] in ids and not h["_source"].get("unit")
                    and all(h["_source"][k] == v for k, v in pins.items())
                ]
            }
        }  # fmt: skip


def test_filters_units_default_is_chunk_only(me):
    pol = SimpleNamespace(ids=lambda g: set())
    chunk_only = _filters(me, pol, {}, None, False)
    assert {
        "bool": {
            "should": [{"bool": {"must_not": [{"exists": {"field": "unit"}}]}}],
            "minimum_should_match": 1,
        }
    } in chunk_only
    both = _filters(me, pol, {}, None, False, ("chunk", "section", "document"))
    (u,) = [f for f in both if "should" in f.get("bool", {}) and "minimum_should_match" in f["bool"]][-1:]
    assert {"terms": {"unit": ["section", "document"]}} in u["bool"]["should"]


def test_pat_unit_filter_keeps_block_agents(me):
    class Pol:
        def ids(self, g):
            return {"lab"} if g == "block_agents" else set()

    pat = Principal(me.user_id, "s", "r", (), False, via="pat", scopes=frozenset({"read"}))
    flt = _filters(pat, Pol(), {}, None, True, ("chunk", "section", "document"))
    assert acl_filter(pat, Pol()) in flt  # units carry label_id: same ES gate as chunks
    assert any("block_agents" not in str(f) and "label_id" in str(f) for f in flt)


def test_expand_replaces_unit_by_source_passages(db_session, settings, me, other):
    d, v = seed(db_session, other.id, title="D")
    chunks = []
    for n in range(3):
        h = hit(d, v, chunk=n, pages=(n + 1,), text=("beta " if n == 1 else "") + "alpha")
        chunks.append(h)
    es = UnitES([*chunks, unit_hit(d, v)])
    u = Passage(f"{v.id}:s0", str(d.id), str(v.id), "sum", "", [1, 2], [], 0.5, unit="section",
                source_chunks=[f"{v.id}:{n}" for n in range(3)])  # fmt: skip
    out = expand_units(es, settings, me, Policy.load(db_session), "beta", [u], exclude_llm=True)
    assert len(out) == 2 and all(x.unit is None for x in out)
    assert out[0].chunk_id == f"{v.id}:1" and {x.chunk_id for x in out} <= {
        c["_source"]["chunk_id"] for c in chunks
    }
    q = es.calls[0]["query"]["bool"]["filter"]
    assert acl_filter(me, Policy.load(db_session)) in q and {"terms": {"doc_id": [str(d.id)]}} in q


def test_expand_drops_unit_pointing_at_another_document(db_session, settings, me, other):
    mine, mv = seed(db_session, me.user_id, title="Mine")
    secret, sv = seed(db_session, other.id, title="S", visibility="private")
    es = UnitES([hit(secret, sv, chunk=0), unit_hit(mine, mv, sources=())])
    u = Passage(f"{mv.id}:s0", str(mine.id), str(mv.id), "sum", "", [], [], 0.5, unit="section",
                source_chunks=[f"{sv.id}:0"])  # fmt: skip
    assert expand_units(es, settings, me, Policy.load(db_session), "q", [u], exclude_llm=True) == []


def test_chat_path_never_returns_units_and_cites_real_chunks(db_session, settings, me, other):
    d, v = seed(db_session, other.id, title="D")
    es = UnitES([unit_hit(d, v, kind="document", key="d"), hit(d, v, chunk=1, pages=(3,), text="alpha body")])
    cfg = settings.model_copy(update={"raptor": True})
    out = rank_for_chat(db_session, es, FakeLLM(), cfg, me, "alpha")
    assert out and all(x.unit is None and x.source_chunks == [] for x in out)
    assert {x.chunk_id for x in out} == {f"{v.id}:1"} and out[0].pages == [3]
    units_asked = [
        c for c in es.calls if "knn" not in c and not any("ids" in f for f in c["query"]["bool"]["filter"])
    ]
    assert units_asked  # the main search ran with the unit-aware filter


def test_chat_path_unit_of_invisible_doc_dropped(db_session, settings, me, other):
    secret, sv = seed(db_session, other.id, title="S", visibility="private")
    es = UnitES([unit_hit(secret, sv), hit(secret, sv, chunk=0)])  # ES (wrongly) returned it
    cfg = settings.model_copy(update={"raptor": True})
    assert rank_for_chat(db_session, es, FakeLLM(), cfg, me, "alpha") == []


def test_chat_default_off_searches_chunks_only(db_session, settings, me, other):
    d, v = seed(db_session, other.id, title="D")
    es = UnitES([hit(d, v, chunk=0)])
    rank_for_chat(db_session, es, FakeLLM(), settings, me, "alpha")
    assert "section" not in str(es.calls[0]["query"]["bool"]["filter"])


def test_expand_is_one_search_for_many_units(db_session, settings, me, other):
    d, v = seed(db_session, other.id, title="D")
    hs = [hit(d, v, chunk=n, text=f"alpha {n}") for n in range(6)]
    es = UnitES(hs)
    us = [
        Passage(f"{v.id}:s{i}", str(d.id), str(v.id), "sum", "", [], [], 0.9 - i / 10, unit="section",
                source_chunks=[f"{v.id}:{2 * i}", f"{v.id}:{2 * i + 1}"])
        for i in range(3)
    ]  # fmt: skip
    out = expand_units(es, settings, me, Policy.load(db_session), "alpha", us, exclude_llm=True)
    assert len(es.calls) == 1 and len(out) == 6 and all(x.unit is None for x in out)


def test_expand_only_top_units(db_session, settings, me, other):
    d, v = seed(db_session, other.id, title="D")
    es = UnitES([hit(d, v, chunk=n) for n in range(4)])
    us = [
        Passage(f"{v.id}:s{i}", str(d.id), str(v.id), "s", "", [], [], 1.0 - i / 10, unit="section",
                source_chunks=[f"{v.id}:{i}"])
        for i in range(4)
    ]  # fmt: skip
    out = expand_units(es, settings, me, Policy.load(db_session), "q", us, exclude_llm=True, max_units=2)
    assert {x.chunk_id for x in out} == {f"{v.id}:0", f"{v.id}:1"}


def test_unit_bm25_demoted_only_when_units_requested():
    from roracle.retrieval import _bm25_clause

    assert "multi_match" in _bm25_clause("q", ("chunk",))
    b = _bm25_clause("q", ("chunk", "section"))["boosting"]
    assert b["negative"] == {"exists": {"field": "unit"}} and b["negative_boost"] < 1
