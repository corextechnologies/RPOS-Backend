"""One-off: move local uploads/ files into R2 and repoint the database at them.

Fixes the images broken by the old scheme, where a full URL containing whichever
host was live at upload time was persisted. Those hosts are gone, so the URLs
404 — but the FILES are still on disk, so nothing has to be re-uploaded by hand.

For each file: upload it to the right bucket (shrinking on the way, exactly as a
fresh upload would), then rewrite any database row whose stored URL ends with that
filename so it holds the new key instead.

DRY RUN BY DEFAULT. Nothing is written without --apply.

    ./.venv/Scripts/python.exe scripts/migrate_uploads_to_r2.py
    ./.venv/Scripts/python.exe scripts/migrate_uploads_to_r2.py --apply
"""
from __future__ import annotations

import mimetypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.menu import MenuItem  # noqa: E402
from app.models.restaurant import Restaurant  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import storage  # noqa: E402

APPLY = "--apply" in sys.argv

# folder -> (public?, max_px, quality). Mirrors the upload endpoints so a migrated
# image is byte-for-byte what a fresh upload would have produced.
FOLDERS = {
    "menu-images": (True, settings.menu_image_max_px, settings.image_webp_quality),
    "logos": (True, settings.menu_image_max_px, settings.image_webp_quality),
    "employee-images": (False, settings.staff_image_max_px, settings.image_webp_quality),
    "staff-images": (False, settings.staff_image_max_px, settings.image_webp_quality),
}

# (model, column) pairs that can hold an image reference.
TARGETS = [
    (MenuItem, "image_url"),
    (Restaurant, "logo_url"),
    (User, "image_url"),
]


def main() -> int:
    root = Path(settings.upload_dir)
    if not root.is_dir():
        print(f"No {root}/ directory — nothing to migrate.")
        return 0

    files = [p for p in root.rglob("*") if p.is_file()]
    if not files:
        print(f"No files under {root}/ — nothing to migrate.")
        return 0

    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — {len(files)} file(s) found\n")

    db = SessionLocal()
    uploaded = repointed = skipped = orphaned = 0
    try:
        # Load every row that holds an image reference once, up front.
        rows: list[tuple[object, str, str]] = []
        for model, column in TARGETS:
            for obj in db.execute(select(model)).scalars().all():
                value = getattr(obj, column, None)
                if value:
                    rows.append((obj, column, value))

        for path in sorted(files):
            folder = path.parent.name
            if folder not in FOLDERS:
                print(f"  SKIP  {path}  (unknown folder '{folder}')")
                skipped += 1
                continue

            public, max_px, quality = FOLDERS[folder]
            content_type = mimetypes.guess_type(path.name)[0]
            if content_type not in storage.ALLOWED_TYPES:
                print(f"  SKIP  {path}  (unsupported type {content_type})")
                skipped += 1
                continue

            # Which database rows point at this file? Match on the filename, since
            # the host part of the old URL is exactly what we cannot rely on.
            matches = [(o, c) for o, c, v in rows if v.endswith(path.name)]

            bucket = "public" if public else "private"

            # Nothing references this file — an upload that was never saved, or an
            # image since replaced. Copying it to R2 would just pay to store
            # something unreachable, so leave it on disk.
            if not matches:
                orphaned += 1
                print(f"  ORPHAN {path.name}  (no database row — not uploaded)")
                continue

            if not APPLY:
                print(
                    f"  {path.name}  ->  {folder}/ [{bucket}]  ({len(matches)} row(s))"
                )
                uploaded += 1
                repointed += len(matches)
                continue

            key = storage.upload(
                path.read_bytes(),
                content_type,
                folder=folder,
                public=public,
                max_px=max_px,
                quality=quality,
            )
            uploaded += 1
            print(f"  uploaded  {path.name}  ->  {key} [{bucket}]")

            for obj, column in matches:
                setattr(obj, column, key)
                repointed += 1
                print(f"            repointed {type(obj).__name__}#{obj.id}.{column}")

        if APPLY:
            db.commit()
            print("\nCommitted.")
        else:
            print("\nNo changes written. Re-run with --apply to perform the migration.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(
        f"\nSummary: {uploaded} uploaded, {repointed} row(s) repointed, "
        f"{skipped} skipped, {orphaned} orphan file(s)."
    )
    if orphaned:
        print(
            "Orphan files were left on disk, not uploaded — nothing references them "
            "(uploads that were never saved, or images since replaced)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
