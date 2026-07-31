"""Transparent decompression of archived log files with zip bomb protection."""

import bz2
import gzip
import io
import lzma
import zlib
import tarfile
import zipfile
from typing import IO
from urllib.parse import urlparse

from logdetective_mcp.exceptions import DecompressionError

DEFAULT_MAX_DECOMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MB
DEFAULT_MAX_COMPRESSION_RATIO = 100.0  # 100:1
_CHUNK_SIZE = 64 * 1024  # 64 KB

_DOUBLE_EXTENSIONS = {
    ".tar.gz": "tar.gz",
    ".tar.bz2": "tar.bz2",
    ".tar.xz": "tar.xz",
    ".tar.lzma": "tar.xz",
}

_SINGLE_EXTENSIONS = {
    ".gz": "gz",
    ".bz2": "bz2",
    ".xz": "xz",
    ".lzma": "xz",
    ".zip": "zip",
    ".tar": "tar",
}

_TAR_FORMATS = frozenset({"tar", "tar.gz", "tar.bz2", "tar.xz"})
_STREAM_OPENERS: dict[str, type] = {
    "bz2": bz2.BZ2File,
    "xz": lzma.LZMAFile,
}


def detect_format(source: str) -> str | None:
    """Detect compression format from a file path or URL.

    Checks double-extensions first (.tar.gz, etc.) then single extensions.
    For URLs, extracts the path component before matching.
    Case-insensitive.

    Returns:
        Canonical format string ("gz", "tar.gz", etc.) or None if uncompressed.
    """
    parsed = urlparse(source)
    path = parsed.path if parsed.scheme else source
    path_lower = path.lower()

    for ext, fmt in _DOUBLE_EXTENSIONS.items():
        if path_lower.endswith(ext):
            return fmt

    for ext, fmt in _SINGLE_EXTENSIONS.items():
        if path_lower.endswith(ext):
            return fmt

    return None


def read_chunks(
    fileobj: IO[bytes] | io.BufferedIOBase,
    compressed_size: int,
    max_bytes: int,
    max_ratio: float,
) -> bytes:
    """Read from a file-like object in chunks, enforcing size and ratio limits."""
    output = io.BytesIO()
    total = 0
    while True:
        chunk = fileobj.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise DecompressionError(
                f"Size exceeds limit of {max_bytes} bytes (reached {total} bytes)"
            )
        if compressed_size > 0 and total / compressed_size > max_ratio:
            raise DecompressionError(
                f"Compression ratio {total / compressed_size:.1f}:1 "
                f"exceeds limit of {max_ratio}:1"
            )
        output.write(chunk)
    return output.getvalue()


def _decompress_stream(
    data: bytes,
    opener: type[bz2.BZ2File | lzma.LZMAFile],
    max_bytes: int,
    max_ratio: float,
) -> bytes:
    try:
        with opener(io.BytesIO(data)) as f:
            return read_chunks(f, len(data), max_bytes, max_ratio)
    except (EOFError, OSError, lzma.LZMAError) as e:
        raise DecompressionError(f"Decompression failed: {e}") from e


def _decompress_gz(data: bytes, max_bytes: int, max_ratio: float) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            return read_chunks(f, len(data), max_bytes, max_ratio)
    except (zlib.error, EOFError, OSError) as e:
        raise DecompressionError(f"Corrupt gzip data: {e}") from e


def _decompress_zip(data: bytes, max_bytes: int, max_ratio: float) -> bytes:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise DecompressionError(f"Corrupt zip archive: {e}") from e

    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) == 0:
            raise DecompressionError("Archive contains no files")
        if len(members) > 1:
            names = [m.filename for m in members]
            raise DecompressionError(
                f"Archive contains {len(members)} files; expected exactly 1. "
                f"Files: {names}"
            )

        member = members[0]
        if member.file_size > max_bytes:
            raise DecompressionError(
                f"Declared uncompressed size {member.file_size} bytes "
                f"exceeds limit of {max_bytes} bytes"
            )

        try:
            with zf.open(member) as f:
                return read_chunks(f, len(data), max_bytes, max_ratio)
        except zipfile.BadZipFile as e:
            raise DecompressionError(f"Decompression failed with {e}") from e


def _decompress_tar(data: bytes, max_bytes: int, max_ratio: float) -> bytes:
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r")
    except tarfile.TarError as e:
        raise DecompressionError(f"Corrupt tar archive: {e}") from e

    with tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        if len(members) == 0:
            raise DecompressionError("Archive contains no files")
        if len(members) > 1:
            names = [m.name for m in members]
            raise DecompressionError(
                f"Archive contains {len(members)} files; expected exactly 1. "
                f"Files: {names}"
            )

        member = members[0]
        if member.size > max_bytes:
            raise DecompressionError(
                f"Declared uncompressed size {member.size} bytes "
                f"exceeds limit of {max_bytes} bytes"
            )

        f = tf.extractfile(member)
        if f is None:
            raise DecompressionError(
                f"Cannot extract member '{member.name}' from tar archive"
            )
        with f:
            return read_chunks(f, len(data), max_bytes, max_ratio)


def decompress(
    data: bytes,
    fmt: str,
    max_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> bytes:
    """Decompress data in the given format with safety limits.

    Args:
        data: Raw compressed bytes.
        fmt: Format string from detect_format().
        max_bytes: Maximum allowed decompressed size.
        max_ratio: Maximum allowed compression ratio.

    Returns:
        Decompressed bytes.

    Raises:
        DecompressionError: If safety limits are exceeded or archive is invalid.
    """
    if fmt == "gz":
        return _decompress_gz(data, max_bytes, max_ratio)
    if fmt in _STREAM_OPENERS:
        opener = _STREAM_OPENERS[fmt]
        return _decompress_stream(data, opener, max_bytes, max_ratio)
    if fmt == "zip":
        return _decompress_zip(data, max_bytes, max_ratio)
    if fmt in _TAR_FORMATS:
        return _decompress_tar(data, max_bytes, max_ratio)

    raise DecompressionError(f"Unsupported format: {fmt}")


def decompress_if_needed(
    data: bytes,
    source: str,
    max_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> str:
    """Detect format, decompress if needed, and decode to text.

    Args:
        data: Raw bytes (possibly compressed).
        source: Original file path or URL (used for format detection).
        max_bytes: Maximum allowed decompressed size.
        max_ratio: Maximum allowed compression ratio.

    Returns:
        Decoded text content.

    Raises:
        DecompressionError: If safety limits are exceeded or archive is invalid.
    """
    compression_fmt = detect_format(source)
    if compression_fmt is None:
        return data.decode("utf-8", errors="replace")

    decompressed = decompress(data, compression_fmt, max_bytes, max_ratio)
    return decompressed.decode("utf-8", errors="replace")
