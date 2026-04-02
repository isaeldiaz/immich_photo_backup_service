"""Immich REST API client.

Thin wrapper around the Immich server API. Handles authentication,
pagination, and error propagation — no business logic.
"""

import logging
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)


class ImmichAPI:
    """Client for the Immich REST API."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._session = requests.Session()
        self._session.headers.update(
            {
                "x-api-key": self.api_key,
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str, **kwargs) -> requests.Response:
        resp = self._session.get(self._url(path), **kwargs)
        resp.raise_for_status()
        return resp

    def _post(self, path: str, **kwargs) -> requests.Response:
        resp = self._session.post(self._url(path), **kwargs)
        resp.raise_for_status()
        return resp

    def _put(self, path: str, **kwargs) -> requests.Response:
        resp = self._session.put(self._url(path), **kwargs)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # Server
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Return True if the server responds with pong."""
        try:
            data = self._get("/api/server/ping").json()
            return data.get("res") == "pong"
        except requests.RequestException:
            log.exception("Ping failed")
            return False

    def get_version(self) -> dict:
        """Return server version as ``{"major": N, "minor": N, "patch": N}``."""
        return self._get("/api/server/version").json()

    # ------------------------------------------------------------------
    # Admin onboarding (used in tests / first-time setup)
    # ------------------------------------------------------------------

    def admin_sign_up(self, email: str, password: str, name: str) -> dict:
        """First-time admin sign-up. Only works when no admin exists yet."""
        payload = {"email": email, "password": password, "name": name}
        return self._post("/api/auth/admin-sign-up", json=payload).json()

    def login(self, email: str, password: str) -> str:
        """Log in and return the access token."""
        payload = {"email": email, "password": password}
        data = self._post("/api/auth/login", json=payload).json()
        return data["accessToken"]

    def create_api_key(self, access_token: str, name: str = "test") -> str:
        """Create an API key using a bearer token and return the key string."""
        resp = self._session.post(
            self._url("/api/api-keys"),
            json={"name": name},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()["secret"]

    # ------------------------------------------------------------------
    # Users (admin)
    # ------------------------------------------------------------------

    def list_users(self) -> list[dict]:
        """List all users (requires admin API key)."""
        return self._get("/api/admin/users").json()

    def get_user(self, user_id: str) -> dict:
        """Get a single user by ID."""
        return self._get(f"/api/users/{user_id}").json()

    def create_user(self, email: str, password: str, name: str) -> dict:
        """Create a new user (admin endpoint)."""
        payload = {"email": email, "password": password, "name": name}
        return self._post("/api/admin/users", json=payload).json()

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    def search_assets(
        self,
        is_archived: bool | None = None,
        owner_id: str | None = None,
        page: int = 1,
        size: int = 250,
    ) -> dict:
        """Search assets via metadata.

        Returns the raw response dict which contains
        ``{"assets": {"items": [...], "nextPage": "2"}}``.
        """
        body: dict = {"page": page, "size": size}
        if is_archived is not None:
            body["isArchived"] = is_archived
        if owner_id is not None:
            body["ownerId"] = owner_id
        return self._post("/api/search/metadata", json=body).json()

    def get_all_unarchived_assets(self) -> list[dict]:
        """Paginate through all unarchived assets and return a flat list."""
        all_items: list[dict] = []
        page = 1

        while True:
            data = self.search_assets(is_archived=False, page=page)
            assets_section = data.get("assets", {})
            items = assets_section.get("items", [])
            all_items.extend(items)

            next_page = assets_section.get("nextPage")
            if not next_page:
                break
            page = int(next_page)
            log.debug("Fetching unarchived assets page %d", page)

        log.info("Retrieved %d unarchived assets", len(all_items))
        return all_items

    def get_asset(self, asset_id: str) -> dict:
        """Get a single asset by ID."""
        return self._get(f"/api/assets/{asset_id}").json()

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------

    def archive_assets(self, asset_ids: list[str]) -> bool:
        """Mark one or more assets as archived. Returns True on success."""
        payload = {"ids": asset_ids, "visibility": "archive"}
        self._put("/api/assets", json=payload)
        return True

    # ------------------------------------------------------------------
    # Libraries
    # ------------------------------------------------------------------

    def list_libraries(self) -> list[dict]:
        """Return all libraries visible to this API key."""
        return self._get("/api/libraries").json()

    def scan_library(self, library_id: str, refresh_modified_files: bool = False, refresh_all_files: bool = False) -> None:
        """Trigger a rescan of an external library.

        ``refresh_modified_files`` re-imports files whose mtime changed.
        ``refresh_all_files`` forces a full re-import of every file.
        """
        payload = {
            "refreshModifiedFiles": refresh_modified_files,
            "refreshAllFiles": refresh_all_files,
        }
        self._post(f"/api/libraries/{library_id}/scan", json=payload)

    # ------------------------------------------------------------------
    # Upload (for test setup)
    # ------------------------------------------------------------------

    def upload_asset(
        self, file_path: str | Path, device_id: str = "backup-script"
    ) -> dict:
        """Upload a file to Immich as a new asset."""
        file_path = Path(file_path)
        stat = file_path.stat()

        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        mtime = stat.st_mtime
        iso_mtime = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        iso_ctime = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()

        with open(file_path, "rb") as fh:
            files = {"assetData": (file_path.name, fh, mime_type)}
            data = {
                "deviceAssetId": f"{file_path.name}-{mtime}",
                "deviceId": device_id,
                "fileCreatedAt": iso_ctime,
                "fileModifiedAt": iso_mtime,
            }
            return self._post("/api/assets", data=data, files=files).json()
