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
    BranchToAdminStatus,
    KitchenToWarehouseStatus,
    LocationType,
    RequestType,
    WarehouseToAdminStatus,
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


def _approved_quantity(line: RequestLineItem) -> int:
    qty = line.quantity_approved
    return line.quantity_requested if qty is None else qty


def _require_location(
    request: Request,
    *,
    which: str,
    expected: LocationType,
    code: str,
) -> int:
    loc_type = getattr(request, f"{which}_location_type")
    loc_id = getattr(request, f"{which}_location_id")
    if loc_type != expected or loc_id is None:
        raise ConflictError(
            f"Request #{request.id} is missing a {expected.value.lower()} "
            f"{which}.",
            code=code,
        )
    return loc_id


def _dispatch_from(
    db: Session,
    *,
    actor: User,
    request: Request,
    location_type: LocationType,
    location_id: int,
) -> None:
    for line in request.line_items:
        qty = _approved_quantity(line)
        if qty <= 0:
            continue
        # FEFO across batches, not a probe of the empty-batch bucket: PO-received
        # warehouse stock lives in named batches, so a batch-blind dispatch would
        # report insufficient_stock while the product is fully on hand. The service
        # raises insufficient_stock itself (with a product/qty breakdown) when the
        # summed on-hand across usable batches falls short.
        InventoryService.apply_dispatch_fefo(
            db,
            actor=actor,
            location_type=location_type,
            location_id=location_id,
            product_id=line.product_id,
            quantity=qty,
            request_id=request.id,
            notes=f"Dispatch for request #{request.id}",
        )


def _receive_into(
    db: Session,
    *,
    actor: User,
    request: Request,
    location_type: LocationType,
    location_id: int,
) -> None:
    for line in request.line_items:
        qty = _approved_quantity(line)
        if qty <= 0:
            continue
        InventoryService.receive_stock(
            db,
            actor=actor,
            location_type=location_type,
            location_id=location_id,
            product_id=line.product_id,
            quantity=qty,
            request_id=request.id,
            notes=f"Receipt for request #{request.id}",
        )


def _warehouse_dispatches_to_kitchen(
    db: Session, *, actor: User, request: Request, body: RequestTransition
) -> None:
    """KITCHEN_TO_WAREHOUSE -> DISPATCHED: warehouse stock leaves."""
    warehouse_id = _require_location(
        request,
        which="target",
        expected=LocationType.WAREHOUSE,
        code="missing_warehouse_target",
    )
    _dispatch_from(
        db,
        actor=actor,
        request=request,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse_id,
    )


def _kitchen_receives_from_warehouse(
    db: Session, *, actor: User, request: Request, body: RequestTransition
) -> None:
    """KITCHEN_TO_WAREHOUSE -> RECEIVED: the requesting kitchen is credited."""
    kitchen_id = _require_location(
        request,
        which="source",
        expected=LocationType.KITCHEN,
        code="missing_kitchen_source",
    )
    _receive_into(
        db,
        actor=actor,
        request=request,
        location_type=LocationType.KITCHEN,
        location_id=kitchen_id,
    )


def _warehouse_receives_po(
    db: Session, *, actor: User, request: Request, body: RequestTransition
) -> None:
    """WAREHOUSE_TO_ADMIN_PO -> RECEIVED: the ordering warehouse is credited.

    Credits `quantity_received` — what the warehouse confirmed actually arrived —
    not what was ordered. This is the only place PO stock enters the ledger, and
    it happens exactly once: REPORTED deliberately credits nothing, so goods
    can't be booked twice across a report/resolve round trip.
    """
    warehouse_id = _require_location(
        request,
        which="source",
        expected=LocationType.WAREHOUSE,
        code="missing_warehouse_source",
    )
    # Batch/expiry are transient — they belong to the stock, not the paperwork.
    # They land on InventoryItem and StockMovement, which carries request_id, so
    # the receipt stays traceable without widening RequestLineItem.
    receipts = {r.line_item_id: r for r in (body.line_receipts or [])}
    for line in request.line_items:
        qty = line.quantity_received
        if qty is None:
            qty = _approved_quantity(line)
        if qty <= 0:
            continue
        receipt = receipts.get(line.id)
        InventoryService.receive_stock(
            db,
            actor=actor,
            location_type=LocationType.WAREHOUSE,
            location_id=warehouse_id,
            product_id=line.product_id,
            quantity=qty,
            batch_code=receipt.batch_code if receipt else None,
            expiry_date=receipt.expiry_date if receipt else None,
            request_id=request.id,
            notes=f"PO receipt for request #{request.id}",
        )


def _kitchen_allocates_to_branch(
    db: Session, *, actor: User, request: Request, body: RequestTransition
) -> None:
    """BRANCH_TO_ADMIN -> ALLOCATED: the producing kitchen's stock leaves.

    The branch is credited later, on RECEIVED (Phase 5).
    """
    kitchen_id = _require_location(
        request,
        which="target",
        expected=LocationType.KITCHEN,
        code="missing_kitchen_target",
    )
    _dispatch_from(
        db,
        actor=actor,
        request=request,
        location_type=LocationType.KITCHEN,
        location_id=kitchen_id,
    )


def _branch_receives_stock(
    db: Session, *, actor: User, request: Request, body: RequestTransition
) -> None:
    """BRANCH_TO_ADMIN -> RECEIVED: the requesting branch is credited."""
    branch_id = _require_location(
        request,
        which="source",
        expected=LocationType.BRANCH,
        code="missing_branch_source",
    )
    _receive_into(
        db,
        actor=actor,
        request=request,
        location_type=LocationType.BRANCH,
        location_id=branch_id,
    )


