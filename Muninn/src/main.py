import os
from dotenv import load_dotenv
from anthropic import Anthropic
from rich.console import Console
from rich.prompt import Prompt

load_dotenv()
client = Anthropic()
console = Console()

SYSTEM_PROMPT = """You are Huginn, the first agent of Project Ragnarok.
You are named after one of Odin's two ravens, whose name means "thought."
In Norse mythology, Huginn flies across the world and reports back what he sees.
You are direct, capable, and focused. Keep responses concise."""

messages = []

console.print("[bold cyan]Huginn online. Type 'exit' to quit.[/bold cyan]\n")

while True:
    user_input = Prompt.ask("[bold green]You[/bold green]")
    
    if user_input.lower() in ["exit", "quit"]:
        console.print("[dim]Huginn signing off.[/dim]")
        break
    
    messages.append({"role": "user", "content": user_input})
    
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    
    assistant_text = response.content[0].text
    messages.append({"role": "assistant", "content": assistant_text})
    
    console.print(f"[bold magenta]Huginn[/bold magenta]: {assistant_text}\n")