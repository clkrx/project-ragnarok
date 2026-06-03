"""Discord transport for Muninn.

Behavior:
- Ignores messages from anyone other than DISCORD_ALLOWED_USER_ID.
- Opens a private thread off the source message for the first reply; all
  follow-up messages in that thread reuse the same conversation key.
- In DMs, replies directly (no thread API).
- Skips empty / attachment-only messages and its own messages.
- Reacts to the source message with 👀 while the agent is processing,
  removes the reaction on completion. Streams the response into a single
  message that is edited in place as text deltas arrive (DiscordStreamer);
  falls back to a single end-of-turn send if streaming is unavailable.

Voice and screen-share are declared as supported (`supports_voice=True`,
`supports_screen_share=True`) so the agent's tool registry can
conditionally expose them in Phase 2/3, but the actual methods raise
NotImplementedError until those phases land.

Required env vars:
- DISCORD_BOT_TOKEN
- DISCORD_ALLOWED_USER_ID
- OPENROUTER_API_KEY (loaded by MuninnAgent)
"""

from __future__ import annotations

import os
import time
from typing import cast

import discord

from .base import IncomingCallback, IncomingMessage, Platform


def _chunk(text: str, size: int = 1900) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= size:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, size)
        if cut == -1 or cut < size // 2:
            cut = rest.rfind(" ", 0, size)
        if cut == -1 or cut < size // 2:
            cut = size
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return chunks


class DiscordStreamer:
    """Sends a placeholder message and edits it as text deltas arrive.

    On overflow past MAX_CHARS, the current message is locked at a clean
    word-boundary cut and a new continuation message is sent. The user
    sees a chain of edited messages filling up as the response streams.

    Mid-stream cuts are at word boundaries with no ellipsis (a
    continuation message will follow). Only the final close() trims the
    last message with "..." if the response itself exceeds the buffer.

    Edits are throttled to EDIT_INTERVAL seconds to stay well under
    Discord's per-channel edit rate limit. close() always performs a
    final unthrottled flush.
    """

    EDIT_INTERVAL = 1.5
    MAX_CHARS = 1900
    INITIAL_TEXT = "..."

    def __init__(
        self,
        platform: "DiscordPlatform",
        channel_id: str,
        *,
        reply_to: str | None = None,
    ) -> None:
        self.platform = platform
        self.channel_id = channel_id
        self.reply_to = reply_to
        self._buffer = ""
        self._last_edit = 0.0
        self._current_message_id: str | None = None
        self._sent_length = 0
        self._closed = False

    @property
    def is_active(self) -> bool:
        return self._current_message_id is not None

    async def start(self) -> None:
        mid = await self.platform.send_message(
            self.channel_id, self.INITIAL_TEXT, reply_to=self.reply_to
        )
        if mid is not None:
            self._current_message_id = mid
            self._sent_length = 0
            self._last_edit = time.monotonic()

    async def feed(self, delta: str) -> None:
        if self._closed or not delta or self._current_message_id is None:
            return
        self._buffer += delta
        await self._handle_overflow_if_needed()
        if self._current_message_id is None:
            return
        now = time.monotonic()
        if now - self._last_edit >= self.EDIT_INTERVAL:
            await self._flush_current()
            self._last_edit = now

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._handle_overflow_if_needed()
        await self._flush_final()

    @staticmethod
    def _split_at_boundary(text: str, max_len: int) -> tuple[str, str]:
        """Split text near max_len at the last newline or space.

        Falls back to a hard cut at max_len if no boundary is reasonably
        close. Returns (head, tail) with whitespace trimmed so the tail
        doesn't start with a leading newline or space.
        """
        if len(text) <= max_len:
            return text, ""
        cut = text.rfind("\n", 0, max_len)
        if cut < max_len * 0.8:
            cut = text.rfind(" ", 0, max_len)
        if cut < max_len * 0.5:
            cut = max_len
        return text[:cut].rstrip(), text[cut:].lstrip("\n ")

    async def _handle_overflow_if_needed(self) -> None:
        while (
            self._current_message_id is not None
            and len(self._buffer) - self._sent_length > self.MAX_CHARS
        ):
            remaining = self._buffer[self._sent_length :]
            head, _ = self._split_at_boundary(remaining, self.MAX_CHARS)
            if not head:
                head = remaining[: self.MAX_CHARS]
            await self.platform.edit_message(
                self.channel_id, self._current_message_id, head
            )
            self._sent_length += len(head)
            new_mid = await self.platform.send_message(
                self.channel_id, self.INITIAL_TEXT
            )
            if new_mid is None:
                self._current_message_id = None
                return
            self._current_message_id = new_mid
            self._last_edit = time.monotonic()

    async def _flush_current(self) -> None:
        if self._current_message_id is None:
            return
        remaining = self._buffer[self._sent_length :]
        if not remaining:
            return
        if len(remaining) <= self.MAX_CHARS:
            await self.platform.edit_message(
                self.channel_id, self._current_message_id, remaining
            )
            return
        head, _ = self._split_at_boundary(remaining, self.MAX_CHARS)
        if not head:
            head = remaining[: self.MAX_CHARS]
        await self.platform.edit_message(
            self.channel_id, self._current_message_id, head
        )

    async def _flush_final(self) -> None:
        if self._current_message_id is None:
            return
        remaining = self._buffer[self._sent_length :]
        if not remaining:
            return
        if len(remaining) <= self.MAX_CHARS:
            await self.platform.edit_message(
                self.channel_id, self._current_message_id, remaining
            )
            return
        truncated = remaining[: self.MAX_CHARS - 3] + "..."
        await self.platform.edit_message(
            self.channel_id, self._current_message_id, truncated
        )


