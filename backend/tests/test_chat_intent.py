import pytest

from roracle.chat.intent import contentless, lexical_query, summary_intent

CHIPS = [
    "Summarize this paper", "Key findings", "Methods used", "Limitations", "Explain for a non-specialist",
]  # fmt: skip


@pytest.mark.parametrize("q", ["give me a summary", "what is this paper about?", "tl;dr", "ELI5", *CHIPS])
def test_both(q):
    assert summary_intent(q) and contentless(q)


def test_intent_but_content():
    q = "summarize the effects of statins on LDL"
    assert summary_intent(q) and not contentless(q)


def test_no_intent():
    assert not summary_intent("fenugreek") and not summary_intent("Compare A and B")
    assert not contentless("effects of statins on LDL")


@pytest.mark.parametrize(
    "q", ["what are the limitations of CRISPR?", "summary of BERT", "give me an overview of transformers"]
)
def test_topic_questions_are_not_contentless(q):
    assert summary_intent(q) and not contentless(q)


@pytest.mark.parametrize(
    "q",
    [
        "find things on robots", "I am looking for info on robots", "papers about robots", "robots",
        "Can you show me any papers about robots?", "what do we have on robots", "anything on robots too?",
    ],
)  # fmt: skip
def test_lexical_query_strips_request_filler(q):
    assert lexical_query(q) == "robots"


def test_lexical_query_keeps_content_order_and_case():
    assert lexical_query("find papers on Graph Neural Networks for drug discovery") == (
        "Graph Neural Networks drug discovery"
    )


@pytest.mark.parametrize("q", ["find things", "can you show me?", "papers", "  "])
def test_lexical_query_falls_back_to_raw_when_nothing_is_left(q):
    assert lexical_query(q) == q


@pytest.mark.parametrize(
    ("q", "kept"),
    [
        ("Roboter in der Fertigung", "Roboter der Fertigung"),
        ("trouver des travaux sur la robotique", "trouver des travaux sur la robotique"),
        ("robots autónomos y percepción", "robots autónomos y percepción"),
        ("机器人 论文", "机器人 论文"),
        ("ロボット工学", "ロボット工学"),
    ],
)
def test_lexical_query_never_strips_non_english_words(q, kept):
    """Only a closed list of English function/request words is dropped; every other word is kept."""
    assert lexical_query(q) == kept


def test_lexical_query_keeps_punctuated_terms():
    assert lexical_query("find stuff on C++ and COVID-19") == "C++ COVID-19"
