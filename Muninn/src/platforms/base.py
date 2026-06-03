"""Platform abstraction.

A Platform is the I/O layer between Muninn and wherever users talk to it
(Discord, web, CLI, future transports). Concrete implementations live in
sibling modules (discord.py, slack.py, ...). The agent only depends on
this interface, so adding a new transport is a single new module.

Voice and screen-share methods are defined here as stubs so future
implementations have a contract to fill. They raise NotImplementedError
until Phase 2/3 lands.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class IncomingMessage:
    text: str
    sender_id: str
    platform: str
    conversation_id: str
    reply_target: str
    message_id: str
    raw: object | None = None


IncomingCallback = Callable[[IncomingMessage], Awaitable[None]]


class Platform(ABC):
    name: str = "base"

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def on_message(self, callback: IncomingCallback) -> None: ...

    @abstractmethod
    async def send_message(
        self,
        target: str,
        content: str,
        *,
        reply_to: str | None = None,
    ) -> str | None:
        """Send a message. Returns the sent message id (str) or None on failure."""

    @abstractmethod
    async def add_reaction(self, target: str, emoji: str) -> None: ...

    @abstractmethod
    async def remove_reaction(self, target: str, emoji: str) -> None: ...

    @abstractmethod
    async def edit_message(
        self, channel_id: str, message_id: str, content: str
    ) -> None: ...

    @property
    def supports_voice(self) -> bool:
        return False

    @property
    def supports_screen_share(self) -> bool:
        return False

    async def join_voice(self, channel_id: str) -> None:
        raise NotImplementedError(
            f"{self.name}: voice join not implemented (Phase 2)"
        )

    async def leave_voice(self) -> None:
        raise NotImplementedError(
            f"{self.name}: voice leave not implemented (Phase 2)"
        )

    async def play_in_voice(self, channel_id: str, audio_source: object) -> None:
        raise NotImplementedError(
            f"{self.name}: voice playback not implemented (Phase 2)"
        )

    async def speak_in_voice(self, channel_id: str, text: str) -> None:
        raise NotImplementedError(
            f"{self.name}: voice TTS not implemented (Phase 2)"
        )

    async def share_screen(self, target_channel: str) -> None:
        raise NotImplementedError(
            f"{self.name}: screen share not implemented (Phase 3)"
        )
