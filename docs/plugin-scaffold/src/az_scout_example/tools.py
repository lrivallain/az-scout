"""Example MCP tools for the plugin."""


def example_tool(name: str) -> str:
    """Greet someone by name. This tool is exposed via the MCP server."""
    return f"Hello, {name}! This is the example plugin tool."
