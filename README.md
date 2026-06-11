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
| `skip_patterns` | `dict[str, str]` | `null` | Map of names to regex patterns. Matching chunks are excluded before clustering. |

Returns a list of `Snippet` objects, each with `line_number` (position in the original log) and `text` (the extracted snippet content).

## Contributing

All changes to this repository must pass pre-commit checks and tests.

```sh
uv run pytest tests/
```
