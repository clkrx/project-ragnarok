"""Discord entry point for Muninn.

Wires MuninnAgent + DiscordPlatform together. Streams agent events into
the reply target via DiscordStreamer: text deltas edit a single message
in place; the bot reacts to the source message with 👀 while processing
and removes the reaction on completion. Errors are appended to the
streamed message (or sent as a standalone message if streaming is
unavailable).

Run with:
    python -m discord_bot
or, from the src/ directory:
    python discord_bot.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from agent import build_default_agent
from platforms.base import IncomingMessage
from platforms.discord import DiscordPlatform, DiscordStreamer
from system import get_system_prompt


THINKING_EMOJI = "\U0001f440"


async def _make_handler(agent, platform: DiscordPlatform):
    async def handle(incoming: IncomingMessage) -> None:
        try:
            await platform.add_reaction(incoming.message_id, THINKING_EMOJI)
        except Exception:
            pass

        streamer = DiscordStreamer(
            platform, incoming.reply_target, reply_to=incoming.message_id
        )
        await streamer.start()
        use_streamer = streamer.is_active
        fallback: list[str] = []

        try:
            async for event in agent.handle_message(
                incoming.text,
                incoming.conversation_id,
            ):
                if event["type"] == "text":
                    if use_streamer:
                        await streamer.feed(event["content"])
                    else:
                        fallback.append(event["content"])
                elif event["type"] == "error":
                    msg = f"\n\n:warning: {event['message']}"
                    if use_streamer:
                        await streamer.feed(msg)
                    else:
                        fallback.append(msg)
                elif event["type"] == "done":
                    if use_streamer:
                        await streamer.close()
                    else:
                        body = "".join(fallback).strip() or "*(no response)*"
                        await platform.send_message(incoming.reply_target, body)
        except Exception as e:
            err = f":warning: {type(e).__name__}: {e}"
            if use_streamer:
                try:
                    await streamer.feed(f"\n\n{err}")
                finally:
                    await streamer.close()
            else:
                tail = "".join(fallback).strip()
                body = f"{tail}\n\n{err}" if tail else err
                await platform.send_message(incoming.reply_target, body)
        finally:
            try:
                await platform.remove_reaction(incoming.message_id, THINKING_EMOJI)
            except Exception:
                pass

    return handle


async def main() -> None:
    load_dotenv()
    data_dir = Path(__file__).parent / "data"
    agent = build_default_agent(data_dir, system_prompt=get_system_prompt("discord"))
    platform = DiscordPlatform()
    platform.on_message(await _make_handler(agent, platform))
    try:
        await platform.start()
    finally:
        await platform.stop()


if __name__ == "__main__":
    asyncio.run(main())
