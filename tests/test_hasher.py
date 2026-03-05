"""Tests for lib.hasher -- no sandbox required."""

import hashlib
import pytest
from pathlib import Path

from lib.hasher import FileHasher, MEDIA_EXTENSIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_file(path: Path, content: bytes) -> Path:
    """Write binary content to *path* and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _expected_prefix(content: bytes, length: int = 12) -> str:
    """Return the first *length* hex chars of a sha256 digest."""
    return hashlib.sha256(content).hexdigest()[:length]


# ---------------------------------------------------------------------------
# calculate_hash
# ---------------------------------------------------------------------------

class TestCalculateHash:

    def test_calculate_hash(self, tmp_path):
        """Known content produces a hash that starts with the expected prefix."""
        content = b"hello world"
        f = _write_file(tmp_path / "sample.bin", content)

        hasher = FileHasher()
        result = hasher.calculate_hash(f)

        expected = _expected_prefix(content)
        assert result == expected
        assert len(result) == 12

    def test_calculate_hash_deterministic(self, tmp_path):
        """Hashing the same content twice yields the same hash."""
        content = b"deterministic content"
        f1 = _write_file(tmp_path / "a.bin", content)
        f2 = _write_file(tmp_path / "b.bin", content)

        hasher = FileHasher()
        assert hasher.calculate_hash(f1) == hasher.calculate_hash(f2)

    def test_calculate_hash_different_content(self, tmp_path):
        """Different content produces different hashes."""
        f1 = _write_file(tmp_path / "a.bin", b"alpha")
        f2 = _write_file(tmp_path / "b.bin", b"bravo")

        hasher = FileHasher()
        assert hasher.calculate_hash(f1) != hasher.calculate_hash(f2)


# ---------------------------------------------------------------------------
# get_hash_from_filename
# ---------------------------------------------------------------------------

class TestGetHashFromFilename:

    def test_get_hash_from_filename_valid(self):
        """Standard hash-suffixed filename returns the hash."""
        hasher = FileHasher()
        result = hasher.get_hash_from_filename("photo_abcdef012345.jpg")
        assert result == "abcdef012345"

    def test_get_hash_from_filename_no_hash(self):
        """Filename with no underscore-delimited hash returns None."""
        hasher = FileHasher()
        assert hasher.get_hash_from_filename("photo.jpg") is None

    def test_get_hash_from_filename_wrong_length(self):
        """Candidate segment too short returns None."""
        hasher = FileHasher()
        assert hasher.get_hash_from_filename("photo_abc.jpg") is None

    def test_get_hash_from_filename_non_hex(self):
        """12-char segment that isn't hex returns None."""
        hasher = FileHasher()
        assert hasher.get_hash_from_filename("photo_xyzxyzxyzxyz.jpg") is None


# ---------------------------------------------------------------------------
# build_hash_index
# ---------------------------------------------------------------------------

class TestBuildHashIndex:

    def test_build_hash_index(self, tmp_path):
        """Index is built from media files -- both hashed and unhashed names."""
        hasher = FileHasher()

        # File with a valid hash suffix in the name
        hashed_file = _write_file(tmp_path / "vacation_abcdef012345.jpg", b"jpeg data 1")
        # File without a hash suffix (hash will be computed from content)
        plain_file = _write_file(tmp_path / "sunset.png", b"png data 2")

        index = hasher.build_hash_index(tmp_path)

        # The hashed file should be indexed under the hash from its name
        assert "abcdef012345" in index
        assert index["abcdef012345"] == hashed_file

        # The plain file should be indexed under the hash of its content
        expected_hash = _expected_prefix(b"png data 2")
        assert expected_hash in index
        assert index[expected_hash] == plain_file

    def test_build_hash_index_nonexistent_dir(self, tmp_path):
        """Non-existent directory returns an empty dict."""
        hasher = FileHasher()
        index = hasher.build_hash_index(tmp_path / "does_not_exist")
        assert index == {}

    def test_build_hash_index_skips_non_media(self, tmp_path):
        """Non-media files (e.g. .txt) are not included in the index."""
        hasher = FileHasher()
        _write_file(tmp_path / "readme.txt", b"text data")
        _write_file(tmp_path / "photo.jpg", b"jpeg bytes")

        index = hasher.build_hash_index(tmp_path)

        # Only the jpg should be indexed
        assert len(index) == 1
        txt_hash = _expected_prefix(b"text data")
        assert txt_hash not in index


# ---------------------------------------------------------------------------
# is_duplicate
# ---------------------------------------------------------------------------

class TestIsDuplicate:

    def test_is_duplicate(self, tmp_path):
        """After building the index a known hash is duplicate, unknown is not."""
        hasher = FileHasher()
        content = b"unique photo bytes"
        _write_file(tmp_path / "img.jpg", content)

        hasher.build_hash_index(tmp_path)

        known_hash = _expected_prefix(content)
        assert hasher.is_duplicate(known_hash) is True
        assert hasher.is_duplicate("000000000000") is False
