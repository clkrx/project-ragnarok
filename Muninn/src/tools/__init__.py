"""Tool registry for Muninn.

Each tool module exposes one or more tool definitions (the schema Claude sees)
and a handler function. This module collects them into ALL_TOOLS and provides
a single execute_tool() dispatcher so the server doesn't need to know the details
of any individual tool.

Memory tools (recall/remember/forget) are registered in ALL_TOOLS so the
model sees them, but they are NOT dispatched here -- they need access to
the MemoryStore instance, which the agent handles in _execute_tool().
"""

import json

from .browser import (
    FETCH_PAGE_TOOL,
    WEB_SEARCH_TOOL,
    fetch_page,
    web_search,
)
from .files import FILE_TOOL, run_file
from .memory import FORGET_TOOL, RECALL_TOOL, REMEMBER_TOOL
from .powershell import POWERSHELL_TOOL, run_powershell

ALL_TOOLS = [
    POWERSHELL_TOOL,
    FILE_TOOL,
    WEB_SEARCH_TOOL,
    FETCH_PAGE_TOOL,
    RECALL_TOOL,
    REMEMBER_TOOL,
    FORGET_TOOL,
]


_MEMORY_TOOLS = {"recall", "remember", "forget"}


def execute_tool(name: str, tool_input: dict) -> str:
    """Run a stateless tool by name and return its result as a JSON string."""
    if name in _MEMORY_TOOLS:
        return json.dumps(
            {"error": f"{name} is handled by the agent, not the tool dispatcher."}
        )
    try:
        if name == "powershell":
            result = run_powershell(tool_input["command"])
        elif name == "file":
            result = run_file(
                operation=tool_input["operation"],
                path=tool_input["path"],
                content=tool_input.get("content", ""),
                destination=tool_input.get("destination", ""),
            )
        elif name == "web_search":
            result = web_search(
                tool_input["query"],
                tool_input.get("num_results", 5),
            )
        elif name == "fetch_page":
            result = fetch_page(tool_input["url"])
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}

    return json.dumps(result)
