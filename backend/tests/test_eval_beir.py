from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from roracle.eval import beir
from roracle.eval.fakes import HashEmbedder
from roracle.models import Document, User
from roracle.models_eval import EvalQuery, EvalSet

DIR = Path(__file__).parent / "fixtures" / "beir_mini"


class ES:
    def __init__(self, exists=True):
        self.indices = SimpleNamespace(exists=lambda index: exists)
        self.bulk = []


@pytest.fixture
def owner(db_session):
    u = User(sub="o", username="owen")
    db_session.add(u)
    db_session.flush()
    return u


def test_import_creates_acl_documents_and_gold(db_session, settings, owner, monkeypatch):
    indexed = []
    monkeypatch.setattr(
        beir.search_index,
        "index_chunks",
        lambda es, ver, doc, chunks, vecs, acl, model, **k: indexed.append((doc, acl, chunks, k)),
    )
    out = beir.import_beir(
        db_session, ES(), HashEmbedder(), settings, DIR, set_name="sf", owner="owen", visibility="private"
    )
    assert out == {"documents": 3, "queries": 1}
    docs = db_session.scalars(select(Document).where(Document.owner_id == owner.id)).all()
    assert {d.visibility for d in docs} == {"private"} and {d.title for d in docs} == {
        "Alpha study",
        "Beta study",
        "d3",
    }
    assert len(indexed) == 3 and all(a["visibility"] == "private" for _, a, _, _ in indexed)
    s = db_session.scalar(select(EvalSet).where(EvalSet.name == "sf"))
    q = db_session.scalar(select(EvalQuery).where(EvalQuery.set_id == s.id))
    d1 = next(d for d in docs if d.title == "Alpha study")
    assert (q.key, q.principals, q.relevant) == ("beir-q1", [owner.id], [{"doc": str(d1.id), "grade": 1}])


def test_import_refuses_bad_input(db_session, settings, owner, tmp_path):
    with pytest.raises(ValueError, match="not found"):
        beir.import_beir(db_session, ES(), None, settings, tmp_path, set_name="x", owner="owen")
    with pytest.raises(ValueError, match="no user"):
        beir.import_beir(db_session, ES(), None, settings, DIR, set_name="x", owner="nobody")
    with pytest.raises(ValueError, match="does not exist"):
        beir.import_beir(db_session, ES(False), None, settings, DIR, set_name="x", owner="owen")
