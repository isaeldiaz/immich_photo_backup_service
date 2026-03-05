"""End-to-end sync workflow tests against the sandbox Immich instance.

These test the full pipeline (upload -> fetch unarchived -> organize -> archive)
using real API calls but with local temp dirs standing in for the NAS.
Since the sandbox stores files in a Docker volume we cannot access the original
paths from the host, so we re-use the local test images as the "source" files
and exercise the organizer + archiver logic on them directly.
"""

import shutil
import time
from datetime import datetime

import pytest

from lib.archiver import Archiver
from lib.hasher import FileHasher
from lib.organizer import FileOrganizer
from tests.helpers.image_generator import (
    create_jpeg_with_exif,
    create_jpeg_without_exif,
    create_png,
)

pytestmark = pytest.mark.sandbox


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _upload_and_wait(admin_api, paths, wait=1):
    """Upload a list of local paths and return (asset_ids, assets) after a brief wait."""
    asset_ids = []
    for p in paths:
        result = admin_api.upload_asset(p)
        asset_ids.append(result["id"])
    time.sleep(wait)
    return asset_ids


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

def test_full_sync_workflow(admin_api, tmp_path, nas_dir):
    """Upload -> fetch unarchived -> organize to NAS -> archive -> verify."""
    # 1. Create local test images
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    img1 = create_jpeg_with_exif(
        src_dir / "photo1.jpg",
        date=datetime(2024, 3, 15, 12, 0, 0),
        color=(200, 50, 50),
    )
    img2 = create_jpeg_without_exif(
        src_dir / "photo2.jpg",
        color=(50, 200, 50),
    )
    img3 = create_png(src_dir / "shot.png", color=(50, 50, 200))

    # 2. Upload to sandbox
    asset_ids = _upload_and_wait(admin_api, [img1, img2, img3])

    # 3. Fetch unarchived assets
    unarchived = admin_api.get_all_unarchived_assets()
    unarchived_ids = {a["id"] for a in unarchived}
    for aid in asset_ids:
        assert aid in unarchived_ids, f"Uploaded asset {aid} not found in unarchived list"

    # 4. Build hash index on (currently empty) NAS, then organize local files
    hasher = FileHasher()
    hash_index = hasher.build_hash_index(nas_dir)
    assert len(hash_index) == 0

    organizer = FileOrganizer(hasher, target_base_dir=nas_dir)
    user_name = "admin"

    results = []
    source_files = [img1, img2, img3]
    for src, aid in zip(source_files, asset_ids):
        # Retrieve fileCreatedAt from the API for accurate date extraction
        asset_info = admin_api.get_asset(aid)
        exif_date = asset_info.get("fileCreatedAt")
        result = organizer.organize_file(src, user_name, exif_date=exif_date)
        results.append(result)

    # 5. Verify files landed on the NAS with hash-based naming
    for res in results:
        assert res["target_path"].exists(), f"Organized file missing: {res['target_path']}"
        assert res["is_duplicate"] is False
        # Filename should contain the hash
        assert res["hash"] in res["target_path"].name

    # 6. Rebuild hash index and confirm it now contains the organized files
    updated_index = hasher.build_hash_index(nas_dir)
    assert len(updated_index) == 3

    # 7. Archive all uploaded assets
    archiver = Archiver(admin_api)
    stats = archiver.archive_assets(asset_ids)
    assert stats["archived"] == 3
    assert stats["errors"] == 0

    # 8. Verify assets are no longer unarchived in Immich
    time.sleep(1)
    remaining = admin_api.get_all_unarchived_assets()
    remaining_ids = {a["id"] for a in remaining}
    for aid in asset_ids:
        assert aid not in remaining_ids


def test_sync_dedup(admin_api, tmp_path, nas_dir):
    """Upload the same image content twice (different filenames), organize, verify one copy."""
    src_dir = tmp_path / "source"
    src_dir.mkdir()

    original = create_jpeg_with_exif(
        src_dir / "original.jpg",
        date=datetime(2024, 7, 1, 8, 0, 0),
        color=(100, 100, 100),
    )
    # Create an exact duplicate with a different name
    duplicate = src_dir / "copy_of_original.jpg"
    shutil.copy2(original, duplicate)

    asset_ids = _upload_and_wait(admin_api, [original, duplicate])

    # Build empty hash index, then organize both files
    hasher = FileHasher()
    hasher.build_hash_index(nas_dir)

    organizer = FileOrganizer(hasher, target_base_dir=nas_dir)

    res1 = organizer.organize_file(original, "admin")
    res2 = organizer.organize_file(duplicate, "admin")

    # First file should be organized; second should be flagged as duplicate
    assert res1["is_duplicate"] is False
    assert res1["target_path"].exists()

    assert res2["is_duplicate"] is True
    # The duplicate should point to the same target as the first
    assert res2["hash"] == res1["hash"]

    # Only one file should exist on the NAS
    updated_index = hasher.build_hash_index(nas_dir)
    assert len(updated_index) == 1


def test_dry_run_no_changes(admin_api, tmp_path, nas_dir):
    """Simulate dry-run: identify work to do but make no changes."""
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    img = create_jpeg_with_exif(
        src_dir / "dryrun.jpg",
        date=datetime(2025, 1, 10, 14, 0, 0),
        color=(10, 20, 30),
    )

    asset_ids = _upload_and_wait(admin_api, [img])

    # Fetch unarchived -- the asset should be present
    unarchived = admin_api.get_all_unarchived_assets()
    unarchived_ids = {a["id"] for a in unarchived}
    assert asset_ids[0] in unarchived_ids

    # Dry-run: compute hash and check for duplicates, but do NOT copy or archive
    hasher = FileHasher()
    hasher.build_hash_index(nas_dir)

    file_hash = hasher.calculate_hash(img)
    is_dup = hasher.is_duplicate(file_hash)

    # We expect it is not a duplicate (NAS is empty)
    assert is_dup is False

    # Verify NAS is still empty (no files were copied)
    assert list(nas_dir.rglob("*")) == [] or all(
        p.is_dir() for p in nas_dir.rglob("*")
    )

    # Verify the asset is still unarchived in Immich
    still_unarchived = admin_api.get_all_unarchived_assets()
    still_ids = {a["id"] for a in still_unarchived}
    assert asset_ids[0] in still_ids
