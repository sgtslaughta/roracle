from typing import Annotated

from fastapi import APIRouter, Depends

from roracle.auth import Principal, current_principal

router = APIRouter()


@router.get("/me")
def me(p: Annotated[Principal, Depends(current_principal)]) -> dict:
    return {"id": str(p.user_id), "username": p.username, "groups": list(p.groups), "is_admin": p.is_admin}