# (request_type, to_status) -> handler. Every stock-moving transition lives here
# so a status change can never silently skip its StockMovement.
_INVENTORY_SIDE_EFFECTS = {
    (
        RequestType.KITCHEN_TO_WAREHOUSE,
        KitchenToWarehouseStatus.DISPATCHED.value,
    ): _warehouse_dispatches_to_kitchen,
    (
        RequestType.KITCHEN_TO_WAREHOUSE,
        KitchenToWarehouseStatus.RECEIVED.value,
    ): _kitchen_receives_from_warehouse,
    (
        RequestType.BRANCH_TO_ADMIN,
        BranchToAdminStatus.ALLOCATED.value,
    ): _kitchen_allocates_to_branch,
    (
        RequestType.BRANCH_TO_ADMIN,
        BranchToAdminStatus.RECEIVED.value,
    ): _branch_receives_stock,
    (
        RequestType.WAREHOUSE_TO_ADMIN_PO,
        WarehouseToAdminStatus.RECEIVED.value,
    ): _warehouse_receives_po,
}


def _apply_inventory_side_effects(
    db: Session,
    *,
    actor: User,
    request: Request,
    from_status: str,
    to_status: str,
    body: RequestTransition,
) -> None:
    handler = _INVENTORY_SIDE_EFFECTS.get((request.request_type, to_status))
    if handler is None:
        return
    handler(db, actor=actor, request=request, body=body)


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
        RequestService._apply_line_receipts(request, body, to_status)
        RequestService._apply_routing_target(db, request, body, to_status)

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
            body=body,
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
    def _apply_routing_target(
        db: Session, request: Request, body: RequestTransition, to_status: str
    ) -> None:
        """Set the request's routing target during a transition.

        Forwarding a branch request to a kitchen is the one transition where the
        target is mandatory: it decides which kitchen may see the request, who
        gets notified, and whose stock ALLOCATED later decrements.
        """
        forwarding = (
            request.request_type == RequestType.BRANCH_TO_ADMIN
            and to_status == BranchToAdminStatus.FORWARDED_TO_KITCHEN.value
        )

        if body.target_location_id is None:
            if forwarding and (
                request.target_location_type != LocationType.KITCHEN
                or request.target_location_id is None
            ):
                # The branch names the target kitchen at create time; only error
                # if no kitchen target exists at all (neither branch nor admin set
                # one). An admin may still override by supplying one in the body.
                raise ConflictError(
                    "Forwarding to a kitchen requires target_location_type="
                    "KITCHEN and target_location_id.",
                    code="missing_kitchen_target",
                )
            return

        target_type = body.target_location_type
        if forwarding and target_type != LocationType.KITCHEN:
            raise ConflictError(
                "A branch request can only be forwarded to a KITCHEN.",
                code="invalid_kitchen_target",
            )
        if target_type is None:
            raise ConflictError(
                "target_location_type is required with target_location_id.",
                code="missing_target_location_type",
            )

        model = _LOCATION_MODELS[target_type]
        row = db.get(model, body.target_location_id)
        if row is None or row.restaurant_id != request.restaurant_id:
            raise NotFoundError("Location not found in this restaurant.")

        request.target_location_type = target_type
        request.target_location_id = body.target_location_id

    @staticmethod
    def _apply_line_receipts(
        request: Request, body: RequestTransition, to_status: str
    ) -> None:
        """Record what actually arrived, for REPORTED and RECEIVED on a PO.

        REPORTED is a claim that the delivery was wrong, so it must say how —
        a report where everything matches and nothing is flagged is rejected
        rather than silently accepted as a no-op.
        """
        if request.request_type != RequestType.WAREHOUSE_TO_ADMIN_PO:
            return
        if to_status not in {
            WarehouseToAdminStatus.REPORTED.value,
            WarehouseToAdminStatus.RECEIVED.value,
        }:
            return

        if not body.line_receipts:
            raise ConflictError(
                "line_receipts is required to report or receive a purchase order.",
                code="missing_line_receipts",
            )

        line_ids = {line.id for line in request.line_items}
        receipts = {}
        for receipt in body.line_receipts:
            if receipt.line_item_id not in line_ids:
                raise NotFoundError("Line item does not belong to this request.")
            receipts[receipt.line_item_id] = receipt

        for line in request.line_items:
            receipt = receipts.get(line.id)
            if receipt is None:
                continue
            if receipt.quantity_received > _approved_quantity(line):
                raise ConflictError(
                    "Received quantity cannot exceed the approved quantity.",
                    code="invalid_received_quantity",
                )
            line.quantity_received = receipt.quantity_received
            if receipt.issue_note is not None:
                line.issue_note = receipt.issue_note

        if to_status == WarehouseToAdminStatus.REPORTED.value:
            has_issue = any(
                line.id in receipts
                and (
                    (line.quantity_received or 0) != _approved_quantity(line)
                    or line.issue_note
                )
                for line in request.line_items
            )
            if not has_issue:
                raise ConflictError(
                    "A report needs at least one line with a quantity "
                    "difference or an issue note.",
                    code="nothing_reported",
                )

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
                    "quantity_received": item.quantity_received,
                    "issue_note": item.issue_note,
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
