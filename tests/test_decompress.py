import bz2
import gzip
import io
import lzma
import tarfile
import zipfile

import pytest

from logdetective_mcp.exceptions import DecompressionError
from logdetective_mcp.decompress import (
    decompress,
    decompress_if_needed,
    detect_format,
)

SAMPLE_TEXT = "ERROR something broke\nWARN low disk\nINFO all good"
SAMPLE_BYTES = SAMPLE_TEXT.encode()


# ---------------------------------------------------------------------------
# Test archive helpers
# ---------------------------------------------------------------------------


def make_gz(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(data)
    return buf.getvalue()


def make_bz2(data: bytes) -> bytes:
    return bz2.compress(data)


def make_xz(data: bytes) -> bytes:
    return lzma.compress(data)


def make_zip_archive(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, data)
    return buf.getvalue()


def make_zip_multi(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_tar(name: str, data: bytes, compression: str = "") -> bytes:
    buf = io.BytesIO()
    mode = f"w:{compression}" if compression else "w"
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def make_tar_multi(files: dict[str, bytes], compression: str = "") -> bytes:
    buf = io.BytesIO()
    mode = f"w:{compression}" if compression else "w"
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


class TestDetectFormat:
    @pytest.mark.parametrize(
        "source,expected",
        [
            ("build.log", None),
            ("build.log.gz", "gz"),
            ("/var/log/build.log.gz", "gz"),
            ("build.log.bz2", "bz2"),
            ("build.log.xz", "xz"),
            ("build.log.lzma", "xz"),
            ("build.log.zip", "zip"),
            ("archive.tar", "tar"),
            ("archive.tar.gz", "tar.gz"),
            ("archive.tar.bz2", "tar.bz2"),
            ("archive.tar.xz", "tar.xz"),
            ("archive.tar.lzma", "tar.xz"),
            ("noextension", None),
        ],
    )
    def test_file_paths(self, source, expected):
        assert detect_format(source) == expected

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://example.com/build.log.gz", "gz"),
            ("https://example.com/build.log.gz?token=abc", "gz"),
            ("https://example.com/build.log.bz2#section", "bz2"),
            ("https://example.com/logs/build.tar.gz", "tar.gz"),
            ("https://example.com/build.log", None),
            ("http://koji.example.com/build.log.xz", "xz"),
        ],
    )
    def test_urls(self, url, expected):
        assert detect_format(url) == expected

    @pytest.mark.parametrize(
        "source,expected",
        [
            ("build.log.GZ", "gz"),
            ("archive.TAR.GZ", "tar.gz"),
            ("build.LOG.BZ2", "bz2"),
            ("build.Xz", "xz"),
        ],
    )
    def test_case_insensitive(self, source, expected):
        assert detect_format(source) == expected

    def test_double_extension_priority(self):
        assert detect_format("archive.tar.gz") == "tar.gz"
        assert detect_format("archive.tar.bz2") == "tar.bz2"


# ---------------------------------------------------------------------------
# Round-trip decompression per format
# ---------------------------------------------------------------------------


class TestDecompressGz:
    def test_round_trip(self):
        compressed = make_gz(SAMPLE_BYTES)
        result = decompress(compressed, "gz")
        assert result == SAMPLE_BYTES


class TestDecompressBz2:
    def test_round_trip(self):
        compressed = make_bz2(SAMPLE_BYTES)
        result = decompress(compressed, "bz2")
        assert result == SAMPLE_BYTES


class TestDecompressXz:
    def test_round_trip(self):
        compressed = make_xz(SAMPLE_BYTES)
        result = decompress(compressed, "xz")
        assert result == SAMPLE_BYTES


