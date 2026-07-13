"""Standard success/response helpers and pagination.

Success envelope: {"data": <payload>, "meta": {...optional...}}
Error envelope (see exceptions.py): {"error": {"code", "message", "details?"}}
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: dict[str, Any] | None = None


def ok(data: Any, meta: dict[str, Any] | None = None) -> dict:
    body: dict[str, Any] = {"data": data}
    if meta is not None:
        body["meta"] = meta
    return body


class PageParams(BaseModel):
    page: int = 1
    page_size: int = 50

    @property
    def offset(self) -> int:
        return (max(self.page, 1) - 1) * self.page_size

    @property
    def limit(self) -> int:
        return max(1, min(self.page_size, 200))
