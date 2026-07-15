from logdetective_mcp.extractor import Extractor, PythonTracebackExtractor


# --- Python traceback examples ---


PYTHON_SIMPLE_TB = """\
Traceback (most recent call last):
  File "/usr/lib/rpm/redhat/pyproject_buildrequires.py", line 721, in main
    generate_requires()
  File "/usr/lib/rpm/redhat/pyproject_buildrequires.py", line 263, in get_backend
    raise FileNotFoundError('File "setup.py" not found for legacy project.')
FileNotFoundError: File "setup.py" not found for legacy project.\
"""


PYTHON_SIMPLE_CHAINED_TB = """\
Traceback (most recent call last):
  File "/usr/bin/tool", line 10, in run
    do_work()
ValueError: inner error

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/usr/bin/tool", line 20, in main
    run()
RuntimeError: outer error\
"""


PYTHON_LONGER_TB = """\
Traceback (most recent call last):
  File "/app/main.py", line 12, in <module>
    app.run()
  File "/app/app.py", line 45, in run
    self.process()
  File "/app/handler.py", line 78, in process
    self.execute()
  File "/app/executor.py", line 120, in execute
    self.validate()
  File "/app/validator.py", line 34, in validate
    raise ValueError("Invalid input")
ValueError: Invalid input\
"""


PYTHON_LONG_CHAIN_TB = """\
Traceback (most recent call last):
  File "/app/level1.py", line 10, in func1
    level2.call()
ValueError: Error at level 1

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/app/level2.py", line 20, in func2
    level3.call()
RuntimeError: Error at level 2

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/level3.py", line 30, in func3
    raise TypeError("Error at level 3")
TypeError: Error at level 3\
"""


# --- Log examples with inserted tracebacks ---


SIMPLE_TRACEBACK_LOG = f"""\
[INFO] Starting job
[INFO] Running step
{PYTHON_SIMPLE_TB}
[ERROR] Build failed
Finish: rpmbuild random-package-2026.1.2.3-4.fc42.src.rpm
Finish: build phase for random-package-2026.1.2.3-4.fc42.src.rpm
"""


CHAINED_TRACEBACK_LOG = f"""\
Mock output
Running: git clone https://copr.org/author/org/product /some/abs/path/to/package --depth 500 --no-single-branch --recursive

cmd: ['git', 'clone', 'https://copr.org/author/org/product', '/some/abs/path/to/package', '--depth', '500', '--no-single-branch', '--recursive']
cwd: .
rc: 0
stdout:
stderr: Cloning into '/some/abs/path/to/package'...
INFO: calling preinit hooks
INFO: enabled root cache
INFO: enabled package manager cache

{PYTHON_SIMPLE_CHAINED_TB}
Installing group/module packages:
 bash                              x86_64 5.2.37-1.fc42              fedora       8.2 MiB
 bzip2                             x86_64 1.0.8-20.fc42              fedora      99.3 KiB
 coreutils                         x86_64 9.6-4.fc42                 updates      5.4 MiB
"""


LONGER_TRACEBACK_LOG = f"""\
Building target platforms: x86_64
Building for target x86_64
warning: %source_date_epoch_from_changelog is set, but %changelog has no entries to take a date from
{PYTHON_LONGER_TB}

Start(bootstrap): cleaning package manager metadata
Finish(bootstrap): cleaning package manager metadata
"""


LONG_CHAIN_TRACEBACK_LOG = f"""\
RPM build warnings:
    %source_date_epoch_from_changelog is set, but %changelog has no entries to take a date from
    absolute symlink: /usr/bin/package -> /usr/share/package

{PYTHON_LONG_CHAIN_TB}

+ RPM_EC=0
++ jobs -p
+ exit 0
"""


class TestExtractorBase:
    def test_init_defaults(self):
        extractor = Extractor()
        assert extractor.max_snippet_len == 2000
        assert extractor._skip_patterns == {}

    def test_init_with_skip_patterns(self):
        extractor = Extractor(skip_patterns={"noise": "DEBUG.*"})
        assert "noise" in extractor._skip_patterns

    def test_init_custom_snippet_len(self):
        extractor = Extractor(max_snippet_len=500)
        assert extractor.max_snippet_len == 500

    def test_filter_patterns_no_patterns(self):
        extractor = Extractor()
        chunks = [(1, "hello"), (2, "world")]
        assert extractor._filter_patterns(chunks) == chunks

    def test_filter_patterns_removes_match(self):
        extractor = Extractor(skip_patterns={"debug": "DEBUG.*"})
        chunks = [(1, "DEBUG noise"), (2, "ERROR real")]
        result = extractor._filter_patterns(chunks)
        assert result == [(2, "ERROR real")]


