from __future__ import annotations

from typing import Any

from .contracts import GamePlugin


def validate_plugin(plugin: GamePlugin, extension: dict[str, Any], levels: list[Any]) -> list[str]:
    plugin.validate_extension(extension)
    errors: list[str] = []
    identities = []
    for level in levels:
        errors.extend(plugin.validate_level(level))
        identities.append(plugin.level_identity(level))
    if len(identities) != len(set(identities)):
        errors.append("level identities must be unique within a campaign")
    return errors