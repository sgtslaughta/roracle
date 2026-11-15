from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from roracle import runtime
from roracle.api.routes.admin_deps import Admin
from roracle.audit import audit
from roracle.db import get_session
from roracle.models import AppSetting

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]


def _shape(base, ov: dict[str, Any]) -> dict:
    eff = runtime.view(runtime.apply(base, ov))
    return {k: {"value": eff[k], "source": "override" if k in ov else "env"} for k in runtime.KEYS}


@router.get("/admin/settings")
def get_settings(p: Admin, session: Db, request: Request) -> dict:
    return _shape(request.app.state.settings, runtime.overrides(session))


@router.put("/admin/settings")
def put_settings(body: runtime.SettingsIn, p: Admin, session: Db, request: Request) -> dict:
    base = request.app.state.settings
    old = runtime.overrides(session)
    new = dict(old)
    for k in body.model_fields_set:
        v = getattr(body, k)
        if v is None:
            new.pop(k, None)
        else:
            new[k] = v
    if "chat_default_model" in new and new["chat_default_model"] not in (
        new.get("chat_models") or base.chat_model_list
    ):
        raise HTTPException(422, "chat_default_model must be one of chat_models")
    before, after = runtime.view(runtime.apply(base, old)), runtime.view(runtime.apply(base, new))
    for k in body.model_fields_set:
        if k in new:
            session.merge(AppSetting(key=k, value=new[k], updated_at=func.now(), updated_by=p.user_id))
        elif (row := session.get(AppSetting, k)) is not None:
            session.delete(row)
    changes = {k: {"old": before[k], "new": after[k]} for k in body.model_fields_set if before[k] != after[k]}
    audit(session, p, "admin.settings_update", "/admin/settings", {"changes": changes})
    session.flush()
    return _shape(base, new)