class TestPythonTracebackExtractor:
    def test_simple_traceback(self):
        extractor = PythonTracebackExtractor()
        result = extractor(PYTHON_SIMPLE_TB)
        assert len(result) == 1
        line_no, text = result[0]
        assert line_no == 1
        assert "FileNotFoundError" in text
        assert "pyproject_buildrequires.py" in text

    def test_no_tracebacks(self):
        extractor = PythonTracebackExtractor()
        result = extractor("INFO all good\nDEBUG nothing here")
        assert result == []

    def test_empty_input(self):
        extractor = PythonTracebackExtractor()
        assert extractor("") == []

    def test_line_numbering_in_log(self):
        """Traceback starting on line 3 should report line_number=3."""
        extractor = PythonTracebackExtractor()
        result = extractor(SIMPLE_TRACEBACK_LOG)
        assert len(result) == 1
        assert result[0][0] == 3

    def test_two_separate_tracebacks(self):
        log = f"INFO first\n{PYTHON_SIMPLE_TB}\nINFO middle\n{PYTHON_LONGER_TB}"
        extractor = PythonTracebackExtractor()
        result = extractor(log)
        assert len(result) == 2
        assert "FileNotFoundError" in result[0][1]
        assert "ValueError: Invalid input" in result[1][1]

    def test_chained_during_handling(self):
        extractor = PythonTracebackExtractor()
        result = extractor(PYTHON_SIMPLE_CHAINED_TB)
        assert len(result) == 1
        text = result[0][1]
        assert "ValueError: inner error" in text
        assert "RuntimeError: outer error" in text
        assert "During handling" in text

    def test_chained_direct_cause(self):
        extractor = PythonTracebackExtractor()
        result = extractor(PYTHON_LONG_CHAIN_TB)
        assert len(result) == 1
        text = result[0][1]
        assert "ValueError: Error at level 1" in text
        assert "RuntimeError: Error at level 2" in text
        assert "TypeError: Error at level 3" in text
        assert "direct cause" in text

    def test_traceback_at_end_of_file(self):
        log = 'INFO start\nTraceback (most recent call last):\n  File "x.py", line 1, in f\n    pass\nRuntimeError: eof'
        extractor = PythonTracebackExtractor()
        result = extractor(log)
        assert len(result) == 1
        assert "RuntimeError: eof" in result[0][1]

    def test_traceback_only_header(self):
        """Traceback header with no frames — degenerate but shouldn't crash."""
        extractor = PythonTracebackExtractor()
        result = extractor("Traceback (most recent call last):\nSomeError: bad")
        assert len(result) == 1
        assert "SomeError" in result[0][1]

    def test_tab_indented_frames(self):
        log = 'Traceback (most recent call last):\n\tFile "x.py", line 1, in f\n\t\tx()\nTypeError: oops'
        extractor = PythonTracebackExtractor()
        result = extractor(log)
        assert len(result) == 1
        assert "TypeError: oops" in result[0][1]

    def test_skip_patterns_filter(self):
        extractor = PythonTracebackExtractor(
            skip_patterns={"fnf": ".*FileNotFoundError.*"}
        )
        result = extractor(PYTHON_SIMPLE_TB)
        assert result == []

    def test_skip_patterns_partial(self):
        log = f"INFO first\n{PYTHON_SIMPLE_TB}\nINFO middle\n{PYTHON_LONGER_TB}"
        extractor = PythonTracebackExtractor(
            skip_patterns={"fnf": ".*FileNotFoundError.*"}
        )
        result = extractor(log)
        assert len(result) == 1
        assert "ValueError: Invalid input" in result[0][1]

    def test_truncation_short_snippet(self):
        extractor = PythonTracebackExtractor(max_snippet_len=50000)
        result = extractor(PYTHON_SIMPLE_TB)
        assert len(result) == 1
        assert "...<truncated>..." not in result[0][1]

    def test_truncation_long_snippet(self):
        extractor = PythonTracebackExtractor(max_snippet_len=50)
        result = extractor(PYTHON_SIMPLE_TB)
        assert len(result) == 1
        text = result[0][1]
        assert "...<truncated>..." in text
        assert len(text) <= 50

    def test_non_traceback_lines_excluded(self):
        extractor = PythonTracebackExtractor()
        result = extractor(SIMPLE_TRACEBACK_LOG)
        text = result[0][1]
        assert "[INFO] Starting job" not in text
        assert "[ERROR] Build failed" not in text

    def test_blank_line_terminates_without_chain(self):
        """A blank line NOT followed by a chain marker ends the traceback."""
        log = 'Traceback (most recent call last):\n  File "x.py", line 1, in f\n    x()\nValueError: v\n\nINFO unrelated'
        extractor = PythonTracebackExtractor()
        result = extractor(log)
        assert len(result) == 1
        assert "unrelated" not in result[0][1]

    def test_chained_traceback_in_log(self):
        extractor = PythonTracebackExtractor()
        result = extractor(CHAINED_TRACEBACK_LOG)
        assert len(result) == 1
        text = result[0][1]
        assert "RuntimeError: outer error" in text
        assert "git clone" not in text

    def test_longer_traceback_in_log(self):
        extractor = PythonTracebackExtractor()
        result = extractor(LONGER_TRACEBACK_LOG)
        assert len(result) == 1
        text = result[0][1]
        assert "ValueError: Invalid input" in text
        assert "Building target platforms" not in text

    def test_long_chain_traceback_in_log(self):
        extractor = PythonTracebackExtractor()
        result = extractor(LONG_CHAIN_TRACEBACK_LOG)
        assert len(result) == 1
        text = result[0][1]
        assert "TypeError: Error at level 3" in text
        assert "RPM build warnings" not in text
