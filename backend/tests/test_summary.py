from types import SimpleNamespace

from roracle.ingest.summary import summarize


class FakeClient:
    def __init__(self, content):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._content = content

    def _create(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_summarize_returns_stripped_text_and_truncates_input():
    c = FakeClient("  A summary.  ")
    assert summarize(c, "m", "Title", "x" * 10000) == "A summary."
    kw = c.calls[0]
    assert kw["model"] == "m" and kw["temperature"] == 0
    assert kw["messages"][0]["role"] == "system"
    assert kw["messages"][1]["content"] == f"<title>\nTitle\n</title>\n<document>\n{'x' * 6000}\n</document>"


def test_summarize_empty_and_cap():
    assert summarize(FakeClient(None), "m", "t", "x") == ""
    assert len(summarize(FakeClient("a" * 5000), "m", "t", "x")) == 1000
