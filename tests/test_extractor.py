from logdetective_mcp.extractor import get_chunks, new_message


class TestNewMessage:
    def test_regular_line(self):
        assert new_message("ERROR something failed") is True

    def test_whitespace_continuation(self):
        assert new_message("  at module.py:42") is False

    def test_tab_continuation(self):
        assert new_message("\tindented line") is False

    def test_pipe_continuation(self):
        assert new_message("| continued output") is False

    def test_digit_start(self):
        assert new_message("2024-01-15 10:00:00 INFO start") is True


class TestGetChunks:
    def test_single_line(self):
        chunks = list(get_chunks("hello"))
        assert chunks == [(1, "hello")]

    def test_separate_messages(self):
        log = "line one\nline two\nline three"
        chunks = list(get_chunks(log))
        assert len(chunks) == 3
        assert chunks[0] == (1, "line one")
        assert chunks[1] == (2, "line two")
        assert chunks[2] == (3, "line three")

    def test_continuation_lines_grouped(self):
        log = "ERROR something\n  traceback line 1\n  traceback line 2\nINFO next"
        chunks = list(get_chunks(log))
        assert len(chunks) == 2
        assert chunks[0][0] == 1
        assert "traceback line 1" in chunks[0][1]
        assert "traceback line 2" in chunks[0][1]
        assert chunks[1] == (4, "INFO next")

    def test_pipe_continuation_grouped(self):
        log = "header\n| detail 1\n| detail 2"
        chunks = list(get_chunks(log))
        assert len(chunks) == 1
        assert "detail 1" in chunks[0][1]
        assert "detail 2" in chunks[0][1]

    def test_empty_lines_skipped(self):
        log = "line one\n\n\nline two"
        chunks = list(get_chunks(log))
        assert len(chunks) == 2
        assert chunks[0] == (1, "line one")
        assert chunks[1] == (4, "line two")

    def test_chunk_splitting_on_max_len(self):
        long_line = "A" * 50
        chunks = list(get_chunks(long_line, max_chunk_len=20))
        total = "".join(text for _, text in chunks)
        assert total == long_line
        assert all(len(text) <= 20 for _, text in chunks)

    def test_empty_input(self):
        chunks = list(get_chunks(""))
        assert chunks == [(1, "")]
