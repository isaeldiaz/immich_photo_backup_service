"""Tests for lib.organizer -- no sandbox required."""

import os
import time
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from lib.hasher import FileHasher
from lib.organizer import FileOrganizer
from tests.helpers.image_generator import (
    create_jpeg_with_exif,
    create_jpeg_without_exif,
    create_png,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_file(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _make_organizer(tmp_path, hasher=None):
    """Build a FileOrganizer with a real FileHasher and a temp target dir."""
    if hasher is None:
        hasher = FileHasher()
    target_base = tmp_path / "library"
    target_base.mkdir(parents=True, exist_ok=True)
    return FileOrganizer(hasher=hasher, target_base_dir=target_base), target_base


# ---------------------------------------------------------------------------
# extract_date
# ---------------------------------------------------------------------------

class TestExtractDate:

    def test_extract_date_from_exif_string(self, tmp_path):
        """An ISO-style exif_date string is parsed into a datetime."""
        organizer, _ = _make_organizer(tmp_path)
        # Create a dummy file so the method has a valid path
        f = _write_file(tmp_path / "photo.jpg", b"dummy")

        dt = organizer.extract_date(f, exif_date="2023:06:15 10:30:00")
        assert dt.year == 2023
        assert dt.month == 6
        assert dt.day == 15

    def test_extract_date_from_exif_file(self, tmp_path):
        """EXIF DateTimeOriginal embedded in a JPEG is extracted."""
        organizer, _ = _make_organizer(tmp_path)
        target_date = datetime(2022, 12, 25, 8, 0, 0)
        f = create_jpeg_with_exif(tmp_path / "xmas.jpg", date=target_date)

        dt = organizer.extract_date(f)
        assert dt.year == 2022
        assert dt.month == 12
        assert dt.day == 25

    def test_extract_date_from_filename(self, tmp_path):
        """Dates embedded in filenames are recognised by common patterns."""
        organizer, _ = _make_organizer(tmp_path)

        cases = [
            ("IMG_20230615_photo.jpg", datetime(2023, 6, 15)),
            ("2023-06-15_photo.jpg", datetime(2023, 6, 15)),
            ("20230615.jpg", datetime(2023, 6, 15)),
        ]

        for filename, expected in cases:
            f = create_jpeg_without_exif(tmp_path / filename)
            dt = organizer.extract_date(f)
            assert dt.year == expected.year, f"Failed for {filename}"
            assert dt.month == expected.month, f"Failed for {filename}"
            assert dt.day == expected.day, f"Failed for {filename}"

    def test_extract_date_fallback_to_mtime(self, tmp_path):
        """When no date info is available the file mtime is used."""
        organizer, _ = _make_organizer(tmp_path)

        # A file whose name has no date pattern and no EXIF
        f = _write_file(tmp_path / "random_name.jpg", b"no date here")
        # Set a known mtime
        target_ts = datetime(2021, 3, 14, 12, 0, 0).timestamp()
        os.utime(f, (target_ts, target_ts))

        dt = organizer.extract_date(f)
        assert dt.year == 2021
        assert dt.month == 3
        assert dt.day == 14


# ---------------------------------------------------------------------------
# generate_target_path
# ---------------------------------------------------------------------------

class TestGenerateTargetPath:

    def test_generate_target_path(self, tmp_path):
        """Path follows {base}/{user}/{year}/{MM}-{Month}/{stem}_{hash}.{ext}."""
        organizer, base = _make_organizer(tmp_path)
        source = tmp_path / "sunset.jpg"
        source.touch()

        date = datetime(2023, 6, 15)
        target = organizer.generate_target_path(
            source=source,
            file_hash="abcdef012345",
            user_name="alice",
            date=date,
        )

        assert target == base / "alice" / "2023" / "06-June" / "sunset_abcdef012345.jpg"

    def test_generate_target_path_strips_existing_hash(self, tmp_path):
        """If the source already has a hash suffix it is replaced, not doubled."""
        organizer, base = _make_organizer(tmp_path)
        source = tmp_path / "sunset_000000000000.jpg"
        source.touch()

        date = datetime(2023, 6, 15)
        target = organizer.generate_target_path(
            source=source,
            file_hash="abcdef012345",
            user_name="alice",
            date=date,
        )

        # The old hash should be gone; only the new hash remains
        assert target.name == "sunset_abcdef012345.jpg"
        assert "000000000000" not in str(target)


# ---------------------------------------------------------------------------
# organize_file (end-to-end, still no sandbox)
# ---------------------------------------------------------------------------

class TestOrganizeFile:

    def test_organize_file_new(self, tmp_path):
        """Organise a brand-new file: copy is created with hash-suffixed name."""
        hasher = FileHasher()
        organizer, base = _make_organizer(tmp_path, hasher=hasher)

        source = create_jpeg_with_exif(
            tmp_path / "src" / "photo.jpg",
            date=datetime(2024, 1, 20),
        )

        result = organizer.organize_file(source, user_name="bob")

        assert result is not None
        assert result["is_duplicate"] is False
        assert result["target_path"].exists()
        assert result["hash"] in result["target_path"].name

    def test_organize_file_duplicate(self, tmp_path):
        """Organising the same content twice marks the second as duplicate."""
        hasher = FileHasher()
        organizer, base = _make_organizer(tmp_path, hasher=hasher)

        content = b"identical photo bytes"
        src1 = _write_file(tmp_path / "src" / "a.jpg", content)
        src2 = _write_file(tmp_path / "src" / "b.jpg", content)

        # First organize -- should succeed
        r1 = organizer.organize_file(src1, user_name="carol")
        assert r1 is not None
        assert r1["is_duplicate"] is False

        # Build index from the library so the duplicate is known
        hasher.build_hash_index(base)

        # Second organize -- same content, should be detected as duplicate
        r2 = organizer.organize_file(src2, user_name="carol")
        assert r2 is not None
        assert r2["is_duplicate"] is True

    def test_organize_file_verify_copy(self, tmp_path):
        """After organising, the target file hash matches the source hash."""
        hasher = FileHasher()
        organizer, base = _make_organizer(tmp_path, hasher=hasher)

        source = create_png(tmp_path / "src" / "diagram.png")

        result = organizer.organize_file(source, user_name="dave")

        assert result is not None
        source_hash = hasher.calculate_hash(source)
        target_hash = hasher.calculate_hash(result["target_path"])
        assert source_hash == target_hash
