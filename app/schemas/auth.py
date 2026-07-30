from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import BranchPosition, UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str | None = None
    role: UserRole
    restaurant_id: int | None = None
    created_by_id: int | None = None
    is_active: bool
    # `role` alone cannot answer "which portal does this person belong in?" — a
    # cashier and a sub-kitchen chef are both BRANCH_STAFF. The position is what
    # separates them, and it is what the POS already derives capabilities from.
    # Null for everyone who is not branch sub-staff.
    position: BranchPosition | None = None
    # Where they work. Without these the client knows *what* someone is but not
    # *where*, so it cannot label or scope the portal it just routed them to.
    branch_id: int | None = None
    kitchen_id: int | None = None
    warehouse_id: int | None = None


class MeOut(UserOut):
    """`/auth/me` — identity plus what this user may actually do.

    Mirrors the user block the POS bootstrap already returns, so a client uses
    one shape on both the device-bound and the ordinary portal path. Capabilities
    live only here, not on UserOut: they answer "what may *I* do", which is
    meaningless on a roster listing of other people.
    """

    capabilities: list[str] = Field(default_factory=list)
