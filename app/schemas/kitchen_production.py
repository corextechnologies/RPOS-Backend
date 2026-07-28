"""Kitchen product catalogue, recipes, recipe-driven production, and staff."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import UserRole
from app.models.recipe import StockUnit
from app.schemas.quantity import Quantity


class KitchenProductCreate(BaseModel):
    """The kitchen introduces something it MAKES.

    No `kind` field: this endpoint only ever creates a FINISHED_GOOD, exactly as
    the warehouse's product endpoint only ever creates raw materials / resale
    items. Who calls it decides what it is — a caller cannot ask for something
    else.
    """

    name: str = Field(min_length=1, max_length=255)
    sku: str | None = Field(default=None, max_length=100)
    stock_unit: StockUnit = StockUnit.EACH


class RecipeComponentIn(BaseModel):
    component_product_id: int
    #: How much of the component one yield_qty consumes, stated in `unit`.
    #: Fractional: "0.25 kg flour" is 0.25 with unit=KG.
    quantity: Quantity = Field(gt=0)
    #: The unit `quantity` is in. Omit to use the component product's stock_unit.
    #: Must equal that stock_unit or share its dimension (weight/volume) — a
    #: cross-dimension unit (grams of an EACH-stocked product) is rejected.
    unit: StockUnit | None = None
    #: Expected loss, basis points. 250 = 2.5%.
    wastage_bp: int = Field(default=0, ge=0, le=10000)


class KitchenRecipeIn(BaseModel):
    """What a finished good is made of. Owned by the kitchen — it's their craft."""

    product_id: int
    #: How many of product_id one run of this recipe makes.
    yield_qty: int = Field(default=1, gt=0)
    note: str | None = Field(default=None, max_length=500)
    components: list[RecipeComponentIn] = Field(min_length=1)


class RecipeComponentOut(BaseModel):
    component_product_id: int
    component_name: str | None = None
    quantity: Quantity
    #: How the component product is stocked (e.g. KG). Callers convert `unit` →
    #: this before comparing against on-hand. Always present when the product
    #: exists; production already loads it from Product, but the recipe read
    #: path must surface it too or the UI labels kg on-hand as grams.
    stock_unit: StockUnit
    #: The unit `quantity` is stated in (e.g. GRAM). Null/legacy = stock_unit.
    unit: StockUnit | None = None
    wastage_bp: int

    model_config = {"from_attributes": True}


class RecipeOut(BaseModel):
    id: int
    product_id: int
    product_name: str | None = None
    version: int
    is_active: bool
    yield_qty: int
    note: str | None = None
    components: list[RecipeComponentOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class KitchenProduceIn(BaseModel):
    """Make N of a finished good. The recipe decides what that consumes."""

    product_id: int
    quantity: int = Field(gt=0)
    batch_code: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None
    note: str | None = Field(default=None, max_length=500)


# --- Kitchen sub-staff schemas ---


# Field order across the staff schemas is the order the form asks for them:
# name, personal image, email, phone, address, role, CNIC front, CNIC back.


class KitchenStaffCreate(BaseModel):
    """A kitchen manager adds someone to the kitchen roster.

    These are personnel records, not system users: kitchen sub-staff cannot sign
    in, so no password is set and no credentials are emailed. `job_title` is the
    manager's own free-text label ("Head Chef") — the UI calls it Role, but it is
    NOT the system `role` and grants no access.

    Every field is required: a roster entry is only useful complete.
    """

    full_name: str = Field(min_length=1, max_length=255)
    #: Key or URL from POST /v1/uploads/staff-document (kind=personal).
    image_url: str = Field(min_length=1, max_length=1024)
    email: str = Field(min_length=1, max_length=255)
    phone_number: str = Field(min_length=1, max_length=30)
    address: str = Field(min_length=1, max_length=500)
    job_title: str = Field(min_length=1, max_length=100)
    #: Keys or URLs from POST /v1/uploads/staff-document (kind=cnic).
    cnic_front_url: str = Field(min_length=1, max_length=1024)
    cnic_back_url: str = Field(min_length=1, max_length=1024)


class KitchenStaffUpdate(BaseModel):
    """Editable kitchen sub-staff fields.

    Optional here even though the form requires them all: an omitted field means
    "unchanged", so a partial save can never blank an address or wipe a CNIC scan,
    and staff created before these fields existed remain editable.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    image_url: str | None = Field(default=None, max_length=1024)
    email: str | None = Field(default=None, min_length=1, max_length=255)
    phone_number: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=500)
    job_title: str | None = Field(default=None, max_length=100)
    cnic_front_url: str | None = Field(default=None, max_length=1024)
    cnic_back_url: str | None = Field(default=None, max_length=1024)


class KitchenStaffCreateResult(BaseModel):
    user_id: int
    full_name: str | None = None
    image_url: str | None = None
    email: str
    phone_number: str | None = None
    address: str | None = None
    job_title: str | None = None
    cnic_front_url: str | None = None
    cnic_back_url: str | None = None
    role: UserRole
    kitchen_id: int


class KitchenStaffOut(BaseModel):
    id: int
    full_name: str | None = None
    image_url: str | None = None
    email: str
    phone_number: str | None = None
    address: str | None = None
    job_title: str | None = None
    cnic_front_url: str | None = None
    cnic_back_url: str | None = None
    role: UserRole
    is_active: bool
    kitchen_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
