import os
import re
from collections.abc import Generator

from drain3.template_miner import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig


def new_message(text: str) -> bool:
    """Determine whether a line starts a new log message.

    Returns False if the first character is whitespace or '|',
    indicating a continuation of the previous message.
    """
    if text[0].isspace():
        return False
    if text[0] == "|":
        return False
    return True


def get_chunks(
    text: str, max_chunk_len: int = 2000
) -> Generator[tuple[int, str], None, None]:
    """Split log into chunks based on line-continuation heuristics.

    Lines starting with whitespace or '|' are treated as continuations
    of the previous message. Chunks exceeding max_chunk_len are split.
    """
    lines = text.splitlines()

    chunk = ""
    original_line = 1
    for i, line in enumerate(lines, start=1):
        if len(line) == 0:
            continue
        if new_message(line):
            if len(chunk) > 0:
                yield (original_line, chunk)
            original_line = i
            chunk = line
        else:
            chunk += "\n" + line
        if len(chunk) > max_chunk_len:
            while len(chunk) > max_chunk_len:
                remainder = chunk[max_chunk_len:]
                chunk = chunk[:max_chunk_len]
                yield (original_line, chunk)
                chunk = remainder

    yield (original_line, chunk)


class DrainExtractor:
    """Extracts representative log snippets using Drain3 template mining."""

    def __init__(
        self,
        max_clusters: int = 8,
        max_snippet_len: int = 2000,
        skip_patterns: dict[str, str] | None = None,
    ):
        self.max_snippet_len = max_snippet_len
        self._skip_patterns: dict[str, re.Pattern] | None = None

        if skip_patterns:
            self._skip_patterns = {
                name: re.compile(pattern, re.DOTALL)
                for name, pattern in skip_patterns.items()
            }

        config = TemplateMinerConfig()
        config.load(os.path.join(os.path.dirname(__file__), "drain3.ini"))
        config.drain_max_clusters = max_clusters
        self.miner = TemplateMiner(config=config)

    def __call__(self, log: str) -> list[tuple[int, str]]:
        if not log or not log.strip():
            return []

        chunks = list(get_chunks(log, self.max_snippet_len))
        chunks = self._filter_patterns(chunks)

        self._create_clusters(chunks)
        return self._extract_messages(chunks)

    def _filter_patterns(self, chunks: list[tuple[int, str]]) -> list[tuple[int, str]]:
        if not self._skip_patterns:
            return chunks
        return [
            (line_no, text)
            for line_no, text in chunks
            if not any(p.match(text) for p in self._skip_patterns.values())
        ]

    def _create_clusters(self, chunks: list[tuple[int, str]]) -> None:
        for _, chunk in chunks:
            self.miner.add_log_message(chunk)
        self._clusters = list(self.miner.drain.clusters)

    def _extract_messages(self, chunks: list[tuple[int, str]]) -> list[tuple[int, str]]:
        out = []
        for chunk_start, chunk in chunks:
            cluster = self.miner.match(chunk, "always")
            if cluster in self._clusters:
                out.append((chunk_start, chunk))
                self._clusters.remove(cluster)
        return out
