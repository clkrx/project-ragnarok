"""MuninnAgent -- transport-agnostic chat loop on an OpenAI-compatible API.

Talks to OpenRouter (or any OpenAI-compatible endpoint) via the openai SDK.
Conversation history is stored on disk in a SQLite MemoryStore (see
memory.py); _to_openai_messages() handles the boundary conversion at
request time, so tool definitions (which are authored in Anthropic format)
don't need a parallel schema.

Before each turn the agent optionally runs a keyword search against the
user's message and injects the top matches as a [Relevant prior context]
block. The model can also call recall/remember/forget explicitly via the
tool list.

Event shape yielded by handle_message():
    {"type": "text",         "content": <str>}
    {"type": "tool_use",     "name": <str>, "input": <dict>}
    {"type": "tool_result",  "name": <str>, "result": <str>}
    {"type": "error",        "message": <str>}
    {"type": "done",         "conversation_id": <str>}

Required env:
- OPENROUTER_API_KEY
Optional env:
- OPENROUTER_MODEL (default: qwen/qwen3.7-max)
- OPENROUTER_BASE_URL (default: https://openrouter.ai/api/v1)
"""

from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from memory import MemoryStore
from system import BASE_SYSTEM_PROMPT
from tools import ALL_TOOLS, execute_tool
from tools.memory import run_forget, run_recall, run_remember

load_dotenv()

log = logging.getLogger(__name__)


DEFAULT_MODEL = "qwen/qwen3.7-max"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


_current_cid_var: ContextVar[str | None] = ContextVar(
    "muninn_current_cid", default=None
)


PreToolHook = Callable[[str, dict], Awaitable[bool]]


