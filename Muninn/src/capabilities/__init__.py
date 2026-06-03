"""Optional capabilities a platform may declare.

These Protocols are checked via isinstance() at runtime. Adding voice or
screen-share support to a transport means implementing the corresponding
Protocol and setting the supports_voice / supports_screen_share flags on
the Platform subclass. The agent's tool list (future Phase 2/3) will
inspect these to decide which tools to register.
"""

from .voice import ScreenShareCapability, VoiceCapability, VoiceSession

__all__ = ["VoiceCapability", "ScreenShareCapability", "VoiceSession"]
