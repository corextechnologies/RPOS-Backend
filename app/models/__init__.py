"""Import all models so Base.metadata is fully populated for Alembic."""
from app.models.enums import UserRole  # noqa: F401
from app.models.restaurant import Restaurant  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.location import Branch, Kitchen, Warehouse  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.refresh_token import RefreshToken  # noqa: F401
from app.models.request_enums import RequestType, LocationType  # noqa: F401
from app.models.request import Request, RequestLineItem  # noqa: F401
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
