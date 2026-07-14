# Huginn

Huginn is the first agent in **Project Ragnarok**, a Norse-themed set of AI agents. Named after one of Odin's ravens ("thought"), Huginn is a local agent that runs on your Windows machine: it can execute PowerShell commands, manage files, search the web, and read web pages, all through a streaming chat interface in your browser.

Built with FastAPI, the Anthropic Claude API, and server-sent events. No frameworks, no LangChain — the agent loop is ~200 lines you can actually read.

![Huginn UI]

## What it can do

| Tool | Description |
|---|---|
| `powershell` | Run Windows PowerShell commands (with a safety layer, see below) |
| `file` | Read, write, list, and move files and folders |
| `web_search` | Search the web via the Serper.dev Google Search API |
| `fetch_page` | Fetch a URL and return its readable text |

Conversations stream token-by-token over SSE and are persisted to a local JSON store, so history survives a server restart.

## Requirements

- Windows 11 (the PowerShell tool assumes Windows; the rest is cross-platform)
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- An [Anthropic API key](https://console.anthropic.com/settings/keys)
- A [Serper.dev API key](https://serper.dev/api-key) (free tier is fine)

## Setup

```powershell
# 1. Clone the repo and enter the Huginn directory
git clone https://github.com/clkrx/project-ragnarok.git
cd project-ragnarok/Huginn

# 2. Create your environment file
Copy-Item .env.example .env
# then open .env and paste in your two API keys

# 3. Install dependencies
uv sync
```

<details>
<summary>Using pip instead of uv</summary>

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```
</details>

## Running Huginn

```powershell
uv run uvicorn server:app --app-dir src --port 8000
```

Then open **http://localhost:8000** in your browser. You should see the Huginn chat UI. Type a message and watch it stream.

Quick smoke test that the tools work:

- *"What's the current weather in Chicago?"* → should trigger `web_search`
- *"How much free space is on my C drive?"* → should trigger `powershell`
- *"Create a file called test.txt on my desktop with 'hello' in it"* → should trigger `file`

## PowerShell safety layer

The PowerShell tool checks every command against a denylist **before** execution and refuses anything catastrophic:

- Recursive deletes aimed at a drive root, user profile, or system directory
- Raw writes to physical disks (`\\.\PhysicalDrive...`)
- Disk formatting, partitioning, and `diskpart`
- Privilege escalation (`sudo`, `runas`, `Start-Process -Verb RunAs`)
- Fork bombs and runaway process spawners
- Piping downloaded scripts straight into a shell (`iwr ... | iex`)

Blocked commands return a clear "BLOCKED by safety layer" message to the agent instead of executing or crashing the loop.

**This is a guardrail against accidents, not a security sandbox.** Commands run with your user's permissions, and a determined attacker could bypass the regex patterns. Don't expose Huginn to the open internet.

## Architecture

```
Huginn/
├── src/
│   ├── server.py        # FastAPI app: agent loop, SSE streaming, endpoints
│   ├── storage.py       # Thread-safe JSON conversation persistence
│   ├── tools/
│   │   ├── __init__.py  # Tool registry + dispatcher
│   │   ├── powershell.py# PowerShell execution + safety denylist
│   │   ├── files.py     # File operations
│   │   └── browser.py   # Serper web search + page fetching
│   └── static/          # Chat UI (vanilla HTML/JS, dark theme)
├── .env.example         # Template for required API keys
├── pyproject.toml       # Dependencies
└── uv.lock              # Pinned dependency versions
```

The agent loop: the server sends your message plus the tool definitions to Claude, streams text back as it generates, executes any tool calls locally, feeds the results back to the model, and repeats until Claude finishes (capped at 25 iterations per turn).

### API endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the chat UI |
| `/chat` | POST | Send a message; responds with an SSE stream |
| `/history` | GET | Fetch a conversation's messages |
| `/reset` | POST | Start a new conversation |

## Roadmap

Huginn is V1 and intentionally minimal. Planned future work under Project Ragnarok, in rough order:

- **Muninn** — Huginn's counterpart ("memory"): persistent memory, remote access, and a Discord interface
- **V3** — voice control, optional local models for cheap/fast tasks, and multi-agent orchestration

None of that lives in this codebase yet, on purpose.

## License

See [LICENSE](../LICENSE) in the repo root.