"""File organization module.

Handles file copying, date extraction, and directory organization.
"""

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image
    from PIL.ExifTags import TAGS

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .hasher import FileHasher

logger = logging.getLogger(__name__)

# Month name -> number mapping used for folder-based date extraction.
MONTH_MAP: dict[str, int] = {
    # Abbreviated (3-letter)
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mai": 5, "may": 5,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # Full English names
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}


class FileOrganizer:
    """Organizes media files into a user/year/month directory structure."""

    def __init__(self, hasher: FileHasher, target_base_dir: Path, verify_copies: bool = True):
        self.hasher = hasher
        self.target_base_dir = Path(target_base_dir)
        self.verify_copies = verify_copies

    # ------------------------------------------------------------------
    # Date extraction
    # ------------------------------------------------------------------

    def extract_date(self, file_path: Path, exif_date: str = None) -> datetime:
        """Extract date from a file using multiple strategies.

        Priority:
        1. *exif_date* string (ISO format from Immich API ``fileCreatedAt``)
        2. EXIF tags embedded in the file (DateTimeOriginal, DateTime, DateTimeDigitized)
        3. Filename patterns (IMG_YYYYMMDD_, YYYY-MM-DD, YYYYMMDD, DD.MM.YYYY)
        4. Folder name patterns (YYYY-MM-DD, DD.Month.YYYY, DD Month YYYY, YYYY)
        5. File modification time
        """
        # 1. Provided exif_date string (ISO format)
        if exif_date is not None:
            parsed = self._parse_iso_date(exif_date)
            if parsed is not None:
                return parsed

        # 2. EXIF from file
        exif_result = self._extract_date_from_exif(file_path)
        if exif_result is not None:
            return exif_result

        # 3. Filename patterns
        filename_result = self._extract_date_from_filename(file_path.name)
        if filename_result is not None:
            return filename_result

        # 4. Folder patterns
        folder_result = self._extract_date_from_folder(file_path.parent)
        if folder_result is not None:
            return folder_result

        # 5. Modification time
        mtime = file_path.stat().st_mtime
        return datetime.fromtimestamp(mtime)

    # ------------------------------------------------------------------
    # Target path generation
    # ------------------------------------------------------------------

    def generate_target_path(
        self, source: Path, file_hash: str, user_name: str, date: datetime
    ) -> Path:
        """Build the target path for an organized file.

        Structure: ``{target_base_dir}/{user_name}/{year}/{MM}-{MonthName}/{filename}_{hash}.{ext}``
        """
        hash_filename = self._generate_hash_filename(source, file_hash)
        month_folder = f"{date.month:02d}-{date.strftime('%B')}"
        return self.target_base_dir / user_name / str(date.year) / month_folder / hash_filename

    # ------------------------------------------------------------------
    # Full organize pipeline
    # ------------------------------------------------------------------

    def organize_file(
        self, source: Path, user_name: str, exif_date: str = None
    ) -> dict:
        """Organize a single file: hash, deduplicate, copy, verify.

        Returns:
            ``{"target_path": Path, "hash": str, "is_duplicate": bool}``

        Raises on unrecoverable errors (hash failure, copy failure, verification failure).
        """
        # Fast path: if hash is already embedded in the filename and known to the
        # index, skip reading the file entirely.
        filename_hash = self.hasher.get_hash_from_filename(source.name)
        if filename_hash is not None and self.hasher.is_duplicate(filename_hash):
            logger.debug("Duplicate (filename hash): %s (hash %s)", source, filename_hash)
            return {
                "target_path": self.hasher._hash_index[filename_hash],
                "hash": filename_hash,
                "is_duplicate": True,
            }

        file_hash = self.hasher.calculate_hash(source)

        # Duplicate check
        if self.hasher.is_duplicate(file_hash):
            logger.info("Duplicate detected, skipping: %s (hash %s)", source, file_hash)
            return {
                "target_path": self.hasher._hash_index[file_hash],
                "hash": file_hash,
                "is_duplicate": True,
            }

        date = self.extract_date(source, exif_date=exif_date)
        target_path = self.generate_target_path(source, file_hash, user_name, date)

        self._copy_and_verify(source, target_path, file_hash)

        # Register in hash index after successful copy
        self.hasher._hash_index[file_hash] = target_path

        return {
            "target_path": target_path,
            "hash": file_hash,
            "is_duplicate": False,
        }

    # ------------------------------------------------------------------
    # Internal helpers – date parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_iso_date(date_str: str) -> datetime | None:
        """Parse a date string, tolerating ISO format, EXIF colon-format, and trailing ``Z``."""
        try:
            cleaned = date_str.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned)
        except (ValueError, TypeError):
            pass
        # EXIF-style: "2023:06:15 10:30:00"
        try:
            return datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_date_from_exif(file_path: Path) -> datetime | None:
        if not PIL_AVAILABLE:
            return None
        try:
            with Image.open(file_path) as img:
                exif_data = img._getexif()
                if not exif_data:
                    return None
                # Check tags in priority order
                tag_names = ("DateTimeOriginal", "DateTime", "DateTimeDigitized")
                tag_values: dict[str, str] = {}
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    if tag_name in tag_names:
                        tag_values[tag_name] = value
                for name in tag_names:
                    if name in tag_values:
                        try:
                            return datetime.strptime(tag_values[name], "%Y:%m:%d %H:%M:%S")
                        except ValueError:
                            continue
        except Exception as exc:
            logger.debug("Could not extract EXIF from %s: %s", file_path, exc)
        return None

    @staticmethod
    def _extract_date_from_filename(filename: str) -> datetime | None:
        patterns = [
            (r"IMG_(\d{4})(\d{2})(\d{2})_", "ymd"),
            (r"(\d{4})-(\d{2})-(\d{2})", "ymd"),
            (r"(\d{4})(\d{2})(\d{2})", "ymd"),
            (r"(\d{2})\.(\d{2})\.(\d{4})", "dmy"),
        ]
        for pattern, order in patterns:
            match = re.search(pattern, filename)
            if match:
                try:
                    g = match.groups()
                    if order == "ymd":
                        return datetime(int(g[0]), int(g[1]), int(g[2]))
                    else:  # dmy
                        return datetime(int(g[2]), int(g[1]), int(g[0]))
                except ValueError:
                    continue
        return None

    @staticmethod
    def _extract_date_from_folder(folder_path: Path) -> datetime | None:
        folder_name = folder_path.name

        # YYYY-MM-DD
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", folder_name)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass

        # DD.MonthName.YYYY
        m = re.search(r"(\d{2})\.(\w+)\.(\d{4})", folder_name)
        if m:
            day, month_name, year = m.groups()
            month = MONTH_MAP.get(month_name.lower())
            if month is None:
                month = MONTH_MAP.get(month_name.lower()[:3])
            if month is not None:
                try:
                    return datetime(int(year), month, int(day))
                except ValueError:
                    pass

        # DD MonthName YYYY
        m = re.search(r"(\d{2})\s+(\w+)\s+(\d{4})", folder_name)
        if m:
            day, month_name, year = m.groups()
            month = MONTH_MAP.get(month_name.lower())
            if month is None:
                month = MONTH_MAP.get(month_name.lower()[:3])
            if month is not None:
                try:
                    return datetime(int(year), month, int(day))
                except ValueError:
                    pass

        # Bare year YYYY
        m = re.search(r"(\d{4})", folder_name)
        if m:
            try:
                year = int(m.group(1))
                if 1900 <= year <= 2100:
                    return datetime(year, 1, 1)
            except ValueError:
                pass

        return None

    # ------------------------------------------------------------------
    # Internal helpers – filename / copy
    # ------------------------------------------------------------------

    def _generate_hash_filename(self, file_path: Path, file_hash: str) -> str:
        """Build ``stem_hash.ext``, stripping any existing hash suffix first."""
        stem = file_path.stem
        ext = file_path.suffix

        existing_hash = self.hasher.get_hash_from_filename(file_path.name)
        if existing_hash:
            # Remove the trailing _hash from the stem
            stem = stem[: -(len(existing_hash) + 1)]

        return f"{stem}_{file_hash}{ext}"

    def _copy_and_verify(self, source: Path, target: Path, source_hash: str) -> None:
        """Copy *source* to *target* with optional hash verification.

        Raises ``RuntimeError`` on failure.
        """
        if target.exists():
            logger.info("Target already exists, skipping copy: %s", target)
            return

        target.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Copying %s -> %s", source, target)
        shutil.copy2(source, target)

        if self.verify_copies:
            target_hash = self.hasher.calculate_hash(target)
            if target_hash != source_hash:
                logger.error(
                    "Copy verification failed for %s -> %s (source=%s, target=%s)",
                    source, target, source_hash, target_hash,
                )
                try:
                    target.unlink()
                except OSError:
                    pass
                raise RuntimeError(
                    f"Copy verification failed: {source} -> {target}"
                )
            logger.debug("Copy verified: %s", target)
