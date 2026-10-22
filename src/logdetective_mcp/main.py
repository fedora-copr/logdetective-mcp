import http.client
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated
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
MAX_CONTEXT_LINES = 50


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


def _add_context(
    log: str,
    snippets: list[tuple[int, str]],
    context: int,
    source_spans: list[tuple[int, int, bool]] | None = None,
    max_core_len: int | None = None,
) -> list[tuple[int, str]]:
    """Add surrounding lines while retaining shortened extractor output.

    ``source_spans`` supplies inclusive source start/end lines and a flag for
    preserving truncated or split text whose rendering differs from the source.
    """
    _validate_context(context)
    if context == 0:
        return snippets

    lines = log.splitlines()
    contextual_snippets = []
    for index, (line_number, text) in enumerate(snippets):
        snippet_start = max(line_number - 1, 0)
        snippet_lines = text.splitlines()
        snippet_end = snippet_start + max(len(snippet_lines), 1) - 1
        preserve_text = False
        if source_spans is not None:
            source_start, source_end, preserve_text = source_spans[index]
            snippet_start = source_start - 1
            snippet_end = source_end - 1
        else:
            # Without spans, match rendered lines to source lines where possible.
            source_index = snippet_start
            for snippet_line in snippet_lines:
                while source_index < len(lines) and not lines[source_index].strip():
                    source_index += 1
                if source_index == len(lines) or lines[source_index] != snippet_line:
                    preserve_text = True
                    break
                snippet_end = source_index
                source_index += 1
            if preserve_text:
                snippet_end = min(
                    snippet_start + max(len(snippet_lines), 1) - 1,
                    len(lines) - 1,
                )

        context_start = max(0, snippet_start - context)
        context_end = min(len(lines), snippet_end + context + 1)
        if preserve_text:
            core = text
        else:
            source_core = "\n".join(lines[snippet_start : snippet_end + 1])
            exceeds_limit = max_core_len is not None and len(source_core) > max_core_len
            core = text if exceeds_limit else source_core
        parts = (
            lines[context_start:snippet_start]
            + [core]
            + lines[snippet_end + 1 : context_end]
        )
        contextual_snippets.append((context_start + 1, "\n".join(parts)))
    return contextual_snippets


def _validate_context(context: int) -> None:
    """Reject context sizes outside the supported 0-50 line range."""
    if not isinstance(context, int) or not 0 <= context <= MAX_CONTEXT_LINES:
        raise ValueError(f"context must be between 0 and {MAX_CONTEXT_LINES}")


@mcp.tool()
def extract_log_snippets(
    log_text: str = "",
    log_path: str = "",
    log_url: str = "",
    max_clusters: int = 8,
    max_snippet_len: int = 2000,
    skip_patterns: list[str] = [],
    context: Annotated[
        int,
        Field(
            ge=0,
            le=MAX_CONTEXT_LINES,
            description="Number of surrounding lines to include.",
        ),
    ] = 0,
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
        context: Number of lines to include before and after each snippet (0-50).
            These lines are additional to max_snippet_len. Split chunks retain
            their extracted text instead of expanding to the whole source line.
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
    if context:
        extract_with_spans = getattr(extractor, "extract_with_spans", None)
        spans = extract_with_spans(log) if callable(extract_with_spans) else None
        if isinstance(spans, list):
            raw_snippets = [(start, text) for start, _, text, _ in spans]
            raw_snippets = _add_context(
                log,
                raw_snippets,
                context,
                source_spans=[
                    (start, end, preserve) for start, end, _, preserve in spans
                ],
                max_core_len=max_snippet_len,
            )
        else:
            # Keep compatibility with extractor substitutes that implement
            # only the original callable interface.
            raw_snippets = _add_context(
                log, extractor(log), context, max_core_len=max_snippet_len
            )
    else:
        raw_snippets = extractor(log)
    return [Snippet(line_number=line_no, text=text) for line_no, text in raw_snippets]


@mcp.tool()
def extract_python_tracebacks(
    log_text: str = "",
    log_path: str = "",
    log_url: str = "",
    max_traceback_len: int = 2000,
    skip_patterns: list[str] = [],
    context: Annotated[
        int,
        Field(
            ge=0,
            le=MAX_CONTEXT_LINES,
            description="Number of surrounding lines to include.",
        ),
    ] = 0,
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
        context: Number of lines to include before and after each traceback (0-50).
            These lines are additional to max_traceback_len. A truncated
            traceback keeps its shortened text; following context begins after
            the original traceback ends.
    """
    log = _read_log_source(
        log_text,
        log_path,
        log_url,
    )
    extractor = PythonTracebackExtractor(
        skip_patterns=skip_patterns, max_snippet_len=max_traceback_len
    )
    if context:
        spans = extractor.extract_with_spans(log)
        tracebacks = [(start, text) for start, _, text, _ in spans]
        tracebacks = _add_context(
            log,
            tracebacks,
            context,
            source_spans=[
                (start, end, truncated) for start, end, _, truncated in spans
            ],
            max_core_len=max_traceback_len,
        )
    else:
        tracebacks = extractor(log)
    return [Snippet(line_number=line_no, text=text) for line_no, text in tracebacks]


def main():
    mcp.run()


if __name__ == "__main__":
    main()
