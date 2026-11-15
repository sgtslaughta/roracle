"""Document visibility rule. P3 mirrors this for Elastic filters."""

from sqlalchemy import and_, exists, false, func, literal, or_, select, true
from sqlalchemy.sql.elements import ColumnElement

from roracle.auth import Principal
from roracle.models import Document, DocumentShare


def _share(kind: str, principals: list[str]) -> ColumnElement[bool]:
    return exists(
        select(1).where(
            DocumentShare.document_id == Document.id,
            DocumentShare.principal_kind == kind,
            DocumentShare.principal.in_(principals),
        )
    )


def visible_documents(p: Principal) -> ColumnElement[bool]:
    live = Document.deleted_at.is_(None)
    if p.is_admin:
        return and_(live, true())
    groups = list(p.groups)
    prefix = func.rtrim(Document.org_unit, "/") + "/"
    org = (
        and_(
            Document.visibility == "org_unit",
            or_(
                *[
                    or_(Document.org_unit == g, literal(g).startswith(prefix, autoescape=False))
                    for g in groups
                ]
            ),
        )
        if groups
        else false()
    )
    return and_(
        live,
        or_(
            Document.visibility == "everyone",
            Document.owner_id == p.user_id,
            _share("user", [str(p.user_id)]),
            _share("group", groups) if groups else false(),
            org,
        ),
    )
