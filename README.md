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

Returns a list of `Snippet` objects, each with `line_number` (position in the original log) and `text` (the extracted snippet content).

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
