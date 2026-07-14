"""Shared request workflow service."""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.deps.request_scoping import user_can_view_request, visible_requests
from app.models.location import Branch, Kitchen, Warehouse
from app.models.product import Product
from app.models.request import Request, RequestLineItem
from app.models.request_enums import (
    INITIAL_STATUS,
    KitchenToWarehouseStatus,
    LocationType,
    RequestType,
)
from app.models.user import User
from app.schemas.request import (
    RequestCreate,
    RequestListFilters,
    RequestOut,
    RequestTransition,
)
from app.services.audit import AuditService
from app.services.inventory import InventoryService
from app.services.notifications import NotificationService
from app.services.transitions import (
    FULL_APPROVAL_STATUSES,
    PARTIAL_APPROVAL_STATUSES,
    assert_role_can_create,
    assert_role_can_transition,
    supports_partial_approval,
    validate_transition,
)

_LOCATION_MODELS = {
    LocationType.BRANCH: Branch,
    LocationType.KITCHEN: Kitchen,
    LocationType.WAREHOUSE: Warehouse,
}


def _apply_inventory_side_effects(
    db: Session,
    *,
    actor: User,
    request: Request,
    from_status: str,
    to_status: str,
) -> None:
    """Phase 6B: decrement warehouse stock when kitchen requests are dispatched."""
    if request.request_type != RequestType.KITCHEN_TO_WAREHOUSE:
        return
    if to_status != KitchenToWarehouseStatus.DISPATCHED.value:
        return
    if (
        request.target_location_type != LocationType.WAREHOUSE
        or request.target_location_id is None
    ):
        raise ConflictError(
            "Kitchen-to-warehouse request is missing a warehouse target.",
            code="missing_warehouse_target",
        )

    for line in request.line_items:
        qty = line.quantity_approved
        if qty is None:
            qty = line.quantity_requested
        if qty <= 0:
            continue
        try:
            InventoryService.apply_dispatch(
                db,
                actor=actor,
                location_type=LocationType.WAREHOUSE,
                location_id=request.target_location_id,
                product_id=line.product_id,
                quantity=qty,
                request_id=request.id,
                notes=f"Dispatch for request #{request.id}",
            )
        except NotFoundError as exc:
            raise ConflictError(
                "Insufficient stock for this operation.",
                code="insufficient_stock",
            ) from exc


