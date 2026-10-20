# Log Detective MCP

MCP server implementing core log analysis tools of Log Detective for use by other agents.

The server uses the Drain3 algorithm to cluster log messages into templates and extract
a representative subset of snippets, reducing large logs to a manageable size for
downstream analysis.

## Installation

Requires Python 3.11+.

```sh
pip install .
```

Or with [uv](https://docs.astral.sh/uv/):

```sh
uv pip install .
```

## Usage

### Running the server

```sh
logdetective-mcp
```

Or directly:

```sh
python -m logdetective_mcp.main
```

### MCP client configuration

#### Claude Code

```sh
claude mcp add logdetective -- logdetective-mcp
```

#### Claude Desktop

Add to your Claude Desktop configuration file:

```json
{
  "mcpServers": {
    "logdetective": {
      "command": "logdetective-mcp"
    }
  }
}
```

## Tools

### `extract_log_snippets`

Extracts representative log snippets using Drain3 clustering. The tool chunks
the log into logical messages, clusters similar messages, and returns one
representative snippet per cluster.

Log content can be provided in three ways (exactly one must be used):

| Parameter | Type | Description |
|---|---|---|
| `log_text` | `str` | Raw log text passed directly. |
| `log_path` | `str` | Path to a log file on the server's filesystem. |
| `log_url` | `str` | HTTP(S) URL to fetch log content from. |

Optional parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_clusters` | `int` | 8 | Maximum number of snippets to extract. |
| `max_snippet_len` | `int` | 2000 | Maximum character length per snippet. |
| `skip_patterns` | `list[str]` | `null` | List of regex patterns. Chunks matching any pattern are excluded before clustering. |
| `context` | `int` | 0 | Number of lines to include before and after each snippet (0-50). |

Returns a list of `Snippet` objects, each with `line_number` (position in the original log) and `text` (the extracted snippet content).

### `extract_python_tracebacks`

Extracts Python tracebacks from the log, including chained exceptions.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_traceback_len` | `int` | 2000 | Maximum character length per extracted traceback. |
| `skip_patterns` | `list[str]` | `null` | List of regex patterns. Tracebacks matching any pattern are excluded. |
| `context` | `int` | 0 | Number of lines to include before and after each traceback (0-50). |

For both extractor tools, `context` follows grep's `-C` behavior: the requested
number of surrounding log lines is included on each side when available. The
length limit applies to the extracted snippet or traceback; context lines are
additional, so the returned `text` can be longer than the limit. With context
enabled, `line_number` points to the first returned line. Values outside 0-50
are rejected.

If a traceback is truncated, the extractor keeps its beginning and end with a
marker when `max_traceback_len` permits; smaller limits keep only a prefix.
Following context starts after the original traceback, without restoring its
omitted middle.
If a log line is split into chunks by `max_snippet_len`, each selected chunk
keeps its own text when context is added. Neighboring lines do not replace it
with the complete long line. Context boundaries follow the chunk's original
source lines, including blank lines omitted from the rendered chunk.

### Size limits

All downloaded inputs (via `log_url`) are limited to 100 MB regardless of format.
Downloads are streamed in chunks and aborted as soon as the limit is exceeded.

Archives read via `log_path` are also checked against the 100 MB limit during decompression.

### Compressed file support

Both tools transparently decompress archived log files. Supported formats:

- **Single-file compression:** `.gz`, `.bz2`, `.xz`, `.lzma`
- **Archives:** `.zip`, `.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`

Format is detected from the file extension (or URL path). Archives must contain exactly one file.

**Zip bomb protection** is enforced via:
- Maximum decompressed size of 100 MB
- Maximum compression ratio of 100:1
- Pre-check of declared sizes in zip/tar headers
- No recursive decompression (nested archives are not unpacked)

## Contributing

All changes to this repository must pass pre-commit checks and tests.

```sh
uv run pytest tests/
```

<!-- mcp-name: io.github.fedora-copr/logdetective-mcp -->
