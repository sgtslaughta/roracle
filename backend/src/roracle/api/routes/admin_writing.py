from importlib import resources
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from roracle.api.routes.admin_deps import Admin
from roracle.audit import audit
from roracle.db import get_session
from roracle.models_writing import WritingProfile
from roracle.writing import profiles

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]


class ProfileIn(BaseModel):
    yaml: str


@router.get("/admin/writing-profiles")
def list_profiles(p: Admin, session: Db) -> dict:
    return {"items": profiles.list_effective(session)}


@router.get("/admin/writing-profiles/{name}")
def get_profile(name: str, p: Admin, session: Db) -> dict:
    rows = session.scalars(
        select(WritingProfile).where(WritingProfile.name == name).order_by(WritingProfile.version.desc())
    ).all()
    shipped = name in profiles.builtin()
    if not rows and not shipped:
        raise HTTPException(404, "not found")
    out = [
        {"version": r.version, "yaml": r.yaml, "active": r.active, "created_at": r.created_at} for r in rows
    ]
    if shipped:
        f = resources.files(profiles.__package__).joinpath("profiles", f"{name}.yaml")
        out.append({"version": 0, "yaml": f.read_text(encoding="utf-8"), "active": True, "created_at": None})
    return {"name": name, "versions": out}


@router.post("/admin/writing-profiles", status_code=201)
def create_profile(body: ProfileIn, p: Admin, session: Db) -> dict:
    try:
        prof = profiles.parse(body.yaml)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('writing_profiles', 0))"))
    version = (
        session.scalar(select(func.max(WritingProfile.version)).where(WritingProfile.name == prof.name)) or 0
    ) + 1  # builtin counts as 0
    session.add(
        WritingProfile(
            name=prof.name,
            version=version,
            yaml=body.yaml,
            spec=prof.model_dump(mode="json"),
            created_by=p.user_id,
        )
    )
    audit(session, p, "admin.writing_profile", prof.name, {"name": prof.name, "version": version})
    session.flush()
    return {"name": prof.name, "version": version}


@router.delete("/admin/writing-profiles/{name}")
def deactivate_profile(name: str, p: Admin, session: Db) -> dict:
    n = session.execute(
        update(WritingProfile).where(WritingProfile.name == name, WritingProfile.active).values(active=False)
    ).rowcount
    if not n:
        raise HTTPException(404, "not found")
    audit(session, p, "admin.writing_profile", name, {"name": name, "deactivated": True})
    session.flush()
    return {"name": name, "deactivated": n}
