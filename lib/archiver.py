"""Archive assets in Immich via the REST API."""

import logging

from .immich_api import ImmichAPI

log = logging.getLogger(__name__)


class Archiver:
    """Marks Immich assets as archived using the API."""

    def __init__(self, api: ImmichAPI):
        self.api = api

    def archive_assets(self, asset_ids: list[str], batch_size: int = 50) -> dict:
        """Archive *asset_ids* in batches, returning a stats dict.

        Returns ``{"total": N, "archived": N, "errors": N}``.
        """
        total = len(asset_ids)
        archived = 0
        errors = 0

        for start in range(0, total, batch_size):
            batch = asset_ids[start : start + batch_size]
            batch_num = start // batch_size + 1
            try:
                self.api.archive_assets(batch)
                archived += len(batch)
                log.info(
                    "Batch %d: archived %d assets (%d/%d)",
                    batch_num,
                    len(batch),
                    archived,
                    total,
                )
            except Exception:
                errors += len(batch)
                log.exception(
                    "Batch %d: failed to archive %d assets", batch_num, len(batch)
                )

        log.info(
            "Archiving complete: %d total, %d archived, %d errors",
            total,
            archived,
            errors,
        )
        return {"total": total, "archived": archived, "errors": errors}
