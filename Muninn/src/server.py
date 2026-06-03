import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import build_default_agent
from memory import MemoryStore
from system import BASE_SYSTEM_PROMPT


load_dotenv()
app = FastAPI()
DATA_DIR = Path(__file__).parent / "data"
store = MemoryStore(DATA_DIR / "muninn_memory.db")
agent = build_default_agent(DATA_DIR, system_prompt=BASE_SYSTEM_PROMPT)

STATIC_DIR = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ResetRequest(BaseModel):
    conversation_id: str | None = None


def sse(event: dict) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


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
    async def stream():
        async for event in agent.handle_message(req.message, req.conversation_id):
            yield sse(event)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/reset")
async def reset(req: ResetRequest):
    """Clear the current conversation (keeps the record on disk) and start a new one."""
    if req.conversation_id and store.exists(req.conversation_id):
        store.clear(req.conversation_id)
    new_cid = store.create()
    return {"conversation_id": new_cid}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
