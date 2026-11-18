"""Per-user "Get started" checklist state (see roracle.onboarding)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from roracle import onboarding
from roracle.auth import Principal, current_principal, get_app_settings, require_human
from roracle.config import Settings
from roracle.db import get_session

router = APIRouter()
Session_ = Annotated[Session, Depends(get_session)]
Who = Annotated[Principal, Depends(current_principal)]
Cfg = Annotated[Settings, Depends(get_app_settings)]


class Body(BaseModel):
    dismissed: bool


def _out(session: Session, p: Principal, settings: Settings) -> dict:
    return {**onboarding.state(session, p.user_id), "pats_enabled": settings.pats_enabled}


@router.get("/me/onboarding")
def get_onboarding(p: Who, session: Session_, settings: Cfg) -> dict:
    return _out(session, p, settings)


@router.put("/me/onboarding")
def put_onboarding(body: Body, p: Annotated[Principal, Depends(require_human)], session: Session_, settings: Cfg) -> dict:
    onboarding.set_dismissed(session, p.user_id, body.dismissed)
    session.commit()
    return _out(session, p, settings)
