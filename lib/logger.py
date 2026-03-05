"""Logging module for Immich backup system."""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

_initialized = False


def _parse_size(size_str: str) -> int:
    """Parse a size string like '10MB' to bytes."""
    s = size_str.upper().strip()
    if s.endswith("GB"):
        return int(s[:-2]) * 1024 * 1024 * 1024
    if s.endswith("MB"):
        return int(s[:-2]) * 1024 * 1024
    if s.endswith("KB"):
        return int(s[:-2]) * 1024
    return int(s)


def setup_logging(
    log_file: str = "logs/immich_backup.log",
    level: str = "INFO",
    max_size: str = "10MB",
    backup_count: int = 5,
) -> None:
    """Configure root logger with rotating file handler and console handler."""
    global _initialized

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)

    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=_parse_size(max_size), backupCount=backup_count
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(file_formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)
    root.addHandler(console_handler)

    _initialized = True


def get_logger(name: str = "immich_backup") -> logging.Logger:
    """Get a named logger. setup_logging() must be called first."""
    if not _initialized:
        raise RuntimeError("Logging not initialized. Call setup_logging() first.")
    return logging.getLogger(name)
