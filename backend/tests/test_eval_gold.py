import json
import uuid
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from roracle.eval import gold
from roracle.models import User
from roracle.models_eval import EvalQuery, EvalSet

FIXTURE = Path(__file__).parent / "eval" / "fixture.yaml"


def test_yaml_and_json_parse_identically(tmp_path):
    j = tmp_path / "f.json"
    j.write_text(json.dumps(yaml.safe_load(FIXTURE.read_text())))
    assert gold.load_file(j) == gold.load_file(FIXTURE)
    assert gold.load_file(FIXTURE).queries[0].as_pat


def test_unknown_key_rejected(tmp_path):
    d = yaml.safe_load(FIXTURE.read_text())
    d["queries"][0]["bogus"] = 1
    with pytest.raises(ValidationError):
        gold.FixtureSet.model_validate(d)


@pytest.mark.parametrize("grade", [0, 4])
def test_grade_range(grade):
    with pytest.raises(ValidationError):
        gold.GoldItem(doc="a", grade=grade)


def test_dangling_refs_rejected():
    d = yaml.safe_load(FIXTURE.read_text())
    d["queries"][0]["relevant"][0]["doc"] = "nope"
    with pytest.raises(ValidationError, match="unknown references"):
        gold.FixtureSet.model_validate(d)


def _user(db, name, admin=False, groups=()):
    u = User(id=uuid.uuid4(), sub=name, username=name, is_admin=admin, groups=list(groups))
    db.add(u)
    db.flush()
    return u


def _gs(*refs, as_pat=False):
    q = gold.GoldQuery(
        key="q",
        text="t",
        kind="fact",
        principals=[str(r) for r in refs],
        as_pat=as_pat,
        relevant=[gold.GoldItem(doc=str(uuid.uuid4()))],
    )
    return gold.GoldSet(name="s", queries=[q])


def test_admin_only_principals_rejected(db_session):
    adm = _user(db_session, "adm", admin=True)
    with pytest.raises(ValueError, match="non-admin"):
        gold.resolve_principals(db_session, _gs(adm.id))
    with pytest.raises(ValueError, match="unknown principal"):
        gold.resolve_principals(db_session, _gs(uuid.uuid4()))


def test_as_pat_yields_pat_principal(db_session):
    adm, ana = _user(db_session, "adm", admin=True), _user(db_session, "ana", groups=["/eng"])
    ps = gold.resolve_principals(db_session, _gs(adm.id, ana.id, as_pat=True))
    assert ps[str(ana.id)].via == "jwt" and ps[str(ana.id) + ":pat"].via == "pat"
    assert ps[str(ana.id) + ":pat"].scopes == {"read"} and ps[str(ana.id)].groups == ("/eng",)
    assert ps[str(adm.id) + ":pat"].is_admin


def test_load_db_active_only(db_session):
    s = EvalSet(name="s")
    db_session.add(s)
    db_session.flush()
    for key, status in (("a", "active"), ("b", "draft")):
        db_session.add(
            EvalQuery(
                set_id=s.id,
                key=key,
                text="t",
                kind="fact",
                principals=[uuid.uuid4()],
                relevant=[{"doc": str(uuid.uuid4()), "grade": 2}],
                status=status,
            )
        )
    db_session.flush()
    gs = gold.load_db(db_session, s.id)
    assert [q.key for q in gs.queries] == ["a"] and gs.queries[0].relevant[0].grade == 2


def test_load_db_normalizes_doc_ids(db_session):
    s = EvalSet(name="s")
    db_session.add(s)
    db_session.flush()
    d = uuid.uuid4()
    db_session.add(
        EvalQuery(
            set_id=s.id,
            key="a",
            text="t",
            kind="fact",
            principals=[uuid.uuid4()],
            relevant=[{"doc": "{" + str(d).upper() + "}"}],
        )
    )
    db_session.flush()
    assert gold.load_db(db_session, s.id).queries[0].relevant[0].doc == str(d)
