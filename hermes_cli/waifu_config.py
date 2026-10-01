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

#: Factory typing pace in chars/sec for a paced Waifu-Mode series. 11.12 is deliberate: the
#: dashboard slider's far end (0.5-20, two decimals) lands exactly on it. 0 still means "no delay".
DEFAULT_TYPING_SPEED = 11.12


def _coerce_typing_speed(raw: Any) -> float:
    """chars/sec; absent/unusable -> the factory default, an explicit negative -> ``0.0`` (no delay)."""
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_TYPING_SPEED
    speed = float(raw)
    return speed if speed >= 0 else 0.0


def _coerce_interrupt_after(raw: Any) -> int:
    """Bubbles still delivered after a new user message arrives; absent/malformed/negative -> 0."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0
    value = int(raw)
    return value if value > 0 else 0


#: Random +/- ms of human typing wobble on each paced wait. The seeded default; a malformed value
#: falls back HERE rather than to 0 so the typing illusion survives a bad config.yaml edit.
DEFAULT_TYPING_JITTER_MS = 93


def _coerce_typing_jitter_ms(raw: Any) -> int:
    """Whole milliseconds >= 0; absent/malformed/negative -> :data:`DEFAULT_TYPING_JITTER_MS`."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_TYPING_JITTER_MS
    value = int(raw)
    return value if value >= 0 else DEFAULT_TYPING_JITTER_MS


@dataclass(frozen=True)
class WaifuConfig:
    """Typed, validated view of the ``waifu`` section. Every field defaults to OFF/neutral.

    ``typing_speed`` is the exception to "zero default": the factory pace (:data:`DEFAULT_TYPING_SPEED`)
    applies whenever the key is absent, so enabling the feature paces at the documented default
    without the user setting anything. Only an explicit ``0`` (or a negative, which validation
    flags) means "no delay".
    """

    enabled: bool = False
    typing_speed: float = DEFAULT_TYPING_SPEED
    typing_jitter_ms: int = DEFAULT_TYPING_JITTER_MS
    interrupt_after: int = 0
    strip_trailing_punctuation: bool = False
    emojis_enabled: bool = False
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
        typing_jitter_ms=_coerce_typing_jitter_ms(section.get("typing_jitter_ms")),
        interrupt_after=_coerce_interrupt_after(section.get("interrupt_after")),
        strip_trailing_punctuation=section.get("strip_trailing_punctuation") is True,
        emojis_enabled=section.get("emojis_enabled") is True,
        emojis=emojis,
    )


def waifu_enabled(cfg: Any) -> bool:
    """Cheap gate for the outbound path: True only when ``waifu.enabled`` is exactly True."""
    return read_waifu_config(cfg).enabled
