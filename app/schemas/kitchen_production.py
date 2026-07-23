"""Kitchen product catalogue, recipes and recipe-driven production."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.recipe import StockUnit


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
    #: Whole units of the component's stock_unit (EACH / GRAM / ML), so
    #: "30g of sauce" is simply 30.
    quantity: int = Field(gt=0)
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
    quantity: int
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
    note: str | None = Field(default=None, max_length=500)
