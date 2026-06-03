import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Prompt

sys.path.insert(0, str(Path(__file__).parent))

from agent import build_default_agent

load_dotenv()
console = Console()
DATA_DIR = Path(__file__).parent / "data"
agent = build_default_agent(DATA_DIR)
conversation_id = agent.store.create()


async def main() -> None:
    console.print(
        f"[bold cyan]Muninn online ({agent.model}). Type 'exit' to quit.[/bold cyan]\n"
    )
    while True:
        user_input = Prompt.ask("[bold green]You[/bold green]")
        if user_input.lower() in ["exit", "quit"]:
            console.print("[dim]Muninn signing off.[/dim]")
            break
        async for event in agent.handle_message(user_input, conversation_id):
            if event["type"] == "text":
                console.print(event["content"], end="", highlight=False)
            elif event["type"] == "tool_use":
                console.print(
                    f"\n[dim][tool: {event['name']}][/dim]", end="", highlight=False
                )
            elif event["type"] == "error":
                console.print(f"\n[red]{event['message']}[/red]", highlight=False)
            elif event["type"] == "done":
                console.print()
        console.print()


if __name__ == "__main__":
    asyncio.run(main())
