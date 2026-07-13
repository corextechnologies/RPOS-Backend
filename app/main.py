"""FastAPI application entrypoint."""
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.exceptions import register_exception_handlers

app = FastAPI(title="Restaurant OS Backend", version="0.1.0")

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
