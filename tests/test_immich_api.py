"""Integration tests for ImmichAPI against the sandbox Immich instance."""

import time
import uuid

import pytest

from tests.helpers.image_generator import create_jpeg_with_exif, create_jpeg_without_exif

pytestmark = pytest.mark.sandbox


def test_ping(admin_api):
    assert admin_api.ping() is True


def test_get_version(admin_api):
    version = admin_api.get_version()
    assert isinstance(version, dict)
    for key in ("major", "minor", "patch"):
        assert key in version
        assert isinstance(version[key], int)


def test_list_users(admin_api):
    users = admin_api.list_users()
    assert isinstance(users, list)
    assert len(users) >= 1


def test_create_user(admin_api):
    unique = uuid.uuid4().hex[:8]
    email = f"testuser-{unique}@test.local"
    user = admin_api.create_user(email, "password123!", f"Test User {unique}")
    assert isinstance(user, dict)
    assert "id" in user
    assert "name" in user
    assert user["name"] == f"Test User {unique}"


def test_upload_and_search(admin_api, tmp_path):
    img_path = create_jpeg_with_exif(tmp_path / "upload_test.jpg")
    result = admin_api.upload_asset(img_path)
    asset_id = result["id"]

    # Give the server a moment to index the asset
    time.sleep(1)

    unarchived = admin_api.get_all_unarchived_assets()
    found_ids = [a["id"] for a in unarchived]
    assert asset_id in found_ids


def test_archive_asset(admin_api, tmp_path):
    img_path = create_jpeg_without_exif(tmp_path / "archive_test.jpg")
    result = admin_api.upload_asset(img_path)
    asset_id = result["id"]

    time.sleep(1)

    admin_api.archive_assets([asset_id])

    time.sleep(1)

    unarchived = admin_api.get_all_unarchived_assets()
    found_ids = [a["id"] for a in unarchived]
    assert asset_id not in found_ids
