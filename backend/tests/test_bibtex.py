import pytest
from api_seed import seed

from roracle.bibtex import escape
from roracle.models import Collection, CollectionItem


def test_escape():
    assert escape(r"a_b & c% $d #e {f} \g") == r"a\_b \& c\% \$d \#e \{f\} \textbackslash{}g"


@pytest.fixture
def docs(db_session, ana, bob):
    a, _ = seed(db_session, ana, title="Spectral & Graphs", authors=("Ana Müller", "Bo Lee"), year=2021)
    b, _ = seed(db_session, ana, title="Spectral Bounds", authors=("Müller, Ana",), year=2021)
    hidden, _ = seed(db_session, bob, title="Secret", visibility="private")
    parsed, _ = seed(db_session, bob, title="Early", stage="parsed")
    own_parsed, _ = seed(db_session, ana, title="Mine", stage="parsed", authors=(), year=None)
    db_session.commit()
    return a, b, hidden, parsed, own_parsed


def get(client, h, *ids, collection=None):
    q = [("doc_id", str(i)) for i in ids] + ([("collection", collection)] if collection else [])
    return client.get("/export/bibtex", params=q, headers=h)


def test_export_keys_escape_and_omit(client, as_ana, docs):
    a, b, hidden, parsed, own = docs
    r = get(client, as_ana, a.id, b.id, hidden.id, parsed.id, own.id)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-bibtex")
    t = r.text
    assert "@article{muller2021spectrala," in t and "@article{muller2021spectralb," in t
    assert "Spectral \\& Graphs" in t and "Ana Müller and Bo Lee" in t
    assert "anonndmine" in t and "Secret" not in t and "Early" not in t


def test_collection_and_validation(client, as_ana, as_bob, ana, docs, db_session):
    c = Collection(owner_id=ana, name="c")
    db_session.add(c)
    db_session.flush()
    db_session.add(CollectionItem(collection_id=c.id, document_id=docs[0].id))
    db_session.commit()
    assert get(client, as_ana, collection=str(c.id)).text.count("@") == 1
    assert get(client, as_bob, collection=str(c.id)).text == ""
    assert client.get("/export/bibtex", headers=as_ana).status_code == 422
    import uuid

    assert get(client, as_ana, *[uuid.uuid4() for _ in range(201)]).status_code == 422
