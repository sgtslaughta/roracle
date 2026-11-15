import uuid

import pytest
from sqlalchemy import select

from roracle.access import org_ancestors, visible_documents
from roracle.auth import Principal
from roracle.models import Document, DocumentShare, User


def _p(user, groups=(), admin=False):
    return Principal(
        user_id=user.id, sub=user.sub, username=user.username, groups=tuple(groups), is_admin=admin
    )


@pytest.fixture
def other(db_session):
    u = User(id=uuid.uuid4(), sub="other", username="other")
    db_session.add(u)
    db_session.flush()
    return u


def _doc(db, owner, **kw):
    d = Document(
        owner_id=owner.id,
        title="t",
        org_unit=kw.pop("org_unit", "/"),
        visibility=kw.pop("visibility", "private"),
        **kw,
    )
    db.add(d)
    db.flush()
    return d


def _visible(db, p):
    return set(db.scalars(select(Document.id).where(visible_documents(p))))


def test_private_hidden_from_others(db_session, user, other):
    d = _doc(db_session, other)
    assert d.id not in _visible(db_session, _p(user))


def test_owner_sees_own(db_session, user):
    d = _doc(db_session, user)
    assert d.id in _visible(db_session, _p(user))


def test_everyone(db_session, user, other):
    d = _doc(db_session, other, visibility="everyone")
    assert d.id in _visible(db_session, _p(user))


def test_user_share(db_session, user, other):
    d = _doc(db_session, other, visibility="shared")
    db_session.add(DocumentShare(document_id=d.id, principal_kind="user", principal=str(user.id)))
    db_session.flush()
    assert d.id in _visible(db_session, _p(user))


def test_group_share(db_session, user, other):
    d = _doc(db_session, other, visibility="shared")
    db_session.add(DocumentShare(document_id=d.id, principal_kind="group", principal="/G"))
    db_session.flush()
    assert d.id in _visible(db_session, _p(user, ["/X", "/G"]))
    assert d.id not in _visible(db_session, _p(user, ["/X"]))
    assert d.id not in _visible(db_session, _p(user))


def test_org_unit_ancestor_rule(db_session, user, other):
    d = _doc(db_session, other, visibility="org_unit", org_unit="/Math")
    assert d.id in _visible(db_session, _p(user, ["/Math"]))
    assert d.id in _visible(db_session, _p(user, ["/Math/Algebra"]))
    assert d.id not in _visible(db_session, _p(user, ["/Mathematics"]))
    assert d.id not in _visible(db_session, _p(user))


def test_org_unit_ignored_unless_visibility_org_unit(db_session, user, other):
    d = _doc(db_session, other, visibility="private", org_unit="/Math")
    assert d.id not in _visible(db_session, _p(user, ["/Math"]))


def test_deleted_never_visible(db_session, user):
    from sqlalchemy import func

    d = _doc(db_session, user, visibility="everyone", deleted_at=func.now())
    assert d.id not in _visible(db_session, _p(user))
    assert d.id not in _visible(db_session, _p(user, admin=True))


def test_admin_sees_all(db_session, user, other):
    d = _doc(db_session, other)
    assert d.id in _visible(db_session, _p(user, admin=True))


@pytest.mark.parametrize("unit,group", [("/Math_Dept", "/MathXDept/Sub"), ("/Math%", "/Mathematics/Sub")])
def test_org_unit_wildcards_are_literal(db_session, user, other, unit, group):
    d = _doc(db_session, other, visibility="org_unit", org_unit=unit)
    assert d.id not in _visible(db_session, _p(user, [group]))
    assert d.id in _visible(db_session, _p(user, [unit + "/Sub"]))


def test_root_org_unit_visible_to_groupless(db_session, user, other):
    d = _doc(db_session, other, visibility="org_unit", org_unit="/")
    assert d.id in _visible(db_session, _p(user))


def test_org_ancestors():
    assert org_ancestors([]) == ["/"]
    assert org_ancestors(["/A/B/C", "/A/D/", "/A/B"]) == ["/", "/A", "/A/B", "/A/B/C", "/A/D"]
