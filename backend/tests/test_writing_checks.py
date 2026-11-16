import uuid

from roracle.writing import checks
from roracle.writing.cite_check import parse_key
from roracle.writing.markdown import parse
from roracle.writing.profiles import Limits, Profile

P = Profile(name="t", title="T")
D = uuid.uuid4()


def rules(fn, md, prof=P, *a):
    return [f.message for f in fn(parse(md), prof, *a)]


def test_parse_front_matter_sections_masking():
    p = parse(
        "---\ntitle: X\nabstract: one two three\n---\n# A\n\n```\n# not a heading\nQQQ\n```\n## 1. B {#sec:b}\n"
    )
    assert p.meta["title"] == "X" and [s.title for s in p.sections] == ["A", "1. B"]
    assert p.sections[1].line == 11 and p.abstract_words == 3 and p.labels == {"sec:b"}
    assert parse("---\n- a\n---\n").meta_issue and parse("---\na: [\n---\n").meta_issue
    assert parse("no front matter").meta == {}


def test_words_exclude_code_math_references():
    p = parse(
        "one two $x y z$ `code more` three\n\n```\nlots of code here\n```\n$$\na b c\n$$\n\n## References\n\nfoo bar baz\n"
    )
    assert p.words == 4  # one two three + the References heading; its body is excluded


def test_citations_and_crossrefs_not_citations():
    k = f"@doc:{D}"
    p = parse(
        f"Claim A holds [{k}; @psg:{D}:12]. See @fig:x and [@tbl:y]. Bare @doc:{D} too. `[@doc:{uuid.uuid4()}]`\n"
    )
    assert [c.key for c in p.citations] == [f"doc:{D}", f"psg:{D}:12", f"doc:{D}"]
    assert p.citations[0].sentence == "Claim A holds ."
    assert [r for r, _ in p.refs] == ["fig:x", "tbl:y"]


def test_parse_key():
    assert parse_key(f"doc:{D}") == ("doc", D) and parse_key(f"psg:{D}:3") == ("psg", D, 3)
    for bad in ("doc:xyz", f"psg:{D}", f"psg:{D}:x", f"doc:{{{D}}}", "foo:1"):
        assert parse_key(bad) is None


def test_structure():
    prof = P.model_copy(update={"required_sections": ["Introduction", "Methods"]})
    assert rules(checks.check_structure, "## 1. Introduction\n", prof) == ['missing section "Methods"']
    assert rules(checks.check_structure, "## introduction\n## METHODS\n", prof) == []


def test_metadata():
    prof = P.model_copy(update={"required_metadata": ["title", "authors"]})
    assert rules(checks.check_metadata, "---\ntitle: T\nauthors: []\n---\n", prof) == [
        'missing metadata "authors"'
    ]
    assert rules(checks.check_metadata, "---\ntitle: T\nauthors: [a]\n---\n", prof) == []
    assert rules(checks.check_metadata, "---\n- x\n---\n")


def test_limits():
    prof = P.model_copy(
        update={
            "limits": Limits(
                words_max=3, abstract_words_max=2, title_chars_max=3, figures_max=1, references_max=1
            )
        }
    )
    md = f"---\ntitle: Long title\nabstract: a b c\n---\none two three four\n![a](x.png) ![b](y.png) [@doc:{D}] [@doc:{uuid.uuid4()}]\n"
    msgs = rules(checks.check_limits, md, prof)
    assert len(msgs) == 5
    assert rules(checks.check_limits, "one two", prof) == []


def test_notation():
    assert rules(checks.check_notation, "The value $x$ grows.\n") == [
        "symbol $x$ is used before it is defined"
    ]
    assert (
        rules(checks.check_notation, "Let $x$ be the rate and $\\alpha_1$ where small. Then $x$ grows.\n")
        == []
    )
    assert len(rules(checks.check_notation, "We use $\\alpha_1$ and $\\alpha_1$ and $x+y$ and `$z$`.\n")) == 1


def test_acronyms():
    assert rules(checks.check_acronyms, "We use NLP here. NLP again.\n") == [
        "acronym NLP is not defined at first use"
    ]
    assert rules(checks.check_acronyms, "Natural language processing (NLP) works. NLPs too.\n") == []
    assert rules(checks.check_acronyms, "NLP (natural language processing) works.\n") == []
    assert rules(checks.check_acronyms, "# NLP\n\n```\nGPU\n```\n$ABC$ and `XYZ`\n") == []
    prof = P.model_copy(update={"acronym_allowlist": ["NLP"]})
    assert rules(checks.check_acronyms, "We use NLP.\n", prof) == []


def test_crossrefs():
    assert rules(checks.check_crossrefs, "See @fig:a.\n") == ["reference @fig:a has no matching label"]
    assert rules(checks.check_crossrefs, "![x](y.png){#fig:a} See @fig:a and @eq:q.\n$$x$$ {#eq:q}\n") == []
    assert rules(checks.check_crossrefs, "![x](y){#fig:a}\n![x](z){#fig:a}\n") == ["duplicate label fig:a"]


def test_alt_text():
    assert rules(checks.check_alt_text, "![](a.png)\n") == ["image has no alt text"]
    assert rules(checks.check_alt_text, "![a plot](a.png)\n") == []


def test_si_units():
    assert len(rules(checks.check_si_units, "He ran 5 mph for 3 hrs, 10kg, 70 °F.\n")) == 4
    assert rules(checks.check_si_units, "He ran 5 m/s for 3 h with 10 kg, 5% and 20°C and `10kg`.\n") == []


def test_ai_disclosure():
    ok = "---\nai_assistance: {used: true, description: drafted intro}\n---\n"
    assert rules(checks.check_ai_disclosure, ok) == []
    assert rules(checks.check_ai_disclosure, "---\nai_assistance: {used: false}\n---\n") == []
    assert rules(checks.check_ai_disclosure, "hello")
    assert rules(checks.check_ai_disclosure, "---\nai_assistance: {used: yes please}\n---\n")
    assert rules(checks.check_ai_disclosure, "---\nai_assistance: {used: true, description: ' '}\n---\n")


def test_citations_same_wording_for_invisible_and_missing():
    a, b = uuid.uuid4(), uuid.uuid4()
    md = f"X [@doc:{a}; @doc:{b}; @doc:{D}; @doc:nope]\n"
    res = {f"doc:{a}": None, f"doc:{b}": None, f"doc:{D}": {"kind": "doc"}}
    out = rules(checks.check_citations, md, P, res)
    assert out[0].replace(str(a), "K") == out[1].replace(str(b), "K")
    assert "does not resolve to a library document you can access" in out[0] and len(out) == 3
    assert "malformed" in out[2]