class TestDecompressZip:
    def test_single_file(self):
        compressed = make_zip_archive("build.log", SAMPLE_BYTES)
        result = decompress(compressed, "zip")
        assert result == SAMPLE_BYTES

    def test_directory_entries_ignored(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.mkdir("logs")
            zf.writestr("logs/build.log", SAMPLE_BYTES)
        compressed = buf.getvalue()
        result = decompress(compressed, "zip")
        assert result == SAMPLE_BYTES


class TestDecompressTar:
    def test_single_file(self):
        compressed = make_tar("build.log", SAMPLE_BYTES)
        result = decompress(compressed, "tar")
        assert result == SAMPLE_BYTES

    def test_tar_gz(self):
        compressed = make_tar("build.log", SAMPLE_BYTES, compression="gz")
        result = decompress(compressed, "tar.gz")
        assert result == SAMPLE_BYTES

    def test_tar_bz2(self):
        compressed = make_tar("build.log", SAMPLE_BYTES, compression="bz2")
        result = decompress(compressed, "tar.bz2")
        assert result == SAMPLE_BYTES

    def test_tar_xz(self):
        compressed = make_tar("build.log", SAMPLE_BYTES, compression="xz")
        result = decompress(compressed, "tar.xz")
        assert result == SAMPLE_BYTES


# ---------------------------------------------------------------------------
# Zip bomb protection
# ---------------------------------------------------------------------------


class TestSafetyLimits:
    # -- max_bytes exceeded per format --

    @pytest.mark.parametrize(
        "fmt,make_fn",
        [
            ("gz", make_gz),
            ("bz2", make_bz2),
            ("xz", make_xz),
        ],
    )
    def test_max_bytes_exceeded_stream(self, fmt, make_fn):
        big_data = b"A" * 2000
        compressed = make_fn(big_data)
        with pytest.raises(DecompressionError, match="Size exceeds limit"):
            decompress(compressed, fmt, max_bytes=100)

    def test_max_bytes_exceeded_zip(self):
        big_data = b"A" * 2000
        compressed = make_zip_archive("big.log", big_data)
        with pytest.raises(DecompressionError, match="exceeds limit"):
            decompress(compressed, "zip", max_bytes=100)

    @pytest.mark.parametrize(
        "compression,fmt",
        [
            ("", "tar"),
            ("gz", "tar.gz"),
            ("bz2", "tar.bz2"),
            ("xz", "tar.xz"),
        ],
    )
    def test_max_bytes_exceeded_tar(self, compression, fmt):
        big_data = b"A" * 2000
        compressed = make_tar("big.log", big_data, compression=compression)
        with pytest.raises(DecompressionError, match="exceeds limit"):
            decompress(compressed, fmt, max_bytes=100)

    # -- compression ratio exceeded per format --

    @pytest.mark.parametrize(
        "fmt,make_fn",
        [
            ("gz", make_gz),
            ("bz2", make_bz2),
            ("xz", make_xz),
        ],
    )
    def test_compression_ratio_exceeded_stream(self, fmt, make_fn):
        repetitive_data = b"\x00" * 100_000
        compressed = make_fn(repetitive_data)
        with pytest.raises(DecompressionError, match="Compression ratio"):
            decompress(compressed, fmt, max_ratio=2.0)

    def test_compression_ratio_exceeded_zip(self):
        repetitive_data = b"\x00" * 100_000
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.log", repetitive_data)
        compressed = buf.getvalue()
        with pytest.raises(DecompressionError, match="Compression ratio"):
            decompress(compressed, "zip", max_ratio=2.0)

    @pytest.mark.parametrize(
        "compression,fmt",
        [
            ("gz", "tar.gz"),
            ("bz2", "tar.bz2"),
            ("xz", "tar.xz"),
        ],
    )
    def test_compression_ratio_exceeded_tar(self, compression, fmt):
        repetitive_data = b"\x00" * 100_000
        compressed = make_tar("big.log", repetitive_data, compression=compression)
        with pytest.raises(DecompressionError, match="Compression ratio"):
            decompress(compressed, fmt, max_ratio=2.0)

    # -- declared size pre-check --

    def test_declared_size_bomb_zip(self):
        compressed = make_zip_archive("bomb.log", b"small")
        with pytest.raises(DecompressionError, match="exceeds limit"):
            decompress(compressed, "zip", max_bytes=1)

    def test_declared_size_bomb_tar(self):
        compressed = make_tar("bomb.log", b"small")
        with pytest.raises(DecompressionError, match="exceeds limit"):
            decompress(compressed, "tar", max_bytes=1)

    # -- multi-file and empty archive rejection --

    def test_multi_file_zip_rejected(self):
        compressed = make_zip_multi(
            {
                "log1.txt": b"first",
                "log2.txt": b"second",
            }
        )
        with pytest.raises(DecompressionError, match="contains 2 files"):
            decompress(compressed, "zip")

    def test_multi_file_tar_rejected(self):
        compressed = make_tar_multi(
            {
                "log1.txt": b"first",
                "log2.txt": b"second",
            }
        )
        with pytest.raises(DecompressionError, match="contains 2 files"):
            decompress(compressed, "tar")

    @pytest.mark.parametrize(
        "compression,fmt",
        [
            ("gz", "tar.gz"),
            ("bz2", "tar.bz2"),
            ("xz", "tar.xz"),
        ],
    )
    def test_multi_file_compressed_tar_rejected(self, compression, fmt):
        compressed = make_tar_multi(
            {"a.log": b"first", "b.log": b"second"},
            compression=compression,
        )
        with pytest.raises(DecompressionError, match="contains 2 files"):
            decompress(compressed, fmt)

    def test_empty_zip_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass
        with pytest.raises(DecompressionError, match="no files"):
            decompress(buf.getvalue(), "zip")

    def test_empty_tar_rejected(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w"):
            pass
        with pytest.raises(DecompressionError, match="no files"):
            decompress(buf.getvalue(), "tar")

    # -- error handling --

    def test_unsupported_format(self):
        with pytest.raises(DecompressionError, match="Unsupported format"):
            decompress(b"data", "rar")

    def test_corrupt_zip(self):
        with pytest.raises(DecompressionError, match="Corrupt zip"):
            decompress(b"not a zip", "zip")

    def test_corrupt_zip_member_data(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("build.log", "A" * 100)
        raw = bytearray(buf.getvalue())
        # Corrupt stored data after local file header (30 bytes + filename)
        raw[40] ^= 0xFF
        with pytest.raises(DecompressionError, match="Decompression failed"):
            decompress(bytes(raw), "zip")

    def test_corrupt_tar(self):
        with pytest.raises(DecompressionError, match="Corrupt tar"):
            decompress(b"not a tar", "tar")

    # -- no recursive decompression --

    def test_no_recursive_decompression(self):
        inner_gz = make_gz(SAMPLE_BYTES)
        outer_zip = make_zip_archive("nested.gz", inner_gz)
        result = decompress(outer_zip, "zip")
        assert result == inner_gz
        assert result != SAMPLE_BYTES


# ---------------------------------------------------------------------------
# Convenience function: decompress_if_needed
# ---------------------------------------------------------------------------


class TestDecompressIfNeeded:
    def test_uncompressed_passthrough(self):
        result = decompress_if_needed(SAMPLE_BYTES, "build.log")
        assert result == SAMPLE_TEXT

    @pytest.mark.parametrize(
        "ext,make_fn",
        [
            (".gz", make_gz),
            (".bz2", make_bz2),
            (".xz", make_xz),
        ],
    )
    def test_stream_formats(self, ext, make_fn):
        compressed = make_fn(SAMPLE_BYTES)
        result = decompress_if_needed(compressed, f"build.log{ext}")
        assert result == SAMPLE_TEXT

    def test_zip_format(self):
        compressed = make_zip_archive("build.log", SAMPLE_BYTES)
        result = decompress_if_needed(compressed, "build.log.zip")
        assert result == SAMPLE_TEXT

    @pytest.mark.parametrize(
        "compression,ext",
        [
            ("", ".tar"),
            ("gz", ".tar.gz"),
            ("bz2", ".tar.bz2"),
            ("xz", ".tar.xz"),
        ],
    )
    def test_tar_formats(self, compression, ext):
        compressed = make_tar("build.log", SAMPLE_BYTES, compression=compression)
        result = decompress_if_needed(compressed, f"build{ext}")
        assert result == SAMPLE_TEXT

    def test_url_source(self):
        compressed = make_gz(SAMPLE_BYTES)
        result = decompress_if_needed(
            compressed, "https://example.com/build.log.gz?token=x"
        )
        assert result == SAMPLE_TEXT

    def test_utf8_replacement_for_bad_bytes(self):
        bad_bytes = b"hello \xff world"
        result = decompress_if_needed(bad_bytes, "build.log")
        assert "hello" in result
        assert "world" in result
        assert "�" in result

    def test_utf8_replacement_after_decompression(self):
        bad_bytes = b"hello \xff world"
        compressed = make_gz(bad_bytes)
        result = decompress_if_needed(compressed, "build.log.gz")
        assert "hello" in result
        assert "world" in result
        assert "�" in result
