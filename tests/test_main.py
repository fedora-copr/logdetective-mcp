import pytest

from logdetective_mcp.main import Snippet, _read_log_source, extract_log_snippets


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
        assert extract_log_snippets(log_text="") == []

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
        result = extract_log_snippets(log_text=log, skip_patterns={"debug": "DEBUG.*"})
        texts = [s.text for s in result]
        assert not any("DEBUG" in t for t in texts)
        assert any("ERROR" in t for t in texts)

    def test_from_file(self, tmp_path):
        f = tmp_path / "build.log"
        f.write_text("ERROR build failed\nWARN low disk")
        result = extract_log_snippets(log_path=str(f))
        assert len(result) == 2
