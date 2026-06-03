"""Memory tool definitions (Anthropic schema format, registered in ALL_TOOLS).

These three tools are handled by MuninnAgent (not by the stateless
tools.execute_tool dispatcher) because they need access to the
MemoryStore instance and the current conversation id. The agent's
_execute_tool method routes recall/remember/forget here.
"""

from __future__ import annotations

import json


RECALL_TOOL = {
    "name": "recall",
    "description": (
        "Search Muninn's long-term memory and past conversations for "
        "context relevant to the current task. Searches both stored "
        "memories (user preferences, project facts, standing instructions) "
        "and the text of past messages across every conversation. Use this "
        "when the user references past discussions, asks you to remember "
        "something, or when prior context would help answer the current "
        "question. Returns matching memories and message snippets with "
        "their source conversation id -- you can then read the snippet to "
        "decide whether the full conversation is worth pulling up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords or short phrase to search for.",
            },
            "conversations_limit": {
                "type": "integer",
                "description": "Max past-message matches to return (default 5).",
            },
            "memories_limit": {
                "type": "integer",
                "description": "Max stored-memory matches to return (default 5).",
            },
        },
        "required": ["query"],
    },
}


REMEMBER_TOOL = {
    "name": "remember",
    "description": (
        "Store a fact, preference, project detail, or standing instruction "
        "in Muninn's long-term memory. The next time the user asks about "
        "this topic the relevant context will be available. Use this when "
        "the user shares a preference, mentions a project detail, gives a "
        "standing rule, or asks you to remember something. Choose a "
        "category that best describes the memory: 'preference' (likes/"
        "dislikes), 'fact' (objective info about the user), 'project' "
        "(ongoing work context), 'instruction' (standing orders), or "
        "'context' (situational, may go stale)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact or context to remember. Be concise and self-contained so it makes sense out of context later.",
            },
            "category": {
                "type": "string",
                "enum": [
                    "preference",
                    "fact",
                    "project",
                    "instruction",
                    "context",
                ],
                "description": "What kind of memory this is.",
            },
            "importance": {
                "type": "number",
                "description": "How important or evergreen is this on a 0.0-1.0 scale. Default 0.5. Use higher for standing rules, lower for transient context.",
            },
        },
        "required": ["content", "category"],
    },
}


FORGET_TOOL = {
    "name": "forget",
    "description": (
        "Remove a stored memory by its numeric id. Use this when the user "
        "asks to forget something, or when a memory turns out to be wrong "
        "or outdated. Memory ids come from the 'recall' tool's output."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "The id of the memory to delete.",
            },
        },
        "required": ["memory_id"],
    },
}


def run_recall(store, query: str, conversations_limit: int = 5, memories_limit: int = 5) -> str:
    return json.dumps(
        store.search(
            query,
            conversations_limit=conversations_limit,
            memories_limit=memories_limit,
        ),
        indent=2,
        ensure_ascii=False,
    )


def run_remember(
    store,
    content: str,
    category: str,
    importance: float = 0.5,
    source_conversation: str | None = None,
) -> str:
    mid = store.add_memory(
        content=content,
        category=category,
        importance=importance,
        source_conversation=source_conversation,
    )
    return json.dumps({"id": mid, "stored": True, "category": category})


def run_forget(store, memory_id: int) -> str:
    ok = store.delete_memory(memory_id)
    return json.dumps({"deleted": ok, "id": memory_id})
