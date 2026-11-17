import pytest

from roracle.chat.intent import contentless, summary_intent

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
