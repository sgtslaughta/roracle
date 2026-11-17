import pytest

from roracle.identifiers import extract_arxiv_stamp, extract_ids, normalize


@pytest.mark.parametrize(
    ("scheme", "raw", "want"),
    [
        ("doi", "https://doi.org/10.1000/ABC.1", "10.1000/abc.1"),
        ("doi", "http://dx.doi.org/10.1000/abc.1.", "10.1000/abc.1"),
        ("doi", "DOI: 10.1000/abc.1)", "10.1000/abc.1"),
        ("doi", "11.1000/abc", None),
        ("arxiv", "arXiv:1706.03762v5", "1706.03762"),
        ("arxiv", "https://arxiv.org/pdf/1706.03762v2.pdf", "1706.03762"),
        ("arxiv", "1706.03762", "1706.03762"),
        ("arxiv", "arXiv:Math.GT/0309136v2", "math/0309136"),
        ("arxiv", "hep-th/9901001", "hep-th/9901001"),
        ("arxiv", "17.03762", None),
        ("pmid", "PMID: 0123", "123"),
        ("pmid", "1234567890", None),
        ("pmid", "PMID: 0", None),
        ("pmid", "000", None),
        ("doi", "10.1000/x y", None),
        ("doi", "10.1002/(SICI)1097-4571(199806)", "10.1002/(sici)1097-4571(199806)"),
        ("doi", "10.1000/abc(1))", "10.1000/abc(1)"),
        ("isbn", "9780306404000", None),
        ("pmcid", "pmc5834102", "PMC5834102"),
        ("pmcid", "PMC12", None),
        ("isbn", "ISBN 0-306-40615-2", "9780306406157"),
        ("isbn", "978-0-306-40615-7", "9780306406157"),
        ("isbn", "0-306-40615-3", None),
        ("isbn", "978-0-306-40615-8", None),
        ("isbn", "0-8044-2957-X", "9780804429573"),
        ("acl", "https://aclanthology.org/2020.acl-main.1.pdf", "2020.acl-main.1"),
        ("acl", "p19-1001", "P19-1001"),
        ("acl", "nonsense", None),
        ("openreview", "https://openreview.net/forum?id=AbC_12-x", "AbC_12-x"),
        ("issn", "0378-5955", "0378-5955"),
        ("eissn", "ISSN 2049-3630", "2049-3630"),
        ("issn", "1050-124x", "1050-124X"),
        ("issn", "0378-5954", None),
        ("nope", "x", None),
        ("doi", None, None),
    ],
)
def test_normalize(scheme, raw, want):
    assert normalize(scheme, raw) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Vaswani et al. arXiv preprint arXiv:1706.03762v5, 2017.", {"arxiv": "1706.03762"}),
        ("see https://arxiv.org/abs/2611.20773v1 for details", {"arxiv": "2611.20773"}),
        ("CoRR, abs/1810.04805", {"arxiv": "1810.04805"}),
        ("arXiv:hep-th/9901001v3", {"arxiv": "hep-th/9901001"}),
        ("pages 2611.20773 and 12345678", {}),
        ("doi:10.1000/abc.1.", {"doi": "10.1000/abc.1"}),
        (
            "https://doi.org/10.48550/arXiv.1706.03762",
            {"doi": "10.48550/arxiv.1706.03762", "arxiv": "1706.03762"},
        ),
        (
            "10.18653/v1/N19-1423.",
            {"doi": "10.18653/v1/n19-1423", "acl": "N19-1423"},
        ),
        ("PMID: 12345678", {"pmid": "12345678"}),
        ("https://pubmed.ncbi.nlm.nih.gov/31452104/", {"pmid": "31452104"}),
        ("id 12345678 alone", {}),
        ("PMC5834102.", {"pmcid": "PMC5834102"}),
        ("ISBN: 978-0-306-40615-7 2020", {"isbn": "9780306406157"}),
        ("ISBN 0-306-40615-2 2020", {"isbn": "9780306406157"}),
        ("ISBN 0-306-40615-9", {}),
        ("12345678 and 9780306406157 bare", {}),
        ("https://aclanthology.org/2020.acl-main.1/", {"acl": "2020.acl-main.1"}),
        ("https://aclanthology.org/P19-1001.pdf", {"acl": "P19-1001"}),
        ("https://openreview.net/pdf?id=Hk4_qw5xe", {"openreview": "Hk4_qw5xe"}),
        ("ISSN 0378-5955; eISSN: 2049-3630", {"issn": "0378-5955", "eissn": "2049-3630"}),
        ("ISSN 0378-5954", {}),
        ("0378-5955 unlabelled", {}),
        ("", {}),
        ("arXiv preprint 1706.03762", {"arxiv": "1706.03762"}),
        ("arXiv preprint arXiv:1706.03762v2", {"arxiv": "1706.03762"}),
        ("ArXiv abs/1706.03762", {"arxiv": "1706.03762"}),
        ("arXiv:math.GT/0309136v2", {"arxiv": "math/0309136"}),
        ("https://aclweb.org/anthology/P/P19/P19-1001.pdf", {"acl": "P19-1001"}),
        ("ISBN-13: 978-0-306-40615-7", {"isbn": "9780306406157"}),
        ("ISBN 13: 978-0-306-40615-7", {"isbn": "9780306406157"}),
        ("ISBN-10: 0-306-40615-2", {"isbn": "9780306406157"}),
        ("ISBN 9780306404000", {}),
        ("doi:10.1002/(SICI)1097-4571(199806)", {"doi": "10.1002/(sici)1097-4571(199806)"}),
        ("(see doi:10.1000/abc.1)", {"doi": "10.1000/abc.1"}),
    ],
)
def test_extract(text, want):
    assert extract_ids(text) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("arXiv:2611.20773v1 [cs.MA] 20 Nov 2026", {"arxiv": "2611.20773"}),
        ("arXiv:math.GT/0309136v2 [math.GT]", {"arxiv": "math/0309136"}),
        ("built on BERT (arXiv:1810.04805) and more", {}),
        ("BERT (arXiv:1810.04805) ... arXiv:2611.20773v1 [cs.MA]", {"arxiv": "2611.20773"}),
        ("arXiv:2611.20773 [cs.MA] arXiv:1810.04805 [cs.CL]", {}),
        ("doi:10.1234/x PMID: 12345678", {}),
    ],
)
def test_arxiv_stamp(text, want):
    assert extract_arxiv_stamp(text) == want


def test_doi_hostile_input_bounded():
    import time

    from roracle.identifiers import extract_ids, normalize

    t = time.perf_counter()
    assert normalize("doi", "10.1234/" + ")" * 200_000) is None
    extract_ids("10.1234/x" + ")" * 200_000)
    assert time.perf_counter() - t < 1.0
