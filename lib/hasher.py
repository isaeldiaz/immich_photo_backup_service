"""File hashing and duplicate detection."""

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MEDIA_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".raw",
    ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v", ".mpg", ".mpeg", ".flv",
})


class FileHasher:
    """SHA256-based file hashing and duplicate detection."""

    def __init__(self, hash_algorithm: str = "sha256", hash_length: int = 12):
        self.hash_algorithm = hash_algorithm
        self.hash_length = hash_length
        self._hash_index: dict[str, Path] = {}

    def calculate_hash(self, file_path: Path) -> str:
        """Calculate the hash of a file, returning the first `hash_length` hex characters.

        Reads the file in 8192-byte chunks. Raises on any I/O or hashing error.
        """
        hash_func = hashlib.new(self.hash_algorithm)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_func.update(chunk)
        return hash_func.hexdigest()[: self.hash_length]

    def get_hash_from_filename(self, filename: str) -> str | None:
        """Extract a hash suffix from a filename like `name_abcdef012345.ext`.

        Returns the hash string if the last underscore-delimited segment before
        the extension is a hex string of the expected length, otherwise None.
        """
        stem = Path(filename).stem
        parts = stem.split("_")
        if len(parts) < 2:
            return None
        candidate = parts[-1]
        if len(candidate) == self.hash_length and all(c in "0123456789abcdef" for c in candidate.lower()):
            return candidate.lower()
        return None

    def build_hash_index(self, library_dir: Path) -> dict[str, Path]:
        """Scan *library_dir* recursively and build a hash -> path index.

        For each media file, the hash is extracted from the filename when present;
        otherwise it is calculated from the file contents.  The internal
        ``_hash_index`` is replaced with the result.
        """
        logger.info("Building hash index of %s", library_dir)

        if not library_dir.exists():
            logger.warning("Library directory does not exist: %s", library_dir)
            self._hash_index = {}
            return self._hash_index

        hash_index: dict[str, Path] = {}
        file_count = 0

        for file_path in library_dir.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue

            file_count += 1

            file_hash = self.get_hash_from_filename(file_path.name)
            if file_hash is None:
                logger.info("No hash in filename, calculating: %s", file_path.name)
                file_hash = self.calculate_hash(file_path)

            if file_hash in hash_index:
                logger.warning("Duplicate hash %s", file_hash)
                logger.warning("  Original:  %s", hash_index[file_hash])
                logger.warning("  Duplicate: %s", file_path)
            else:
                hash_index[file_hash] = file_path

            if file_count % 100 == 0:
                logger.info("Processed %d files...", file_count)

        logger.info("Hash index built: %d unique files from %d total", len(hash_index), file_count)
        self._hash_index = hash_index
        return self._hash_index

    def is_duplicate(self, file_hash: str) -> bool:
        """Check whether *file_hash* already exists in the index.

        ``build_hash_index`` must be called before this method.
        """
        return file_hash in self._hash_index
