"""Menu-item proposal service — the branch → admin hand-off for a new dish.

A branch manager proposes a dish (`create`); Admin reviews and either `approve`s
it (resolving/creating the FINISHED_GOOD product, setting the final price, and
marking it ready to publish onto the live menu) or `reject`s it with a reason.
A branch can `withdraw` its own still-pending proposal.

Only Admin makes a menu live — approving does NOT itself publish. It readies the
dish; the admin publishes it as a real MenuItem through the normal immutable
menu-version flow (which correctly rebuilds the whole version, combos and
modifiers included). This keeps the fragile menu-composition logic in one place.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import UserRole
from app.models.location import Branch
from app.models.menu_enums import MenuProposalStatus
from app.models.menu_proposal import MenuItemProposal
from app.models.product import Product, ProductKind
from app.models.recipe import StockUnit
from app.models.user import User
from app.pricing.money import to_decimal, to_minor
from app.schemas.menu_proposal import (
    MenuProposalApprove,
    MenuProposalCreate,
    MenuProposalOut,
    MenuProposalReject,
)
from app.services.audit import AuditService
from app.services.notifications import NotificationService
from app.services.products import ProductService

_ENTITY = "menu_item_proposal"


class MenuProposalService:
    # ---- creation ---------------------------------------------------------

    @staticmethod
    def create(
        db: Session, actor: User, branch_id: int, body: MenuProposalCreate
    ) -> MenuItemProposal:
        """A sub-kitchen chef proposes a dish: just a name and a category.

        No price and no product yet — Admin sets the price and creates the
        FINISHED_GOOD product (from the dish name) when adding it to the menu.
        `proposed_price_minor` is stored as 0 as a placeholder until then.
        """
        name = body.name.strip()
        proposal = MenuItemProposal(
            restaurant_id=actor.restaurant_id,
            branch_id=branch_id,
            status=MenuProposalStatus.PENDING,
            name=name,
            category=body.category,
            proposed_price_minor=0,  # placeholder; Admin sets the real price
            made_to_order=True,  # a sub-kitchen dish is finished to order
            product_id=None,  # created from the name on approval
            new_product_name=name,
            note=body.note,
            proposed_by_id=actor.id,
        )
        db.add(proposal)
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="menu.proposal.create",
            entity_type=_ENTITY,
            entity_id=proposal.id,
            restaurant_id=actor.restaurant_id,
            payload={"branch_id": branch_id, "name": proposal.name},
        )
        # Tell the admins there is a dish to review.
        NotificationService.notify_users(
            db,
            users=NotificationService._users_with_role(
                db, actor.restaurant_id, UserRole.ADMIN
            ),
            restaurant_id=actor.restaurant_id,
            title="New menu item proposed",
            body=f"A branch proposed '{proposal.name}' for the menu.",
            entity_type=_ENTITY,
            entity_id=proposal.id,
            exclude_user_id=actor.id,
        )
        db.commit()
        return MenuProposalService._load(db, proposal.id)

    # ---- reads ------------------------------------------------------------

    @staticmethod
    def list(
        db: Session,
        actor: User,
        *,
        status: MenuProposalStatus | None = None,
        branch_id: int | None = None,
    ) -> list[MenuItemProposal]:
        """Admin sees the whole restaurant; a branch passes its own branch_id."""
        stmt = select(MenuItemProposal).where(
            MenuItemProposal.restaurant_id == actor.restaurant_id
        )
        if branch_id is not None:
            stmt = stmt.where(MenuItemProposal.branch_id == branch_id)
        if status is not None:
            stmt = stmt.where(MenuItemProposal.status == status)
        stmt = stmt.options(selectinload(MenuItemProposal.product)).order_by(
            MenuItemProposal.id.desc()
        )
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def get(db: Session, actor: User, proposal_id: int) -> MenuItemProposal:
        proposal = db.get(MenuItemProposal, proposal_id)
        if proposal is None or proposal.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Menu proposal not found.")
        return MenuProposalService._load(db, proposal_id)

    # ---- decisions --------------------------------------------------------

    @staticmethod
    def approve(
        db: Session, admin: User, proposal_id: int, body: MenuProposalApprove
    ) -> MenuItemProposal:
        """Admin accepts a proposal: resolve/create the product, set the price."""
        proposal = MenuProposalService.get(db, admin, proposal_id)
        MenuProposalService._assert_pending(proposal)
        currency = MenuProposalService._currency(db, proposal.branch_id)

        # The chef proposes a name only, so a price is required to add it to the
        # menu (unless one was somehow already recorded).
        if body.price is not None:
            final_price = body.price
        elif proposal.proposed_price_minor > 0:
            final_price = to_decimal(proposal.proposed_price_minor, currency)
        else:
            raise ConflictError(
                "Set a price to add this dish to the menu.",
                code="price_required",
            )

        # Resolve the FINISHED_GOOD product — create it (unpriced) if the branch
        # proposed a brand-new dish. create_product commits its own row; the price
        # and status below land in the following commit.
        if proposal.product_id is None:
            product = ProductService.create_product(
                db,
                admin,
                name=proposal.new_product_name,
                sku=proposal.new_product_sku,
                kind=ProductKind.FINISHED_GOOD,
                stock_unit=proposal.new_product_stock_unit or StockUnit.EACH,
            )
            proposal.product_id = product.id
        else:
            product = db.get(Product, proposal.product_id)
            if product is None or product.restaurant_id != admin.restaurant_id:
                raise NotFoundError("Product not found.")

        # Only Admin prices; the menu item carries the authoritative POS price, but
        # set the product's selling_price too so it is never left unpriced.
        product.selling_price = final_price
        if body.category is not None:
            proposal.category = body.category
        proposal.proposed_price_minor = to_minor(final_price, currency)
        proposal.status = MenuProposalStatus.APPROVED
        proposal.decided_by_id = admin.id
        proposal.decided_at = datetime.now(timezone.utc)

        db.flush()
        AuditService.record(
            db,
            actor=admin,
            action="menu.proposal.approve",
            entity_type=_ENTITY,
            entity_id=proposal.id,
            restaurant_id=admin.restaurant_id,
            payload={
                "product_id": proposal.product_id,
                "price_minor": proposal.proposed_price_minor,
            },
        )
        MenuProposalService._notify_branch(
            db,
            proposal,
            title="Menu item approved",
            body=f"'{proposal.name}' was approved. Publish it to make it live.",
        )
        db.commit()
        return MenuProposalService._load(db, proposal.id)

    @staticmethod
    def reject(
        db: Session, admin: User, proposal_id: int, body: MenuProposalReject
    ) -> MenuItemProposal:
        proposal = MenuProposalService.get(db, admin, proposal_id)
        MenuProposalService._assert_pending(proposal)
        proposal.status = MenuProposalStatus.REJECTED
        proposal.reject_reason = body.reason
        proposal.decided_by_id = admin.id
        proposal.decided_at = datetime.now(timezone.utc)
        db.flush()
        AuditService.record(
            db,
            actor=admin,
            action="menu.proposal.reject",
            entity_type=_ENTITY,
            entity_id=proposal.id,
            restaurant_id=admin.restaurant_id,
            payload={"reason": body.reason},
        )
        MenuProposalService._notify_branch(
            db,
            proposal,
            title="Menu item rejected",
            body=f"'{proposal.name}' was not approved: {body.reason}",
        )
        db.commit()
        return MenuProposalService._load(db, proposal.id)

    @staticmethod
    def mark_published(db: Session, admin: User, ids: list[int]) -> int:
        """Stamp approved proposals as now live on the menu.

        Called by the admin surface right after it publishes the approved dishes
        onto a new menu version, so the review queue can tell "approved, publish
        next" apart from "already on the menu". Idempotent: only APPROVED, still
        unpublished, restaurant-scoped rows are touched.
        """
        if not ids:
            return 0
        rows = (
            db.execute(
                select(MenuItemProposal).where(
                    MenuItemProposal.id.in_(ids),
                    MenuItemProposal.restaurant_id == admin.restaurant_id,
                    MenuItemProposal.status == MenuProposalStatus.APPROVED,
                    MenuItemProposal.published_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        for r in rows:
            r.published_at = now
        if rows:
            db.flush()
            AuditService.record(
                db,
                actor=admin,
                action="menu.proposal.published",
                entity_type=_ENTITY,
                entity_id=rows[0].id,
                restaurant_id=admin.restaurant_id,
                payload={"count": len(rows), "ids": [r.id for r in rows]},
            )
        db.commit()
        return len(rows)

    @staticmethod
    def withdraw(
        db: Session, actor: User, branch_id: int, proposal_id: int
    ) -> None:
        """The proposing branch pulls back a still-pending proposal."""
        proposal = MenuProposalService.get(db, actor, proposal_id)
        if proposal.branch_id != branch_id:
            raise NotFoundError("Menu proposal not found.")
        MenuProposalService._assert_pending(proposal)
        AuditService.record(
            db,
            actor=actor,
            action="menu.proposal.withdraw",
            entity_type=_ENTITY,
            entity_id=proposal.id,
            restaurant_id=actor.restaurant_id,
            payload={"branch_id": branch_id},
        )
        db.delete(proposal)
        db.commit()

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _assert_pending(proposal: MenuItemProposal) -> None:
        if proposal.status is not MenuProposalStatus.PENDING:
            raise ConflictError(
                f"Proposal is {proposal.status.value}; only a PENDING proposal "
                "can be decided or withdrawn.",
                code="proposal_not_pending",
            )

    @staticmethod
    def _currency(db: Session, branch_id: int) -> str:
        branch = db.get(Branch, branch_id)
        return branch.currency if branch is not None else "PKR"

    @staticmethod
    def _notify_branch(
        db: Session, proposal: MenuItemProposal, *, title: str, body: str
    ) -> None:
        recipients = NotificationService._branch_managers_at(
            db, proposal.restaurant_id, [proposal.branch_id]
        )
        if not recipients:
            return
        NotificationService.notify_users(
            db,
            users=recipients,
            restaurant_id=proposal.restaurant_id,
            title=title,
            body=body,
            entity_type=_ENTITY,
            entity_id=proposal.id,
        )

    @staticmethod
    def _load(db: Session, proposal_id: int) -> MenuItemProposal:
        return db.execute(
            select(MenuItemProposal)
            .where(MenuItemProposal.id == proposal_id)
            .options(selectinload(MenuItemProposal.product))
        ).scalar_one()

    @staticmethod
    def to_out(proposal: MenuItemProposal) -> MenuProposalOut:
        return MenuProposalOut(
            id=proposal.id,
            restaurant_id=proposal.restaurant_id,
            branch_id=proposal.branch_id,
            status=proposal.status,
            name=proposal.name,
            category=proposal.category,
            proposed_price_minor=proposal.proposed_price_minor,
            image_url=proposal.image_url,
            description=proposal.description,
            calories=proposal.calories,
            prep_time_minutes=proposal.prep_time_minutes,
            made_to_order=proposal.made_to_order,
            product_id=proposal.product_id,
            product_name=proposal.product.name if proposal.product else None,
            new_product_name=proposal.new_product_name,
            new_product_sku=proposal.new_product_sku,
            new_product_stock_unit=proposal.new_product_stock_unit,
            note=proposal.note,
            reject_reason=proposal.reject_reason,
            proposed_by_id=proposal.proposed_by_id,
            decided_by_id=proposal.decided_by_id,
            decided_at=proposal.decided_at,
            published_at=proposal.published_at,
            created_at=proposal.created_at,
        )
