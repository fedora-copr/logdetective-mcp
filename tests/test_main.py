import gzip
import io

import pytest
from unittest.mock import patch

from logdetective_mcp.decompress import decompress_if_needed
from logdetective_mcp.exceptions import DecompressionError, DownloadError
from logdetective_mcp.main import (
    Snippet,
    _download_with_limit,
    _read_log_source,
    extract_log_snippets,
)

valid_sources = [
    "/valid/path/to.log",
    "https://valid.url/build.log",
    "This is also a valid log.",
    None,
]
none_sources = ["none", "None", " None", "   none   "]


class TestReadLogSource:
    def test_log_text(self):
        assert _read_log_source(log_text="hello") == "hello"

    def test_log_path(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("log from file")
        assert _read_log_source(log_path=str(f)) == "log from file"

    def test_log_path_not_found(self):
        with pytest.raises(FileNotFoundError):
            _read_log_source(log_path="/nonexistent/path.log")

    def test_no_source_raises(self):
        with pytest.raises(ValueError, match="Exactly one"):
            _read_log_source()

    def test_multiple_sources_raises(self):
        with pytest.raises(ValueError, match="Exactly one"):
            _read_log_source(log_text="x", log_path="/tmp/x")

    def test_unsupported_url_scheme(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _read_log_source(log_url="ftp://example.com/log")

    def test_log_path_gzipped(self, tmp_path):
        content = "ERROR build failed\nWARN low disk"
        f = tmp_path / "build.log.gz"
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(content.encode())
        f.write_bytes(buf.getvalue())
        assert _read_log_source(log_path=str(f)) == content

    def test_log_path_plain_text_unchanged(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("plain text log")
        assert _read_log_source(log_path=str(f)) == "plain text log"

    @patch("urllib.request.urlopen")
    def test_log_url_gzipped(self, mock):
        content = "ERROR from url"
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(content.encode())
        compressed = buf.getvalue()
        mock.return_value.__enter__.return_value.read.side_effect = [compressed, b""]
        result = _read_log_source(log_url="https://example.com/build.log.gz")
        assert result == content

    def test_decompression_error_propagates(self):
        big_data = b"A" * 2000
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(big_data)
        with pytest.raises(DecompressionError):
            decompress_if_needed(buf.getvalue(), "big.log.gz", max_bytes=100)


class TestSizeLimits:
    @patch("urllib.request.urlopen")
    def test_download_exceeds_size_limit(self, mock):
        big_data = b"A" * 2000
        mock.return_value.__enter__.return_value.read.side_effect = [
            big_data,
        ]
        with pytest.raises(DownloadError, match="exceeds limit"):
            _download_with_limit("https://example.com/huge.log", max_bytes=100)

    @patch("urllib.request.urlopen")
    def test_download_within_size_limit(self, mock):
        data = b"small log"
        mock.return_value.__enter__.return_value.read.side_effect = [data, b""]
        result = _download_with_limit("https://example.com/small.log", max_bytes=1000)
        assert result == data

    @patch("urllib.request.urlopen")
    def test_download_chunked_exceeds_limit(self, mock):
        """Verify limit is enforced across multiple chunks."""
        chunk = b"A" * 100
        mock.return_value.__enter__.return_value.read.side_effect = [
            chunk,
            chunk,
            chunk,
        ]
        with pytest.raises(DownloadError, match="exceeds limit"):
            _download_with_limit("https://example.com/big.log", max_bytes=250)


class TestExtractLogSnippets:
    def test_returns_snippet_objects(self):
        result = extract_log_snippets(log_text="ERROR something broke")
        assert len(result) == 1
        assert isinstance(result[0], Snippet)

    def test_snippet_fields(self):
        result = extract_log_snippets(log_text="ERROR something broke")
        snippet = result[0]
        assert snippet.line_number == 1
        assert snippet.text == "ERROR something broke"

    def test_empty_input(self):
        with pytest.raises(ValueError):
            assert extract_log_snippets(log_text="")

    def test_deduplication(self):
        log = "\n".join(["ERROR same message"] * 5)
        result = extract_log_snippets(log_text=log)
        assert len(result) == 1

    def test_max_clusters_forwarded(self):
        lines = [f"DISTINCT_{i} unique content {i * 999}" for i in range(20)]
        log = "\n".join(lines)
        result = extract_log_snippets(log_text=log, max_clusters=2)
        assert len(result) <= 2

    def test_skip_patterns_forwarded(self):
        log = "DEBUG noise\nERROR real problem"
        result = extract_log_snippets(log_text=log, skip_patterns=["DEBUG.*"])
        texts = [s.text for s in result]
        assert not any("DEBUG" in t for t in texts)
        assert any("ERROR" in t for t in texts)

    def test_from_file(self, tmp_path):
        f = tmp_path / "build.log"
        f.write_text("ERROR build failed\nWARN low disk")
        result = extract_log_snippets(log_path=str(f))
        assert len(result) == 2

    def test_from_gzipped_file(self, tmp_path):
        content = "ERROR build failed\nWARN low disk"
        f = tmp_path / "build.log.gz"
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(content.encode())
        f.write_bytes(buf.getvalue())
        result = extract_log_snippets(log_path=str(f))
        assert len(result) == 2
        texts = [s.text for s in result]
        assert any("ERROR" in t for t in texts)
        assert any("WARN" in t for t in texts)
