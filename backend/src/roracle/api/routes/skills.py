"""GET /skills/roracle-writer.zip: the agent skill bundle (authenticated)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.writing import skill

router = APIRouter()


@router.get("/skills/roracle-writer.zip")
def writer_skill(
    _: Annotated[Principal, Depends(current_principal)], session: Annotated[Session, Depends(get_session)]
) -> Response:
    return Response(
        skill.zip_bytes(session),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="roracle-writer.zip"'},
    )
