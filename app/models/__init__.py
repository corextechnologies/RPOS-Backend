"""Import all models so Base.metadata is fully populated for Alembic."""
from app.models.enums import UserRole  # noqa: F401
from app.models.restaurant import Restaurant  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.location import Branch, Kitchen, Warehouse  # noqa: F401
from app.models.product import Product, ProductKind  # noqa: F401
from app.models.refresh_token import RefreshToken  # noqa: F401
from app.models.request_enums import RequestType, LocationType  # noqa: F401
from app.models.request import (  # noqa: F401
    Request,
    RequestAllocation,
    RequestLineItem,
)
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.notification import Notification  # noqa: F401
from app.models.invoice import Invoice  # noqa: F401
from app.models.inventory import (  # noqa: F401
    InventoryItem,
    StockMovement,
    StockMovementType,
    WasteReason,
)
from app.models.stock_count import StockCount, StockCountLine  # noqa: F401
from app.models.reorder_level import ReorderLevel  # noqa: F401
from app.models.sales import SalesRecord  # noqa: F401
from app.models.customer import Customer  # noqa: F401
from app.models.menu_enums import (  # noqa: F401
    MenuVersionStatus,
    OrderStatus,
    VoidState,
)
from app.models.menu import (  # noqa: F401
    ComboComponent,
    ItemAvailability,
    MenuItem,
    MenuItemModifierGroup,
    MenuVersion,
    ModifierGroup,
    ModifierOption,
)
from app.models.order import Order, OrderLine, OrderLineModifier  # noqa: F401
from app.models.production_enums import ProductionLineRole  # noqa: F401
from app.models.production import ProductionRun, ProductionRunLine  # noqa: F401
from app.models.pos import (  # noqa: F401
    Device,
    DeviceProfile,
    DeviceStatus,
    IdempotencyKey,
    IdempotencyStatus,
    TaxProfile,
)
from app.models.payment import (  # noqa: F401
    CashMovement,
    CashMovementType,
    DiscountRule,
    DiscountScope,
    DiscountType,
    Payment,
    PaymentStatus,
    ReasonCode,
    Refund,
    Shift,
    ShiftStatus,
)
from app.models.recipe import Recipe, RecipeComponent, StockUnit  # noqa: F401