class RequestService:
    @staticmethod
    def create_request(db: Session, actor: User, body: RequestCreate) -> Request:
        assert_role_can_create(actor.role, body.request_type)

        if body.request_type == RequestType.ADMIN_TO_SUPERADMIN_PLAN:
            if actor.restaurant_id is None:
                raise ForbiddenError("Admin must belong to a restaurant.")
            restaurant_id = actor.restaurant_id
        else:
            if actor.restaurant_id is None:
                raise ForbiddenError("You must belong to a restaurant.")
            restaurant_id = actor.restaurant_id

        RequestService._validate_locations(db, restaurant_id, body)
        product_ids = [line.product_id for line in body.lines]
        if product_ids:
            RequestService._validate_products(db, restaurant_id, product_ids)

        request = Request(
            restaurant_id=restaurant_id,
            request_type=body.request_type,
            status=INITIAL_STATUS[body.request_type],
            requester_id=actor.id,
            source_location_type=body.source_location_type,
            source_location_id=body.source_location_id,
            target_location_type=body.target_location_type,
            target_location_id=body.target_location_id,
            notes=body.notes,
        )
        db.add(request)
        db.flush()

        for line in body.lines:
            db.add(
                RequestLineItem(
                    request_id=request.id,
                    product_id=line.product_id,
                    quantity_requested=line.quantity_requested,
                )
            )

        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="request.create",
            entity_type="request",
            entity_id=request.id,
            restaurant_id=restaurant_id,
            payload={"request_type": body.request_type.value, "status": request.status},
        )
        NotificationService.notify_request_created(db, request=request, actor=actor)
        db.commit()
        db.refresh(request)
        return RequestService._load_request(db, request.id)

    @staticmethod
    def list_requests(
        db: Session, actor: User, filters: RequestListFilters
    ) -> tuple[list[Request], int]:
        stmt = visible_requests(db, actor)
        if filters.request_type is not None:
            stmt = stmt.where(Request.request_type == filters.request_type)
        if filters.status is not None:
            stmt = stmt.where(Request.status == filters.status)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.execute(count_stmt).scalar_one()

        rows = (
            db.execute(
                stmt.options(selectinload(Request.line_items))
                .order_by(Request.id.desc())
                .offset(filters.offset)
                .limit(filters.limit)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    @staticmethod
    def get_request(db: Session, actor: User, request_id: int) -> Request:
        request = RequestService._load_request(db, request_id)
        if not user_can_view_request(db, actor, request):
            raise NotFoundError("Request not found.")
        return request

    @staticmethod
    def transition(
        db: Session, actor: User, request_id: int, body: RequestTransition
    ) -> Request:
        request = RequestService._load_request(db, request_id)
        if not user_can_view_request(db, actor, request):
            raise NotFoundError("Request not found.")

        from_status = request.status
        to_status = body.to_status

        validate_transition(request.request_type, from_status, to_status)
        assert_role_can_transition(actor.role, request.request_type, to_status)

        RequestService._apply_line_approvals(request, body, to_status)

        if body.notes:
            request.notes = body.notes
        if body.assignee_id is not None:
            request.assignee_id = body.assignee_id

        # Compare-and-set: reject stale concurrent transitions.
        result = db.execute(
            update(Request)
            .where(Request.id == request.id, Request.status == from_status)
            .values(status=to_status)
        )
        if result.rowcount != 1:
            db.rollback()
            raise ConflictError(
                "Request status has changed. Please refresh and retry.",
                code="stale_status",
            )

        db.flush()
        request.status = to_status

        _apply_inventory_side_effects(
            db,
            actor=actor,
            request=request,
            from_status=from_status,
            to_status=to_status,
        )

        AuditService.record(
            db,
            actor=actor,
            action="request.status_change",
            entity_type="request",
            entity_id=request.id,
            restaurant_id=request.restaurant_id,
            payload={"from_status": from_status, "to_status": to_status},
        )
        NotificationService.notify_request_transition(
            db,
            request=request,
            actor=actor,
            from_status=from_status,
            to_status=to_status,
        )
        db.commit()
        return RequestService._load_request(db, request.id)

    @staticmethod
    def _apply_line_approvals(
        request: Request, body: RequestTransition, to_status: str
    ) -> None:
        if not supports_partial_approval(request.request_type):
            return

        needs_approval = (
            to_status in PARTIAL_APPROVAL_STATUSES | FULL_APPROVAL_STATUSES
        )
        if not needs_approval:
            return

        approval_map = {
            item.line_item_id: item.quantity_approved
            for item in (body.line_approvals or [])
        }

        for line in request.line_items:
            if to_status in FULL_APPROVAL_STATUSES and line.id not in approval_map:
                line.quantity_approved = line.quantity_requested
            elif line.id in approval_map:
                approved = approval_map[line.id]
                if approved > line.quantity_requested:
                    raise ConflictError(
                        "Approved quantity cannot exceed requested quantity.",
                        code="invalid_approval_quantity",
                    )
                line.quantity_approved = approved
            elif to_status in PARTIAL_APPROVAL_STATUSES:
                raise ConflictError(
                    "Partial approval requires line_approvals for each line.",
                    code="missing_line_approvals",
                )

        if to_status in PARTIAL_APPROVAL_STATUSES:
            if not any(
                (line.quantity_approved or 0) < line.quantity_requested
                for line in request.line_items
            ):
                raise ConflictError(
                    "Partially approved status requires at least one line "
                    "with approved quantity less than requested.",
                    code="not_partial",
                )

    @staticmethod
    def _validate_locations(
        db: Session, restaurant_id: int, body: RequestCreate
    ) -> None:
        for loc_type, loc_id in (
            (body.source_location_type, body.source_location_id),
            (body.target_location_type, body.target_location_id),
        ):
            if loc_type is None or loc_id is None:
                continue
            model = _LOCATION_MODELS[loc_type]
            row = db.get(model, loc_id)
            if row is None or row.restaurant_id != restaurant_id:
                raise NotFoundError("Location not found in this restaurant.")

    @staticmethod
    def _validate_products(
        db: Session, restaurant_id: int, product_ids: list[int]
    ) -> None:
        rows = (
            db.execute(select(Product).where(Product.id.in_(product_ids)))
            .scalars()
            .all()
        )
        if len(rows) != len(set(product_ids)):
            raise NotFoundError("One or more products not found.")
        for product in rows:
            if product.restaurant_id != restaurant_id:
                raise NotFoundError("Product does not belong to this restaurant.")

    @staticmethod
    def _load_request(db: Session, request_id: int) -> Request:
        request = (
            db.execute(
                select(Request)
                .where(Request.id == request_id)
                .options(
                    selectinload(Request.line_items).selectinload(
                        RequestLineItem.product
                    )
                )
            )
            .scalar_one_or_none()
        )
        if request is None:
            raise NotFoundError("Request not found.")
        return request

    @staticmethod
    def to_out(request: Request) -> RequestOut:
        lines = []
        for item in request.line_items:
            lines.append(
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "product_name": item.product.name if item.product else None,
                    "quantity_requested": item.quantity_requested,
                    "quantity_approved": item.quantity_approved,
                }
            )
        data = {
            "id": request.id,
            "restaurant_id": request.restaurant_id,
            "request_type": request.request_type,
            "status": request.status,
            "requester_id": request.requester_id,
            "assignee_id": request.assignee_id,
            "source_location_type": request.source_location_type,
            "source_location_id": request.source_location_id,
            "target_location_type": request.target_location_type,
            "target_location_id": request.target_location_id,
            "notes": request.notes,
            "created_at": request.created_at,
            "updated_at": request.updated_at,
            "line_items": lines,
        }
        return RequestOut.model_validate(data)
