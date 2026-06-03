"""Platform package exports.

Discord is intentionally not imported here -- it pulls in discord.py and the
rest of the heavyweight transport deps. Transports are imported by name
where they're used (e.g. `from platforms.discord import DiscordPlatform`).
"""

from .base import IncomingCallback, IncomingMessage, Platform

__all__ = ["Platform", "IncomingMessage", "IncomingCallback"]
