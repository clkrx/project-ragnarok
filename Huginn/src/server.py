import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


import anthropic
from anthropic import APIConnectionError, APIStatusError
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from storage import ConversationStore
from tools import ALL_TOOLS, execute_tool


load_dotenv()
client = anthropic.Anthropic()
app = FastAPI()
store = ConversationStore(Path(__file__).parent / "data" / "conversations.json")

SYSTEM_PROMPT = """You are Huginn, the first agent of Project Ragnarok.
You are named after one of Odin's two ravens, whose name means "thought."
In Norse mythology, Huginn flies across the world and reports back what he sees.

You run on the user's Windows 11 system and have access to these tools:
- powershell: run Windows PowerShell commands (NOT bash) for system tasks
- file: read, write, list, and manage files and folders
- web_search: search the web via Serper.dev for current information
- fetch_page: read the full text of a web page by URL

Use them freely when needed to answer questions, inspect the system, or
accomplish tasks. Remember this is Windows: use PowerShell syntax, backslash
paths, and prefer the 'file' tool over shell commands for file operations.
Be direct and concise. Show your work briefly when using tools."""

TOOLS = ALL_TOOLS
MAX_ITERATIONS = 25

STATIC_DIR = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ResetRequest(BaseModel):
    conversation_id: str | None = None


def sse(event_type: str, data: dict) -> bytes:
    """Format a server-sent event."""
    payload = json.dumps({"type": event_type, **data})
    return f"data: {payload}\n\n".encode()


def content_to_dicts(blocks) -> list[dict]:
    """Convert Anthropic content blocks to plain dicts for JSON storage."""
    result = []
    for b in blocks:
        if b.type == "text":
            result.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": b.id,
                "name": b.name,
                "input": dict(b.input) if b.input else {},
            })
    return result


def resolve_conversation(cid: str | None) -> tuple[str, list[dict]]:
    """Return (cid, messages). Creates a new conversation if needed."""
    if cid and store.exists(cid):
        existing = store.get(cid) or []
        return cid, existing
    new_cid = store.create()
    return new_cid, []


def append_message(cid: str, messages: list[dict], message: dict) -> None:
    """Mirror a message into both the live list and the on-disk store."""
    messages.append(message)
    store.append(cid, message)


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, APIStatusError):
        code = exc.status_code
        if code in (429, 529):
            return "Claude is overloaded right now. Please try again in a moment."
        if code in (500, 502, 503, 504):
            return "The Anthropic API had a temporary error. Please try again."
        if code == 400:
            return f"Request rejected by the API: {exc.message}"
        if code == 401:
            return "Invalid Anthropic API key. Check your .env file."
        return f"API error ({code}): {exc.message}"
    if isinstance(exc, APIConnectionError):
        return "Could not reach the Anthropic API. Check your network connection."
    return f"{type(exc).__name__}: {exc}"


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/history")
async def history(conversation_id: str):
    """Return the stored messages for a conversation, or an empty list."""
    messages = store.get(conversation_id)
    if messages is None:
        return {"conversation_id": conversation_id, "messages": []}
    return {"conversation_id": conversation_id, "messages": messages}


@app.post("/chat")
async def chat(req: ChatRequest):
    cid, conversation = resolve_conversation(req.conversation_id)

    def stream():
        try:
            append_message(cid, conversation, {"role": "user", "content": req.message})

            for _ in range(MAX_ITERATIONS):
                with client.messages.stream(
                    model="claude-sonnet-4-5",
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=conversation,
                ) as response_stream:
                    for delta in response_stream.text_stream:
                        if delta:
                            yield sse("text", {"content": delta})

                    final_message = response_stream.get_final_message()

                assistant_content = content_to_dicts(final_message.content)
                append_message(
                    cid,
                    conversation,
                    {"role": "assistant", "content": assistant_content},
                )

                tool_uses = [b for b in final_message.content if b.type == "tool_use"]

                for tool_use in tool_uses:
                    yield sse("tool_use", {
                        "name": tool_use.name,
                        "input": tool_use.input,
                    })

                if final_message.stop_reason != "tool_use":
                    break

                tool_results = []
                for tool_use in tool_uses:
                    result = execute_tool(tool_use.name, tool_use.input)
                    yield sse("tool_result", {
                        "name": tool_use.name,
                        "result": result,
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result,
                    })

                append_message(
                    cid,
                    conversation,
                    {"role": "user", "content": tool_results},
                )

        except Exception as e:
            yield sse("error", {"message": friendly_error(e)})

        yield sse("done", {"conversation_id": cid})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/reset")
async def reset(req: ResetRequest):
    """Clear the current conversation (keeps the record on disk) and start a new one."""
    if req.conversation_id and store.exists(req.conversation_id):
        store.clear(req.conversation_id)
    new_cid = store.create()
    return {"conversation_id": new_cid}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
