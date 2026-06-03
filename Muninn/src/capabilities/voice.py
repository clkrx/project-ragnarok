"""Voice and screen-share capability contracts.

Both Protocols are runtime-checkable. Transports opt in by implementing
them and setting the matching supports_* property on their Platform
subclass. No concrete voice / screen code ships yet -- the Platform base
class raises NotImplementedError on those methods until Phase 2/3.
"""

from typing import Protocol, runtime_checkable


class VoiceSession:
    def __init__(self, channel_id: str) -> None:
        self.channel_id = channel_id


@runtime_checkable
class VoiceCapability(Protocol):
    async def join(self, channel_id: str) -> VoiceSession: ...

    async def leave(self) -> None: ...

    async def play(self, audio_source: object) -> None: ...

    async def speak(self, text: str) -> None: ...


@runtime_checkable
class ScreenShareCapability(Protocol):
    async def start(self, target_channel: str) -> None: ...

    async def stop(self) -> None: ...
