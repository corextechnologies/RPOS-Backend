"""Branch sub-staff provisioning routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.branch import BranchStaffCreate, BranchStaffOut, BranchStaffUpdate
from app.services.branch_users import BranchUserService

router = APIRouter(dependencies=[Depends(require_role(UserRole.BRANCH_MANAGER))])


@router.post("/users")
def create_branch_staff(
    body: BranchStaffCreate,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    result = BranchUserService.create_staff(db, current, body)
    return ok(result.model_dump(mode="json"))


@router.get("/users")
def list_branch_staff(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    rows, total = BranchUserService.list_staff(
        db, current, offset=offset, limit=page_size
    )
    data = [BranchUserService.to_out(u).model_dump(mode="json") for u in rows]
    return ok(data, meta={"total": total, "page": page, "page_size": page_size})


@router.patch("/users/{user_id}")
def update_branch_staff(
    user_id: int,
    body: BranchStaffUpdate,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    user = BranchUserService.update_staff(db, current, user_id, body)
    return ok(BranchUserService.to_out(user).model_dump(mode="json"))


@router.post("/users/{user_id}/revoke")
def revoke_branch_staff(
    user_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    user = BranchUserService.set_active(db, current, user_id, is_active=False)
    return ok(BranchUserService.to_out(user).model_dump(mode="json"))


@router.post("/users/{user_id}/restore")
def restore_branch_staff(
    user_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    user = BranchUserService.set_active(db, current, user_id, is_active=True)
    return ok(BranchUserService.to_out(user).model_dump(mode="json"))


@router.delete("/users/{user_id}")
def delete_branch_staff(
    user_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    BranchUserService.delete_staff(db, current, user_id)
    return ok({"detail": "Staff member deleted."})
