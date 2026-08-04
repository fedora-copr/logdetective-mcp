import http.client
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from mcp.server import FastMCP
from pydantic import BaseModel, Field

from logdetective_mcp.decompress import (
    DEFAULT_MAX_DECOMPRESSED_BYTES,
    decompress_if_needed,
    read_chunks,
)
from logdetective_mcp.exceptions import DecompressionError, DownloadError
from logdetective_mcp.extractor import DrainExtractor, PythonTracebackExtractor

mcp = FastMCP("Log Detective")


class Snippet(BaseModel):
    line_number: int = Field(description="Line number in the original log.")
    text: str = Field(description="Extracted log snippet text.")


def _download_with_limit(url: str, max_bytes: int) -> bytes:
    """Download URL content in chunks, rejecting responses that exceed *max_bytes*.

    Raises:
        DownloadError: On HTTP errors, network failures, timeouts, malformed
            URLs, or when the response exceeds *max_bytes*.
    """
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            return read_chunks(
                resp, compressed_size=0, max_bytes=max_bytes, max_ratio=1.0
            )
    except DecompressionError as exc:
        raise DownloadError(str(exc)) from exc
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.code} {exc.reason} for {url}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"Failed to fetch {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise DownloadError(f"Timeout for {url}: {exc}") from exc
    except (OSError, http.client.RemoteDisconnected, http.client.IncompleteRead) as exc:
        raise DownloadError(f"Connection error for {url}: {exc}") from exc
    except (ValueError, http.client.HTTPException) as exc:
        raise DownloadError(f"Invalid request for {url}: {exc}") from exc


def _read_log_source(
    log_text: str = "",
    log_path: str = "",
    log_url: str = "",
) -> str:
    """Resolve log content from exactly one of the three sources.

    All downloaded inputs are limited to DEFAULT_MAX_DECOMPRESSED_BYTES (100 MB),
    regardless of format. Compressed files (.gz, .bz2, .xz, .zip, .tar,
    .tar.gz, .tar.bz2, .tar.xz) are decompressed transparently with
    additional zip bomb protection.
    """

    sources = [s for s in [log_text, log_path, log_url] if s]
    if len(sources) != 1:
        raise ValueError(
            "Exactly one of log_text, log_path, or log_url must be provided."
        )

    if log_text:
        return log_text

    if log_path:
        path = Path(log_path)
        if not path.is_file():
            raise FileNotFoundError(f"Log file not found: {log_path}")
        raw = path.read_bytes()
        return decompress_if_needed(raw, str(path))

    if log_url:
        parsed = urlparse(log_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
        raw = _download_with_limit(log_url, DEFAULT_MAX_DECOMPRESSED_BYTES)
        return decompress_if_needed(raw, log_url)

    raise ValueError("Exactly one of log_text, log_path, or log_url must be provided.")


@mcp.tool()
def extract_log_snippets(
    log_text: str = "",
    log_path: str = "",
    log_url: str = "",
    max_clusters: int = 8,
    max_snippet_len: int = 2000,
    skip_patterns: list[str] = [],
) -> list[Snippet]:
    """Extract representative log snippets using Drain3 clustering.

    Analyzes log text by chunking it into logical messages, clustering
    similar messages using the Drain algorithm, and returning one
    representative snippet per cluster.

    Exactly one of log_text, log_path, or log_url must be provided.

    Downloaded inputs are limited to 100 MB.
    Compressed files (.gz, .bz2, .xz, .zip, .tar, .tar.gz, .tar.bz2,
    .tar.xz) are decompressed transparently. Archives must contain
    exactly one file. Compression ratios above 100:1 are rejected.

    Args:
        log_text: Raw log text to analyze.
        log_path: Path to a log file on disk.
        log_url: HTTP(S) URL to fetch log content from.
        max_clusters: Maximum number of clusters/snippets to extract.
        max_snippet_len: Maximum character length per snippet chunk.
        skip_patterns: Optional list of regex patterns.
            Chunks matching any pattern are excluded before clustering.
    """
    log = _read_log_source(
        log_text,
        log_path,
        log_url,
    )
    extractor = DrainExtractor(
        max_clusters=max_clusters,
        max_snippet_len=max_snippet_len,
        skip_patterns=skip_patterns,
    )
    raw_snippets = extractor(log)
    return [Snippet(line_number=line_no, text=text) for line_no, text in raw_snippets]


@mcp.tool()
def extract_python_tracebacks(
    log_text: str = "",
    log_path: str = "",
    log_url: str = "",
    max_traceback_len: int = 2000,
    skip_patterns: list[str] = [],
) -> list[Snippet]:
    """Extract Python tracebacks using specialized heuristic.

    Analyzes log text and extracts unbroken Python tracebacks.
    Traces are truncated to `max_traceback_len` characters.

    Exactly one of log_text, log_path, or log_url must be provided.

    Downloaded inputs are limited to 100 MB.
    Compressed files (.gz, .bz2, .xz, .zip, .tar, .tar.gz, .tar.bz2,
    .tar.xz) are decompressed transparently. Archives must contain
    exactly one file. Compression ratios above 100:1 are rejected.

    Args:
        log_text: Raw log text to analyze.
        log_path: Path to a log file on disk.
        log_url: HTTP(S) URL to fetch log content from.
        max_traceback_len: Maximum character length per extracted traceback.
        skip_patterns: Optional list of regex patterns.
            Chunks matching any pattern are excluded.
    """
    log = _read_log_source(
        log_text,
        log_path,
        log_url,
    )
    extractor = PythonTracebackExtractor(
        skip_patterns=skip_patterns, max_snippet_len=max_traceback_len
    )
    tracebacks = extractor(log)
    return [Snippet(line_number=line_no, text=text) for line_no, text in tracebacks]


def main():
    mcp.run()


if __name__ == "__main__":
    main()
