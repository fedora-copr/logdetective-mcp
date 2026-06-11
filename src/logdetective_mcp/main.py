import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from mcp.server import FastMCP
from pydantic import BaseModel, Field

from logdetective_mcp.extractor import DrainExtractor

mcp = FastMCP("Log Detective")


class Snippet(BaseModel):
    line_number: int = Field(description="Line number in the original log.")
    text: str = Field(description="Extracted log snippet text.")


def _read_log_source(
    log_text: str | None = None,
    log_path: str | None = None,
    log_url: str | None = None,
) -> str:
    """Resolve log content from exactly one of the three sources."""
    sources = [s for s in (log_text, log_path, log_url) if s is not None]
    if len(sources) != 1:
        raise ValueError(
            "Exactly one of log_text, log_path, or log_url must be provided."
        )

    if log_text is not None:
        return log_text

    if log_path is not None:
        path = Path(log_path)
        if not path.is_file():
            raise FileNotFoundError(f"Log file not found: {log_path}")
        return path.read_text()

    if log_url is not None:
        parsed = urlparse(log_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
        with urllib.request.urlopen(log_url) as resp:  # noqa: S310
            return resp.read().decode()

    raise ValueError("Exactly one of log_text, log_path, or log_url must be provided.")


@mcp.tool()
def extract_log_snippets(
    log_text: str | None = None,
    log_path: str | None = None,
    log_url: str | None = None,
    max_clusters: int = 8,
    max_snippet_len: int = 2000,
    skip_patterns: dict[str, str] | None = None,
) -> list[Snippet]:
    """Extract representative log snippets using Drain3 clustering.

    Analyzes log text by chunking it into logical messages, clustering
    similar messages using the Drain algorithm, and returning one
    representative snippet per cluster.

    Exactly one of log_text, log_path, or log_url must be provided.

    Args:
        log_text: Raw log text to analyze.
        log_path: Path to a log file on disk.
        log_url: HTTP(S) URL to fetch log content from.
        max_clusters: Maximum number of clusters/snippets to extract.
        max_snippet_len: Maximum character length per snippet chunk.
        skip_patterns: Optional dict mapping names to regex patterns.
            Chunks matching any pattern are excluded before clustering.
    """
    log = _read_log_source(log_text, log_path, log_url)
    extractor = DrainExtractor(
        max_clusters=max_clusters,
        max_snippet_len=max_snippet_len,
        skip_patterns=skip_patterns,
    )
    raw_snippets = extractor(log)
    return [Snippet(line_number=line_no, text=text) for line_no, text in raw_snippets]


def main():
    mcp.run()


if __name__ == "__main__":
    main()
