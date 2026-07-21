"""FastAPI application entrypoint."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers

app = FastAPI(title="Restaurant OS Backend", version="0.1.0")

_raw_origins = settings.cors_origins.strip()
_cors_origins = (
    ["*"]
    if _raw_origins == "*"
    else [o.strip() for o in _raw_origins.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # Credentials must be allowed so the POS device_uid httpOnly cookie travels
    # cross-origin (separate frontend host). The frontend must call the API with
    # `fetch(..., {credentials: "include"})`.
    #
    # ⚠️ Production: set CORS_ORIGINS to an explicit list, not "*". A browser
    # refuses the literal wildcard together with credentials; Starlette works
    # around it by echoing the request origin, which with credentials means
    # "any origin" — fine for local dev, unsafe for production.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(api_router)

_upload_path = Path(settings.upload_dir)
_upload_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_upload_path)), name="uploads")


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
