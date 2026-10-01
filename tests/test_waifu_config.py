"""Tests for the native Waifu-Mode config section (功能B).

Covers the three contracts the outbound bubble path depends on:
  1. ``DEFAULT_CONFIG`` seeds a complete, OFF-by-default ``waifu`` section (so
     ``_validate_config_key`` accepts ``waifu.*`` and ``hermes config set`` can write it);
  2. ``validate_config_structure`` rejects the values that would crash or silently distort the
     send path (negative speed/count, truthy non-bool switch);
  3. the reader turns the RAW effective user config into typed values and treats an absent or
     malformed section as OFF — the gateway reads ``load_user_config_effective``, which never
     merges ``DEFAULT_CONFIG``.
"""

from __future__ import annotations

import textwrap

import pytest

from hermes_cli.config import DEFAULT_CONFIG, _validate_config_key, validate_config_structure
from hermes_cli.config_effective import load_user_config_effective
from hermes_cli.waifu_config import (
    DEFAULT_TYPING_JITTER_MS, read_waifu_config, waifu_enabled)

WAIFU_DEFAULTS = {
    "enabled": False,
    "typing_speed": 0.0,
    "typing_jitter_ms": DEFAULT_TYPING_JITTER_MS,
    "interrupt_after": 0,
    "strip_trailing_punctuation": False,
    "emojis": {},
}


class TestDefaults:
    def test_section_seeded_off(self):
        assert DEFAULT_CONFIG["waifu"] == WAIFU_DEFAULTS

    def test_declared_types(self):
        waifu = DEFAULT_CONFIG["waifu"]
        assert isinstance(waifu["enabled"], bool)
        assert isinstance(waifu["typing_speed"], float)
        assert isinstance(waifu["typing_jitter_ms"], int) and not isinstance(waifu["typing_jitter_ms"], bool)
        assert isinstance(waifu["interrupt_after"], int) and not isinstance(waifu["interrupt_after"], bool)
        assert isinstance(waifu["strip_trailing_punctuation"], bool)
        assert waifu["emojis"] == {}

    def test_keys_are_known_to_config_set(self):
        # Seeding DEFAULT_CONFIG is what registers the section: there is no separate schema.
        for key in ("waifu.enabled", "waifu.typing_speed", "waifu.typing_jitter_ms",
                    "waifu.interrupt_after", "waifu.strip_trailing_punctuation"):
            assert _validate_config_key(key) == (True, None), key
        # The empty ``emojis`` mapping is an open-subkey section: user-chosen names validate free.
        assert _validate_config_key("waifu.emojis.happy") == (True, None)

    def test_absent_or_empty_section_reads_off(self):
        assert waifu_enabled({}) is False
        assert waifu_enabled({"waifu": {}}) is False
        assert waifu_enabled({"waifu": None}) is False
        assert read_waifu_config(None).typing_speed == 0.0
        # Jitter has no OFF reading of its own: absent keeps the seeded typing illusion.
        assert read_waifu_config({}).typing_jitter_ms == DEFAULT_TYPING_JITTER_MS


class TestValidation:
    def test_valid_values_pass(self):
        issues = validate_config_structure({
            "waifu": {
                "enabled": True,
                "typing_speed": 3.0,
                "typing_jitter_ms": 0,
                "interrupt_after": 2,
                "strip_trailing_punctuation": True,
                "emojis": {"happy": "/tmp/happy.png"},
            },
        })
        assert [i for i in issues if i.severity == "error"] == []

    @pytest.mark.parametrize("section,key", [
        ({"typing_speed": -1}, "typing_speed"),
        ({"typing_jitter_ms": -2}, "typing_jitter_ms"),
        ({"typing_jitter_ms": 1.5}, "typing_jitter_ms"),
        ({"interrupt_after": -2}, "interrupt_after"),
        ({"enabled": "yes"}, "enabled"),
    ])
    def test_bad_values_rejected(self, section, key):
        issues = validate_config_structure({"waifu": section})
        assert any(i.severity == "error" and key in i.message for i in issues), issues


class TestEffectiveRead:
    def _config(self, tmp_path, body: str):
        path = tmp_path / "config.yaml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_enabled_true_round_trip_through_effective(self, tmp_path):
        path = self._config(tmp_path, """
            waifu:
              enabled: true
              typing_speed: 3
              interrupt_after: 2
            """)
        cfg = read_waifu_config(load_user_config_effective(path))
        assert cfg.enabled is True
        assert cfg.typing_speed == 3.0
        assert cfg.interrupt_after == 2

    def test_absent_key_is_false(self, tmp_path):
        path = self._config(tmp_path, "model:\n  provider: openrouter\n")
        assert read_waifu_config(load_user_config_effective(path)).enabled is False

    def test_jitter_round_trip_and_malformed_fallback(self, tmp_path):
        path = self._config(tmp_path, """
            waifu:
              enabled: true
              typing_jitter_ms: 0
            """)
        assert read_waifu_config(load_user_config_effective(path)).typing_jitter_ms == 0
        path.write_text("waifu:\n  typing_jitter_ms: -4\n", encoding="utf-8")
        assert read_waifu_config(
            load_user_config_effective(path)).typing_jitter_ms == DEFAULT_TYPING_JITTER_MS

    def test_malformed_values_fall_back_off(self, tmp_path):
        path = self._config(tmp_path, """
            waifu:
              enabled: 'yes'
              typing_speed: -4
              interrupt_after: null
            """)
        cfg = read_waifu_config(load_user_config_effective(path))
        assert cfg.enabled is False
        assert cfg.typing_speed == 0.0
        assert cfg.interrupt_after == 0
