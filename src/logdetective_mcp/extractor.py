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


class Extractor:
    """Base extractor class"""

    def __init__(
        self, skip_patterns: list[str] | None = None, max_snippet_len: int = 2000
    ) -> None:
        self._skip_patterns: list[re.Pattern] = []

        if skip_patterns:
            for pattern in skip_patterns:
                try:
                    self._skip_patterns.append(re.compile(pattern, re.DOTALL))
                except re.error as exc:
                    raise ValueError(
                        f"Invalid regex in skip_patterns: {pattern!r}: {exc}"
                    ) from exc
        if max_snippet_len < 0:
            raise ValueError(f"`max_snippet_len` set to value {max_snippet_len} < 0")
        self.max_snippet_len: int = max_snippet_len

    def _filter_patterns(self, chunks: list[tuple[int, str]]) -> list[tuple[int, str]]:
        if not self._skip_patterns:
            return chunks
        return [
            (line_no, text)
            for line_no, text in chunks
            if not any(p.search(text) for p in self._skip_patterns)
        ]


class DrainExtractor(Extractor):
    """Extracts representative log snippets using Drain3 template mining."""

    def __init__(
        self,
        max_clusters: int = 8,
        max_snippet_len: int = 2000,
        skip_patterns: list[str] | None = None,
    ):
        super().__init__(skip_patterns=skip_patterns, max_snippet_len=max_snippet_len)

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


class PythonTracebackExtractor(Extractor):
    """Extract Python exception tracebacks from logs using a line-scanning state machine."""

    _TB_START = "Traceback (most recent call last):"
    _CHAIN_CONT = (
        "During handling of the above exception, another exception occurred:",
        "The above exception was the direct cause of the following exception:",
    )
    _TRUNCATE_STR = "\n...<truncated>...\n"

    def __call__(self, log: str) -> list[tuple[int, str]]:
        lines = log.splitlines()
        chunks = []
        current_idx = 0
        while current_idx < len(lines):
            if lines[current_idx].rstrip() == self._TB_START:
                snippet_lines, next_idx = self._collect_traceback(lines, current_idx)
                text = "\n".join(snippet_lines)
                chunks.append((current_idx + 1, text))  # 1-indexed
                current_idx = next_idx
            else:
                current_idx += 1
        filtered_chunks = self._filter_patterns(chunks)
        truncated_chunks = list(map(self._truncate_long_traceback, filtered_chunks))
        return truncated_chunks

    def _truncate_long_traceback(self, snippet: tuple[int, str]) -> tuple[int, str]:
        """Shorten a snippet with text longer than `max_snippet_len`"""
        line_no, text = snippet
        if len(text) <= self.max_snippet_len:
            return snippet
        border = (self.max_snippet_len - len(self._TRUNCATE_STR)) // 2
        if border <= 0:
            return (line_no, text[: self.max_snippet_len])
        return (line_no, f"{text[:border]}{self._TRUNCATE_STR}{text[-border:]}")

    # In the following, by chaining, we mean:
    #   |Traceback ...
    #   |...
    #   |<blank line>
    #   |During handling of the above ...
    #   |<blank line>
    #   |Traceback ...
    #   |...

    # And by frames, we mean file-code references:
    #   |Traceback ...
    #   |  File "module1.py", line 42, in <module>  <- frame
    #   |    foo()
    #   |  File "module2.py" ...  <- another frame
    #   |    bar()
    #   |  ...
    #   |Exception: details of exception

    def _is_frame_line(self, line: str) -> bool:
        """Check if line is an indented traceback frame (File/code reference)."""
        return bool(line.startswith((" ", "\t")) and line.strip())

    def _is_chain_marker(self, line: str) -> bool:
        """Check if line marks a chained exception or new traceback."""
        return bool(line in self._CHAIN_CONT or line == self._TB_START)

    def _find_next_non_blank(self, lines: list[str], start_idx: int) -> int:
        """Find index of next non-blank line. Returns len(lines) if none found."""
        idx = start_idx
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        return idx

    def _has_chain_continuation(self, lines: list[str], from_idx: int) -> int:
        """Check if a chain continuation follows after blank lines.

        Returns:
            Non-negative index of chain marker if found, otherwise -1
        """
        next_non_blank = self._find_next_non_blank(lines, from_idx)
        if next_non_blank < len(lines) and self._is_chain_marker(
            lines[next_non_blank].rstrip()
        ):
            return next_non_blank
        return -1

    def _collect_traceback(
        self, lines: list[str], start_idx: int
    ) -> tuple[list[str], int]:
        """Collect all lines belonging to a traceback, including chained exceptions.

        Handles the state machine for parsing Python tracebacks:
        1. Indented frame lines (File/code references)
        2. Blank lines (may separate chained tracebacks)
        3. Chain continuation markers
        4. Exception type lines (non-indented, non-blank)

        Args:
            lines: All log lines
            start_idx: Index of "Traceback (most recent call last):" line

        Returns:
            Tuple of (collected lines, index after last collected line)
        """
        collected = [lines[start_idx]]
        current_idx = start_idx + 1

        while current_idx < len(lines):
            line = lines[current_idx]
            line = line.rstrip()

            # frame line (File/code reference)
            if self._is_frame_line(line):
                collected.append(line)
                current_idx += 1
                continue

            # blank line -> check if chain continues
            if not line.strip():
                chain_idx = self._has_chain_continuation(lines, current_idx + 1)
                if chain_idx >= 0:
                    current_idx = chain_idx
                    continue
                break

            # chain marker / new traceback
            if self._is_chain_marker(line):
                collected.append(line)
                current_idx += 1
                continue

            # exception type (non-indented, non-blank)
            collected.append(line)
            current_idx += 1

            # check if another exception follows after
            chain_idx = self._has_chain_continuation(lines, current_idx)
            if chain_idx >= 0:
                current_idx = chain_idx
                continue
            break

        return collected, current_idx
