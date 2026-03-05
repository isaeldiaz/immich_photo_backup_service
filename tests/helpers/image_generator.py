from pathlib import Path
from datetime import datetime

from PIL import Image
import piexif


def create_jpeg_with_exif(path: Path, width=100, height=100,
                          date: datetime = None, color=(255, 0, 0)) -> Path:
    """Create a JPEG with EXIF DateTimeOriginal.

    Args:
        path: Output file path
        width/height: Image dimensions
        date: Date to set in EXIF (default: 2023-06-15 10:30:00)
        color: RGB tuple for solid color fill

    Returns:
        The path written to
    """
    if date is None:
        date = datetime(2023, 6, 15, 10, 30, 0)

    img = Image.new("RGB", (width, height), color)

    # Build EXIF data
    exif_dict = {"Exif": {}, "0th": {}}
    date_str = date.strftime("%Y:%m:%d %H:%M:%S")
    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = date_str.encode()
    exif_dict["0th"][piexif.ImageIFD.Make] = b"TestCamera"

    exif_bytes = piexif.dump(exif_dict)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "JPEG", exif=exif_bytes)
    return path


def create_jpeg_without_exif(path: Path, width=100, height=100,
                              color=(0, 255, 0)) -> Path:
    """Create a JPEG without any EXIF data."""
    img = Image.new("RGB", (width, height), color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "JPEG")
    return path


def create_png(path: Path, width=100, height=100,
               color=(0, 0, 255)) -> Path:
    """Create a PNG image (no EXIF support)."""
    img = Image.new("RGB", (width, height), color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "PNG")
    return path
