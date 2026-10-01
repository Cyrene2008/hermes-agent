"""Reader + normalization for the native Waifu-Mode ``waifu`` config section (功能B).

``DEFAULT_CONFIG`` seeds the section (``hermes_cli/config_defaults.py``) and
``validate_config_structure`` type-checks what the user writes, but the gateway reads config
through ``load_user_config_effective`` — the RAW user YAML with NO ``DEFAULT_CONFIG`` merge — so
every reader must tolerate the section, a single key, or the whole file being absent or
malformed. Mirrors the ``agent/audio_routing.py::audio_input_mode`` precedent: one primitive
that turns an arbitrary mapping into usable, typed values and never raises.

Nothing here is the default-on switch: ``enabled`` is only true for the literal boolean
``True``, so ``enabled: 'yes'`` (a string ``validate_config_structure`` already flags) still
reads OFF and the deployed single-send path stays untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict


def _coerce_typing_speed(raw: Any) -> float:
    """chars/sec; absent, malformed (incl. ``bool``) or negative -> ``0.0`` (no delay)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    speed = float(raw)
    return speed if speed > 0 else 0.0


def _coerce_interrupt_after(raw: Any) -> int:
    """Bubbles still delivered after a new user message arrives; absent/malformed/negative -> 0."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0
    value = int(raw)
    return value if value > 0 else 0


@dataclass(frozen=True)
class WaifuConfig:
    """Typed, validated view of the ``waifu`` section. Every field has an OFF/zero default."""

    enabled: bool = False
    typing_speed: float = 0.0
    interrupt_after: int = 0
    strip_trailing_punctuation: bool = False
    emojis: Dict[str, str] = field(default_factory=dict)


def read_waifu_config(cfg: Any) -> WaifuConfig:
    """``waifu`` from a (possibly raw-effective) config mapping; all-OFF when absent/malformed."""
    section = cfg.get("waifu") if isinstance(cfg, Mapping) else None
    if not isinstance(section, Mapping):
        return WaifuConfig()
    raw_emojis = section.get("emojis")
    emojis = (
        {k: v for k, v in raw_emojis.items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(raw_emojis, Mapping)
        else {}
    )
    return WaifuConfig(
        # Strict identity, not truthiness: a stray string must never turn the series on.
        enabled=section.get("enabled") is True,
        typing_speed=_coerce_typing_speed(section.get("typing_speed")),
        interrupt_after=_coerce_interrupt_after(section.get("interrupt_after")),
        strip_trailing_punctuation=section.get("strip_trailing_punctuation") is True,
        emojis=emojis,
    )


def waifu_enabled(cfg: Any) -> bool:
    """Cheap gate for the outbound path: True only when ``waifu.enabled`` is exactly True."""
    return read_waifu_config(cfg).enabled
