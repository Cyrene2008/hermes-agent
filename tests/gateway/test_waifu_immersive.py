"""Tests for native Waifu-Mode immersive mode (功能B, slice: ``waifu.immersive``).

Contract under test — ON suppresses Hermes' own system notices, OFF is byte-for-byte today:
  * agent side: ``waifu.immersive: true`` forces the file-mutation verifier and the
    turn-completion explainer off through ``_display_flag_enabled``'s single config read;
  * gateway side: ``waifu.immersive: true`` ORs into the status / notice / status-text /
    stream-consumer gates that ``ctx.mute_notification_reply`` already guarded;
  * with ``waifu.immersive: false`` every gate behaves exactly as before.
"""

from __future__ import annotations

import types

import pytest
from unittest.mock import patch

import gateway.run as gateway_run
import gateway.warning_notifications as warning_notifications
from agent.turn_explainers import TurnExplainersMixin
from gateway.config import Platform
from gateway.run_turn_runner import TurnRunner


# ── agent side: the display-flag seam ────────────────────────────────────────

class _Agent(TurnExplainersMixin):
    """Bare agent: only the explainer mixin, no __init__ side effects."""


def _agent() -> _Agent:
    return _Agent()


def test_immersive_suppresses_agent_explainers(monkeypatch):
    monkeypatch.delenv("HERMES_FILE_MUTATION_VERIFIER", raising=False)
    monkeypatch.delenv("HERMES_TURN_COMPLETION_EXPLAINER", raising=False)
    with patch("hermes_cli.config.load_config", return_value={"waifu": {"immersive": True}}):
        agent = _agent()
        assert agent._turn_completion_explainer_enabled() is False
        assert agent._file_mutation_verifier_enabled() is False


def test_immersive_off_keeps_default_explainers(monkeypatch):
    monkeypatch.delenv("HERMES_FILE_MUTATION_VERIFIER", raising=False)
    monkeypatch.delenv("HERMES_TURN_COMPLETION_EXPLAINER", raising=False)
    with patch("hermes_cli.config.load_config", return_value={"waifu": {"immersive": False}}):
        agent = _agent()
        assert agent._turn_completion_explainer_enabled() is True
        assert agent._file_mutation_verifier_enabled() is True


def test_immersive_off_leaves_explicit_display_off_alone(monkeypatch):
    monkeypatch.delenv("HERMES_TURN_COMPLETION_EXPLAINER", raising=False)
    with patch("hermes_cli.config.load_config",
               return_value={"waifu": {"immersive": False},
                             "display": {"turn_completion_explainer": False}}):
        assert _agent()._turn_completion_explainer_enabled() is False


# ── gateway side: the notice rails ───────────────────────────────────────────

class _FakeAdapter:
    async def send(self, *args, **kwargs):
        return None


def _ctx(immersive: bool, *, mute: bool = False):
    return types.SimpleNamespace(
        mute_notification_reply=mute,
        waifu_immersive=immersive,
        _status_adapter=object(),
        _status_chat_id="c",
        _run_still_current=lambda: True,
        source=types.SimpleNamespace(platform=Platform.QQBOT),
        user_config={},
    )


def _runner(ctx) -> TurnRunner:
    runner = object.__new__(TurnRunner)
    runner._ctx = ctx
    return runner


@pytest.fixture
def _status_sink(monkeypatch):
    """Capture whether a status/notice render reaches the wire."""
    calls: list = []
    monkeypatch.setattr(gateway_run, "_prepare_gateway_status_message", lambda *a, **k: {"x": 1})
    monkeypatch.setattr(warning_notifications, "render_notification", lambda *a, **k: calls.append(k))
    return calls


def test_status_callback_suppressed_by_immersive(_status_sink):
    _runner(_ctx(True))._status_callback_sync("info", "hello")
    assert _status_sink == []


def test_status_callback_unchanged_when_immersive_off(_status_sink):
    _runner(_ctx(False))._status_callback_sync("info", "hello")
    assert len(_status_sink) == 1


def test_notice_callback_suppressed_by_immersive(_status_sink):
    notice = types.SimpleNamespace(level="warn", key="x")
    _runner(_ctx(True))._notice_callback_sync(notice)
    assert _status_sink == []


def test_notice_callback_unchanged_when_immersive_off(_status_sink):
    notice = types.SimpleNamespace(level="warn", key="x")
    _runner(_ctx(False))._notice_callback_sync(notice)
    assert len(_status_sink) == 1


def test_send_status_text_suppressed_by_immersive():
    ctx = _ctx(True)
    runner = _runner(ctx)
    scheduled: list = []
    runner._schedule = lambda coro, log: scheduled.append(log)
    runner._send_status_text("text", {}, "log message")
    assert scheduled == []


def test_send_status_text_unchanged_when_immersive_off():
    ctx = _ctx(False)
    ctx._status_adapter = _FakeAdapter()
    runner = _runner(ctx)
    scheduled: list = []

    def _schedule(coro, log):
        scheduled.append(log)
        coro.close()  # the coroutine is never awaited in this unit test

    runner._schedule = _schedule
    runner._send_status_text("text", {}, "log message")
    assert scheduled == ["log message"]


def test_stream_consumer_off_by_immersive():
    assert _runner(_ctx(True))._setup_stream_consumer("qqbot") == (None, None, None, False)