class MuninnAgent:
    def __init__(
        self,
        store: MemoryStore,
        system_prompt: str = BASE_SYSTEM_PROMPT,
        model: str | None = None,
        max_tokens: int = 4096,
        max_iterations: int = 25,
        tools: list[dict] | None = None,
        auto_recall: bool = True,
        auto_recall_limit: int = 3,
    ) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set in the environment.")
        base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.store = store
        self.system_prompt = system_prompt
        self.model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.tools = tools if tools is not None else list(ALL_TOOLS)
        self.auto_recall = auto_recall
        self.auto_recall_limit = auto_recall_limit

    async def handle_message(
        self,
        text: str,
        conversation_id: str | None = None,
        *,
        pre_tool_hook: PreToolHook | None = None,
    ) -> AsyncIterator[dict]:
        cid, conversation = self._resolve_conversation(conversation_id)
        cid_token = _current_cid_var.set(cid)
        try:
            yield from self._run_turn(text, cid, conversation, pre_tool_hook)
        finally:
            _current_cid_var.reset(cid_token)

    def _run_turn(
        self,
        text: str,
        cid: str,
        conversation: list[dict],
        pre_tool_hook: PreToolHook | None,
    ) -> AsyncIterator[dict]:
        return self._run_turn_impl(text, cid, conversation, pre_tool_hook)

    async def _run_turn_impl(
        self,
        text: str,
        cid: str,
        conversation: list[dict],
        pre_tool_hook: PreToolHook | None,
    ) -> AsyncIterator[dict]:
        self._append_message(cid, conversation, {"role": "user", "content": text})

        if self.auto_recall:
            context_block = self._format_recall(text)
            if context_block:
                self._append_message(
                    cid,
                    conversation,
                    {
                        "role": "user",
                        "content": f"[Relevant prior context from memory]\n{context_block}",
                    },
                )

        try:
            for _ in range(self.max_iterations):
                openai_messages = self._to_openai_messages(conversation)
                openai_tools = [self._to_openai_tool(t) for t in self.tools]

                stream = await self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=openai_messages,
                    tools=openai_tools,
                    stream=True,
                )

                text_content = ""
                tool_calls: list[dict] = []

                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        text_content += delta.content
                        yield {"type": "text", "content": delta.content}
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index or 0
                            while len(tool_calls) <= idx:
                                tool_calls.append(
                                    {"id": "", "name": "", "arguments": ""}
                                )
                            if tc.id:
                                tool_calls[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls[idx]["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls[idx]["arguments"] += tc.function.arguments

                assistant_content: list[dict] = []
                if text_content:
                    assistant_content.append({"type": "text", "text": text_content})
                for tc in tool_calls:
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": self._parse_args(tc["arguments"]),
                        }
                    )
                self._append_message(
                    cid,
                    conversation,
                    {"role": "assistant", "content": assistant_content},
                )

                for tc in tool_calls:
                    yield {
                        "type": "tool_use",
                        "name": tc["name"],
                        "input": self._parse_args(tc["arguments"]),
                    }

                if not tool_calls:
                    break

                storage_blocks: list[dict] = []
                for tc in tool_calls:
                    parsed = self._parse_args(tc["arguments"])
                    proceed = True
                    if pre_tool_hook is not None:
                        try:
                            proceed = await pre_tool_hook(tc["name"], parsed)
                        except Exception as e:
                            log.warning(
                                "pre_tool_hook raised for %s: %s; denying",
                                tc["name"],
                                e,
                            )
                            proceed = False
                    if not proceed:
                        result = json.dumps(
                            {"error": "User denied this action."}
                        )
                    else:
                        result = self._execute_tool(tc["name"], parsed)
                    log.info(
                        "tool_call name=%s proceed=%s", tc["name"], proceed
                    )
                    yield {
                        "type": "tool_result",
                        "name": tc["name"],
                        "result": result,
                    }
                    storage_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": result,
                        }
                    )
                self._append_message(
                    cid,
                    conversation,
                    {"role": "user", "content": storage_blocks},
                )
        except Exception as e:
            yield {"type": "error", "message": self._friendly_error(e)}

        yield {"type": "done", "conversation_id": cid}

    def _execute_tool(self, name: str, tool_input: dict) -> str:
        try:
            if name == "recall":
                return run_recall(
                    self.store,
                    query=tool_input.get("query", ""),
                    conversations_limit=tool_input.get("conversations_limit", 5),
                    memories_limit=tool_input.get("memories_limit", 5),
                )
            if name == "remember":
                return run_remember(
                    self.store,
                    content=tool_input.get("content", ""),
                    category=tool_input.get("category", "context"),
                    importance=tool_input.get("importance", 0.5),
                    source_conversation=_current_cid_var.get(),
                )
            if name == "forget":
                return run_forget(self.store, int(tool_input.get("memory_id", 0)))
            return execute_tool(name, tool_input)
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    def _format_recall(self, query: str) -> str:
        results = self.store.search(
            query,
            conversations_limit=self.auto_recall_limit,
            memories_limit=self.auto_recall_limit,
        )
        sections: list[str] = []
        if results["memories"]:
            sections.append("Stored memories:")
            for m in results["memories"]:
                tag = f" [{m['category']}]" if m.get("category") else ""
                sections.append(
                    f"- (memory id={m['id']}){tag} importance={m.get('importance', 0.5):.2f}: {m['content']}"
                )
        if results["conversations"]:
            sections.append("Relevant past messages:")
            for c in results["conversations"]:
                ts = c.get("created_at") or ""
                sections.append(
                    f"- (conversation {c['conversation_id']}, {c['role']}, {ts}) {c['snippet']}"
                )
        return "\n".join(sections)

    def _to_openai_tool(self, tool_def: dict) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool_def["name"],
                "description": tool_def["description"],
                "parameters": tool_def["input_schema"],
            },
        }

    def _to_openai_messages(self, conversation: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": self.system_prompt}]
        for msg in conversation:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            if not isinstance(content, list):
                continue
            if role == "user" and content and all(
                b.get("type") == "tool_result" for b in content
            ):
                for block in content:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"],
                        }
                    )
                continue
            if role == "assistant":
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        tool_calls.append(
                            {
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block["input"]),
                                },
                            }
                        )
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": "".join(text_parts) or None,
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                out.append(assistant_msg)
        return out

    def _resolve_conversation(self, cid: str | None) -> tuple[str, list[dict]]:
        if cid is None:
            return self.store.create(), []
        self.store.get_or_create(cid)
        return cid, self.store.get(cid) or []

    def _append_message(
        self,
        cid: str,
        messages: list[dict],
        message: dict,
    ) -> None:
        messages.append(copy.deepcopy(message))
        self.store.append(cid, message)

    @staticmethod
    def _parse_args(arguments: str) -> dict:
        if not arguments:
            return {}
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        msg = str(exc)
        name = type(exc).__name__
        try:
            from openai import (
                APIConnectionError,
                APIStatusError,
                AuthenticationError,
            )

            if isinstance(exc, AuthenticationError):
                return "Invalid OpenRouter API key. Check OPENROUTER_API_KEY in your .env."
            if isinstance(exc, APIStatusError):
                code = getattr(exc, "status_code", None)
                if code in (429, 529):
                    return "Model is overloaded right now. Please try again in a moment."
                if code in (500, 502, 503, 504):
                    return "The upstream API had a temporary error. Please try again."
                if code == 401:
                    return "Invalid API key. Check your .env."
                if code == 402:
                    return "OpenRouter account is out of credits. Top up at openrouter.ai."
                if code == 404:
                    return f"Model not found on OpenRouter. Check OPENROUTER_MODEL. ({msg})"
                return f"API error ({code}): {msg}"
            if isinstance(exc, APIConnectionError):
                return "Could not reach OpenRouter. Check your network connection."
        except ImportError:
            pass
        return f"{name}: {msg}"


def build_default_agent(
    data_dir: Path,
    system_prompt: str = BASE_SYSTEM_PROMPT,
) -> MuninnAgent:
    return MuninnAgent(
        store=MemoryStore(data_dir / "muninn_memory.db"),
        system_prompt=system_prompt,
    )
