"""Import all models so Base.metadata is fully populated for Alembic."""
from app.models.enums import UserRole  # noqa: F401
from app.models.restaurant import Restaurant  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.location import Branch, Kitchen, Warehouse  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.refresh_token import RefreshToken  # noqa: F401
