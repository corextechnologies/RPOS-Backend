"""FastAPI application entrypoint."""
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import _error_body, register_exception_handlers

logger = logging.getLogger("app")

app = FastAPI(title="Restaurant OS Backend", version="0.1.0")


# Last-resort boundary for genuinely unhandled errors.
#
# It MUST be registered *before* the CORS middleware below, so CORS stays the
# outermost layer and stamps Access-Control-Allow-Origin onto the 500. Without
# it, an unhandled exception is turned into a 500 by Starlette's error layer,
# which sits ABOVE CORS — so the response carries no CORS header and the browser
# reports an opaque `net::ERR_FAILED`, hiding the real error entirely.
#
# Expected errors (AppError / HTTPException / validation) are handled deeper in
# the stack and never reach here. This fires only on a true crash: it logs the
# traceback (so the cause shows up in the platform's runtime logs) and returns
# the standard error envelope the frontend already understands.
@app.middleware("http")
async def _catch_unhandled(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:  # last-resort: anything reaching here was genuinely unhandled
        logger.exception(
            "Unhandled error on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "Something went wrong on our end."),
        )


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

# No /uploads mount: images live in Cloudflare R2, not on local disk. Serving from
# disk was the source of two faults — URLs carried whichever host was live at
# upload time (so every host change broke every image), and serverless platforms
# wipe the disk entirely. See app/services/storage.py.


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
