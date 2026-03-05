"""Integration tests for Archiver against the sandbox Immich instance."""

import time

import pytest

from lib.archiver import Archiver
from tests.helpers.image_generator import create_jpeg_with_exif

pytestmark = pytest.mark.sandbox


@pytest.fixture
def archiver(admin_api):
    return Archiver(admin_api)


def _upload_images(admin_api, tmp_path, count):
    """Upload *count* unique images and return their asset IDs."""
    asset_ids = []
    for i in range(count):
        img_path = create_jpeg_with_exif(
            tmp_path / f"archiver_test_{i}.jpg",
            color=(i * 40 % 256, 100, 200),
        )
        result = admin_api.upload_asset(img_path)
        asset_ids.append(result["id"])
    time.sleep(1)
    return asset_ids


def test_archive_empty_list(archiver):
    stats = archiver.archive_assets([])
    assert stats == {"total": 0, "archived": 0, "errors": 0}


def test_archive_batch(admin_api, archiver, tmp_path):
    asset_ids = _upload_images(admin_api, tmp_path, 3)

    stats = archiver.archive_assets(asset_ids)
    assert stats["total"] == 3
    assert stats["archived"] == 3
    assert stats["errors"] == 0

    # Confirm they no longer appear as unarchived
    time.sleep(1)
    unarchived = admin_api.get_all_unarchived_assets()
    unarchived_ids = {a["id"] for a in unarchived}
    for aid in asset_ids:
        assert aid not in unarchived_ids


def test_archive_batching(admin_api, archiver, tmp_path):
    asset_ids = _upload_images(admin_api, tmp_path, 5)

    stats = archiver.archive_assets(asset_ids, batch_size=2)
    assert stats["total"] == 5
    assert stats["archived"] == 5
    assert stats["errors"] == 0

    time.sleep(1)
    unarchived = admin_api.get_all_unarchived_assets()
    unarchived_ids = {a["id"] for a in unarchived}
    for aid in asset_ids:
        assert aid not in unarchived_ids
