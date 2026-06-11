# Log Detective MCP

MCP server implementing core tools of Log Detective for use by other agents.

## Tools

Included tools use Drain3 algorithm to build log templates and extract subset
of messages, in order to minimize use of context.
Unlike in Log Detective, where much of the tool behavior is determined by agent configuration,
and framework imposed contraints, these tools allow substantially more freedom.

Parameters, such as target number of extracted snippets, excluded patterns and truncation,
have reasonable defaults. However, ultimately are all set by the agent executing the tool.

For now, only base DrainExtractor is included.

## Contributing

All changes to this repository must pass pre-commit checks and tests.
