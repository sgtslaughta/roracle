from roracle.chat.lexical import chat_bm25
from roracle.retrieval import UNIT_BM25_FACTOR, cap_per_doc


def _should(clause):
    return clause["bool"]["should"]


def test_bm25_leg_gets_the_filler_free_query():
    mm = _should(chat_bm25("find things on robots", ("chunk",)))[0]["multi_match"]
    assert mm["query"] == "robots"


def test_title_and_tags_outweigh_abstract_and_body():
    sh = _should(chat_bm25("robots", ("chunk",)))
    fields = dict((f.split("^") + ["1"])[:2] for f in sh[0]["multi_match"]["fields"])
    weights = {k: float(v) for k, v in fields.items()}
    assert weights["title"] > weights["abstract"] > weights["text"]
    assert weights["headings"] == weights["context"] == weights["text"] == 1.0
    tag = next(c["terms"] for c in sh if "terms" in c)
    assert tag["boost"] >= weights["title"] and "robots" in tag["tags"]


def test_tags_match_normalized_words_phrase_and_stem_prefix():
    sh = _should(chat_bm25("papers about Machine Learning robots", ("chunk",)))
    tags = next(c["terms"]["tags"] for c in sh if "terms" in c)
    assert {"machine", "learning", "robots", "machine-learning-robots"} <= set(tags)
    prefixes = {c["prefix"]["tags"]["value"] for c in sh if "prefix" in c}
    assert "robot" in prefixes  # "robots" reaches the #robotics tag
    assert "machine" in prefixes and not any(len(p) < 5 for p in prefixes)


def test_any_single_field_match_is_enough():
    assert chat_bm25("robots", ("chunk",))["bool"]["minimum_should_match"] == 1


def test_summary_units_are_demoted():
    c = chat_bm25("robots", ("chunk", "section"))
    assert c["boosting"]["negative_boost"] == UNIT_BM25_FACTOR
    assert c["boosting"]["positive"] == chat_bm25("robots", ("chunk",))


def _p(doc, n):
    from roracle.retrieval import Passage

    return Passage(f"{doc}:{n}", doc, "v", "", "", [], [], 1.0)


def test_cap_per_doc_keeps_the_best_n_of_each_document_in_order():
    ps = [_p("a", 0), _p("a", 1), _p("b", 0), _p("a", 2), _p("b", 1), _p("c", 0)]
    assert [x.chunk_id for x in cap_per_doc(ps, 1)] == ["a:0", "b:0", "c:0"]
    assert [x.chunk_id for x in cap_per_doc(ps, 2)] == ["a:0", "a:1", "b:0", "b:1", "c:0"]
    assert cap_per_doc(ps, None) == ps and cap_per_doc(ps, 0) == ps
