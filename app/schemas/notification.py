from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    restaurant_id: int | None
    user_id: int
    title: str
    body: str
    entity_type: str
    entity_id: int
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}
