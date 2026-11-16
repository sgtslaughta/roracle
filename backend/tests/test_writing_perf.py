"""2 MiB adversarial inputs must parse and pass every deterministic check in linear time."""

import signal
import time
import uuid

import pytest

from roracle.writing import checks
from roracle.writing.markdown import parse
from roracle.writing.profiles import Profile

P = Profile(name="t", title="T", required_sections=["Intro"])
MB2 = 2 * 1024 * 1024
RULES = [
    r
    for r in Profile.model_fields["rules"].annotation.__args__[0].__args__
    if r not in ("style", "citations")
]


def rep(unit: str) -> str:
    return (unit * (MB2 // len(unit) + 1))[:MB2]


CASES = {
    "labels": rep("{#fig:a"),
    "images": rep("![]("),
    "image_labels": rep("![a](b){#fig:a"),
    "heading_ws": "# a" + " " * MB2 + "x",
    "headings": rep("# a\n"),
    "nested_headings": rep("# a\n## b\n### c\n## d\n"),
    "brackets": rep("[x] "),
    "cite_brackets": rep("[@x] "),
    "at_run": "[" + "@" * MB2,
    "acr_same": rep("ABC "),
    "acr_distinct": " ".join(f"A{i:x}Z" for i in range(MB2 // 6)),
    "doc_cites": rep(f"[@doc:{uuid.uuid4()}] ")[:MB2],
    "doc_cites_distinct": "".join(f"[@doc:{uuid.uuid4()}] " for _ in range(MB2 // 46)),
    "dollars": rep("$a"),
    "display": rep("$$a$$ "),
    "math_distinct": " ".join(f"${'a'}_{i}$" for i in range(MB2 // 9)),
    "code_ticks": rep("``a"),
    "ticks": rep("`"),
    "dots": "@fig:a" + ".a" * (MB2 // 2),
    "sentences": rep(". A"),
    "spaces_after_dot": ". " + " " * MB2 + "x",
    "units": rep("10kg 5 mph "),
    "fences": rep("```x\n"),
}


@pytest.fixture(autouse=True)
def _alarm():  # a quadratic regex must fail fast, not hang the suite
    signal.signal(signal.SIGALRM, lambda *_: pytest.fail("too slow (>20s)"))
    signal.alarm(20)
    yield
    signal.alarm(0)


@pytest.mark.parametrize("name", CASES)
def test_linear(name):
    t = time.perf_counter()
    p = parse(CASES[name])
    for r in RULES:
        getattr(checks, f"check_{r}")(p, P)
    checks.check_citations(p, P, {}, 200)
    took = time.perf_counter() - t
    print(f"PERF {name} {took:.2f}s")
    assert took < 2, took
