"""Device-facing print/routing config snapshot (Phase 1).

One call the device caches to route and print offline: stations, printers (with
addresses), the category map, per-item overrides, and this device's receipt
printer. Carries a strong ETag `"cfg-{branch}-{version}"` so the device can poll
cheaply with `If-None-Match` and get a 304 when nothing changed.

Read-only and device-session scoped. The manager AUTHORS this config under
`/v1/branch/printing/*` (see app/api/v1/branch/printing.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.pos import PosSession, get_pos_session
from app.services.printing import PrintingConfigService

router = APIRouter()


def _if_none_match_hit(header_value: str | None, etag: str) -> bool:
    """RFC 7232 If-None-Match: `*` matches anything; otherwise any listed tag that
    equals ours (comparing weakly — a `W/` prefix does not change identity here)."""
    if not header_value:
        return False
    tokens = [t.strip() for t in header_value.split(",")]
    if "*" in tokens:
        return True

    def _strip_weak(tag: str) -> str:
        return tag[2:] if tag.startswith("W/") else tag

    return any(_strip_weak(t) == _strip_weak(etag) for t in tokens)


@router.get("/config")
def get_config(
    response: Response,
    if_none_match: str | None = Header(default=None),
    session: PosSession = Depends(get_pos_session),
    db: Session = Depends(get_db),
):
    payload, version = PrintingConfigService.config_snapshot(
        db,
        restaurant_id=session.user.restaurant_id,
        branch_id=session.branch_id,
        device_id=session.device.id,
    )
    etag = f'"cfg-{session.branch_id}-{version}"'
    if _if_none_match_hit(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return ok(payload)
