from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.labels.policy import Policy, admin_json

router = APIRouter()


@router.get("/labels")
def list_labels(
    p: Annotated[Principal, Depends(current_principal)], session: Annotated[Session, Depends(get_session)]
) -> dict:
    return {"items": [admin_json(x) for x in Policy.load(session).labels]}
