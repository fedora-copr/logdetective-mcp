import gzip
import http.client
import io
import urllib.error

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

    @patch("logdetective_mcp.main.urllib.request.urlopen")
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
    @patch("logdetective_mcp.main.urllib.request.urlopen")
    def test_download_exceeds_size_limit(self, mock):
        big_data = b"A" * 2000
        mock.return_value.__enter__.return_value.read.side_effect = [
            big_data,
        ]
        with pytest.raises(DownloadError, match="exceeds limit"):
            _download_with_limit("https://example.com/huge.log", max_bytes=100)

    @patch("logdetective_mcp.main.urllib.request.urlopen")
    def test_download_within_size_limit(self, mock):
        data = b"small log"
        mock.return_value.__enter__.return_value.read.side_effect = [data, b""]
        result = _download_with_limit("https://example.com/small.log", max_bytes=1000)
        assert result == data

    @patch("logdetective_mcp.main.urllib.request.urlopen")
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


class TestDownloadErrors:
    @patch("logdetective_mcp.main.urllib.request.urlopen")
    def test_http_error(self, mock):
        mock.side_effect = urllib.error.HTTPError(
            "https://example.com/log", 404, "Not Found", {}, None
        )
        with pytest.raises(DownloadError, match="HTTP 404"):
            _download_with_limit("https://example.com/log", max_bytes=1000)

    @patch("logdetective_mcp.main.urllib.request.urlopen")
    def test_url_error(self, mock):
        mock.side_effect = urllib.error.URLError("Name or service not known")
        with pytest.raises(DownloadError, match="Failed to fetch"):
            _download_with_limit("https://example.com/log", max_bytes=1000)

    @patch("logdetective_mcp.main.urllib.request.urlopen")
    def test_timeout_error(self, mock):
        """TimeoutError is only reachable during resp.read(), not urlopen()."""
        mock.return_value.__enter__.return_value.read.side_effect = TimeoutError(
            "timed out"
        )
        with pytest.raises(DownloadError, match="Timeout for"):
            _download_with_limit("https://example.com/log", max_bytes=1000)

    @patch("logdetective_mcp.main.urllib.request.urlopen")
    def test_connection_refused(self, mock):
        mock.side_effect = ConnectionRefusedError("Connection refused")
        with pytest.raises(DownloadError, match="Connection error"):
            _download_with_limit("https://example.com/log", max_bytes=1000)

    @patch("logdetective_mcp.main.urllib.request.urlopen")
    def test_incomplete_read(self, mock):
        mock.return_value.__enter__.return_value.read.side_effect = (
            http.client.IncompleteRead(b"partial", 1000)
        )
        with pytest.raises(DownloadError, match="Connection error"):
            _download_with_limit("https://example.com/log", max_bytes=1000)

    @patch("logdetective_mcp.main.urllib.request.urlopen")
    def test_malformed_url_value_error(self, mock):
        mock.side_effect = ValueError("Invalid IPv6 URL")
        with pytest.raises(DownloadError, match="Invalid request"):
            _download_with_limit("https://[invalid/log", max_bytes=1000)

    @patch("logdetective_mcp.main.urllib.request.urlopen")
    def test_invalid_url(self, mock):
        mock.side_effect = http.client.InvalidURL("nonnumeric port")
        with pytest.raises(DownloadError, match="Invalid request"):
            _download_with_limit("https://host:notaport/log", max_bytes=1000)


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
