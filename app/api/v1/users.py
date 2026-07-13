"""Read endpoint that demonstrates the central visibility rule end-to-end."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import get_current_user
from app.deps.scoping import visible_users
from app.models.user import User
from app.schemas.auth import UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def list_users(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(visible_users(db, current)).scalars().all()
    return ok([UserOut.model_validate(u).model_dump() for u in rows])