class DiscordPlatform(Platform):
    name = "discord"

    def __init__(self, allowed_user_id: str | int | None = None) -> None:
        if allowed_user_id is None:
            raw = os.getenv("DISCORD_ALLOWED_USER_ID")
            if raw:
                allowed_user_id = int(raw)
        self._allowed_user_id: int | None = (
            int(allowed_user_id) if allowed_user_id is not None else None
        )

        intents = discord.Intents.none()
        intents.message_content = True
        intents.guilds = True
        self.client = discord.Client(intents=intents)
        self._callback: IncomingCallback | None = None
        self._register_events()

    @property
    def supports_voice(self) -> bool:
        return True

    @property
    def supports_screen_share(self) -> bool:
        return True

    def _register_events(self) -> None:
        client = self.client
        allowed = self._allowed_user_id
        state = self

        @client.event
        async def on_message(message: discord.Message) -> None:
            if message.author == client.user:
                return
            if not message.content or not message.content.strip():
                return
            if allowed is not None and message.author.id != allowed:
                return

            channel = message.channel
            if isinstance(channel, discord.Thread):
                thread = channel
            elif isinstance(channel, discord.DMChannel):
                thread = None
            else:
                title = (message.content or "").strip()[:40] or "Muninn"
                try:
                    thread = await message.create_thread(
                        name=f"Muninn - {title}",
                        auto_archive_duration=60,
                    )
                except discord.Forbidden:
                    thread = None

            reply_target = str(thread.id) if thread is not None else str(channel.id)
            conversation_id = f"discord:{reply_target}"

            incoming = IncomingMessage(
                text=message.content,
                sender_id=str(message.author.id),
                platform="discord",
                conversation_id=conversation_id,
                reply_target=reply_target,
                message_id=str(message.id),
                raw=message,
            )
            cb = state._callback
            if cb is not None:
                await cb(incoming)

    def on_message(self, callback: IncomingCallback) -> None:
        self._callback = callback

    async def _resolve_channel(self, channel_id: int):
        channel = self.client.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.client.fetch_channel(channel_id)
        except (discord.NotFound, discord.HTTPException):
            return None

    async def send_message(
        self,
        target: str,
        content: str,
        *,
        reply_to: str | None = None,
    ) -> str | None:
        channel = await self._resolve_channel(int(target))
        if channel is None:
            return None
        channel = cast(discord.abc.Messageable, channel)

        chunks = _chunk(content)
        first_id: str | None = None
        for i, chunk in enumerate(chunks):
            used_reply = False
            if i == 0 and reply_to:
                try:
                    ref = await channel.fetch_message(int(reply_to))
                    sent = await ref.reply(chunk)
                    first_id = str(sent.id)
                    used_reply = True
                except (discord.NotFound, discord.HTTPException):
                    used_reply = False
            if not used_reply:
                sent = await channel.send(chunk)
                if first_id is None:
                    first_id = str(sent.id)
        return first_id

    async def add_reaction(self, target: str, emoji: str) -> None:
        try:
            channel = await self._resolve_channel_for_message(int(target))
            if channel is None:
                return
            message = await channel.fetch_message(int(target))
            await message.add_reaction(emoji)
        except (discord.NotFound, discord.HTTPException, ValueError):
            pass

    async def remove_reaction(self, target: str, emoji: str) -> None:
        try:
            channel = await self._resolve_channel_for_message(int(target))
            if channel is None:
                return
            message = await channel.fetch_message(int(target))
            await message.remove_reaction(emoji, self.client.user)
        except (discord.NotFound, discord.HTTPException, ValueError):
            pass

    async def edit_message(
        self, channel_id: str, message_id: str, content: str
    ) -> None:
        try:
            channel = await self._resolve_channel(int(channel_id))
            if channel is None:
                return
            channel = cast(discord.abc.Messageable, channel)
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=content)
        except (discord.NotFound, discord.HTTPException, ValueError):
            pass

    async def _resolve_channel_for_message(self, message_id: int):
        for guild in self.client.guilds:
            for ch in guild.text_channels:
                try:
                    await ch.fetch_message(message_id)
                    return ch
                except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                    continue
        for ch in self.client.private_channels:
            try:
                await ch.fetch_message(message_id)
                return ch
            except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                continue
        return None

    async def start(self) -> None:
        token = os.environ.get("DISCORD_BOT_TOKEN")
        if not token:
            raise RuntimeError("DISCORD_BOT_TOKEN is not set in the environment.")
        await self.client.start(token)

    async def stop(self) -> None:
        if not self.client.is_closed():
            await self.client.close()
