"""
Multi-tenant query helper.

Every read query for organization-scoped models should go through TenantScope so
organization_id filtering is applied by default instead of manually on each query.
"""

import uuid
from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import InstrumentedAttribute, Session

_T = TypeVar("_T")


def _model_from_entity(entity: Any) -> type | None:
    if isinstance(entity, InstrumentedAttribute):
        return entity.class_
    if isinstance(entity, type) and hasattr(entity, "__tablename__"):
        return entity
    return None


def _scoped_models(*entities: Any) -> list[type]:
    models: list[type] = []
    seen: set[type] = set()
    for entity in entities:
        model = _model_from_entity(entity)
        if model is None or model in seen:
            continue
        seen.add(model)
        if hasattr(model, "organization_id"):
            models.append(model)
    return models


class TenantScope:
    """
    Applies organization_id to queries for tenant-scoped models.

    Usage:
        tenant = TenantScope(db, organization_id)
        branch = tenant.get_one_or_404(Branch, branch_id, "Branch not found...")
        products = tenant.scalars(
            tenant.select(Product).where(Product.is_active.is_(True))
        ).all()
    """

    def __init__(self, db: Session, organization_id: uuid.UUID) -> None:
        self.db = db
        self.organization_id = organization_id

    def where(self, model: type[_T]) -> Any:
        """Organization filter clause for manual query composition."""
        self._assert_scoped(model)
        return model.organization_id == self.organization_id

    def select(self, *entities: Any) -> Select[Any]:
        """Build a SELECT with organization_id applied to all scoped models involved."""
        stmt = select(*entities)
        for model in _scoped_models(*entities):
            stmt = stmt.where(model.organization_id == self.organization_id)
        return stmt

    def select_from(self, model: type[_T], *columns: Any) -> Select[Any]:
        """Build a SELECT FROM model with organization_id applied (aggregates/group by)."""
        self._assert_scoped(model)
        projection = columns if columns else (model,)
        return select(*projection).select_from(model).where(
            model.organization_id == self.organization_id
        )

    def get_one(self, model: type[_T], entity_id: uuid.UUID) -> _T | None:
        """Fetch one row by primary key within the current organization."""
        return self.db.scalar(self.select(model).where(model.id == entity_id))

    def get_one_or_404(
        self,
        model: type[_T],
        entity_id: uuid.UUID,
        detail: str,
        *,
        status_code: int = status.HTTP_404_NOT_FOUND,
    ) -> _T:
        entity = self.get_one(model, entity_id)
        if entity is None:
            raise HTTPException(status_code=status_code, detail=detail)
        return entity

    def scalar(self, statement: Select[Any]) -> Any:
        return self.db.scalar(statement)

    def scalars(self, statement: Select[Any]) -> Any:
        return self.db.scalars(statement)

    def execute(self, statement: Select[Any]) -> Any:
        return self.db.execute(statement)

    @staticmethod
    def _assert_scoped(model: type) -> None:
        if not hasattr(model, "organization_id"):
            raise TypeError(
                f"{model.__name__} is not organization-scoped; "
                "use db directly for global/master-data models"
            )
