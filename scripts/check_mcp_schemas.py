"""Check the installed MCP server's advertised tools against saved JSON files.

Run with the project's virtual-environment Python. Use --write to refresh the
files after an intentional change to the tool definitions.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
TOOL_NAMES = {"extract_log_snippets", "extract_python_tracebacks"}
SOURCE_FIELDS = ("log_text", "log_path", "log_url")


async def check_tool(session: ClientSession, tool) -> None:
    """Validate the advertised schemas and a real structured tool result."""
    Draft202012Validator.check_schema(tool.inputSchema)
    Draft202012Validator.check_schema(tool.outputSchema)

    input_validator = Draft202012Validator(tool.inputSchema)
    for field in SOURCE_FIELDS:
        assert input_validator.is_valid({field: "nonempty"}), (tool.name, field)
    assert input_validator.is_valid({"log_text": "ERROR failed", "log_path": ""})
    for arguments in (
        {},
        {"log_text": ""},
        {"log_text": "x", "log_path": "y"},
    ):
        assert not input_validator.is_valid(arguments), (tool.name, arguments)

    result = await asyncio.wait_for(
        session.call_tool(tool.name, {"log_text": "ERROR failed"}), timeout=10
    )
    assert not result.isError, (tool.name, result)
    Draft202012Validator(tool.outputSchema).validate(result.structuredContent)


async def run(write: bool) -> None:
    executable = Path(sys.executable).with_name("logdetective-mcp")
    if not executable.is_file():
        raise FileNotFoundError(
            f"{executable} is missing; install the project in this virtual environment"
        )

    server = StdioServerParameters(command=str(executable), cwd=ROOT)
    async with stdio_client(server) as (read, send):
        async with ClientSession(read, send) as session:
            await asyncio.wait_for(session.initialize(), timeout=10)
            tools = (await asyncio.wait_for(session.list_tools(), timeout=10)).tools
            names = {tool.name for tool in tools}
            assert names == TOOL_NAMES, f"Unexpected tools: {sorted(names)}"

            for tool in tools:
                await check_tool(session, tool)
                path = SCHEMA_DIR / f"{tool.name}.json"
                definition = tool.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
                if write:
                    SCHEMA_DIR.mkdir(exist_ok=True)
                    path.write_text(json.dumps(definition, indent=2) + "\n")
                    print(f"Wrote {path.relative_to(ROOT)}")
                else:
                    if json.loads(path.read_text()) != definition:
                        raise AssertionError(
                            f"{path.relative_to(ROOT)} differs from tools/list; "
                            "run this script with --write to refresh it"
                        )
                    print(f"Valid: {path.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="save current tools/list definitions"
    )
    args = parser.parse_args()
    asyncio.run(run(args.write))


if __name__ == "__main__":
    main()
