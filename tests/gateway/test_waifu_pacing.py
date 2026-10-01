"""Tests for Waifu-Mode typing pacing (功能B, typing_speed).

Contract under test:
  * ``typing_speed > 0`` waits ``len(bubble)/typing_speed`` seconds BEFORE each bubble is sent;
  * ``typing_speed = 0`` (the OFF path) never waits — pacing must not slow the existing send path;
  * the wait is interruptible: the session's interrupt event (a steer from a new user message)
    ends it immediately instead of holding the delivery path asleep, and task cancellation
    propagates untouched.
"""

from __future__ import annotations

import asyncio

import pytest

import gateway.platforms.helpers as helpers
from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent
from gateway.platforms.helpers import wait_or_interrupt, waifu_pace_delay
from gateway.session import SessionSource
from hermes_cli.waifu_config import WaifuConfig


class _StubAdapter:
    name = "stub"
    _dispatch_final_text = BasePlatformAdapter._dispatch_final_text
    _send_waifu_series = BasePlatformAdapter._send_waifu_series
    _waifu_transport_chunks = BasePlatformAdapter._waifu_transport_chunks
    _waifu_interrupt_guard = BasePlatformAdapter._waifu_interrupt_guard


def _event(chat_id: str = "c1") -> MessageEvent:
    return MessageEvent(
        text="hi", source=SessionSource(platform=Platform.TELEGRAM, chat_id=chat_id, user_id="u"))


def _make_stub(*, config, sends, guard=None, session_key="sk"):
    stub = _StubAdapter()
    stub._waifu_delivery_enabled = lambda _event: True
    stub._waifu_config = lambda _event: config
    stub.max_message_length_for_chat = lambda _chat_id: None
    stub.message_len_fn_for_chat = lambda _chat_id: len
    stub._waifu_ledger_record = lambda **_kw: None  # ledger covered by test_waifu_ledger
    if guard is not None:
        stub._active_sessions = {session_key: guard}

    async def _fake_final(event, key, text, metadata, is_ephemeral, ttl, record):
        sends.append(text)

    async def _fake_ledger(event, key, text, metadata, *, reply_to, is_ephemeral_response=False):
        sends.append(text)
        return SendResult(success=True, message_id=f"m{len(sends)}"), stub

    stub._send_final_text = _fake_final
    stub.send_final_ledgered = _fake_ledger
    return stub


def _run(coro):
    return asyncio.run(coro)


# ── the pure delay calculation ────────────────────────────────────────────────

def test_delay_is_chars_over_speed():
    assert waifu_pace_delay("abcd", 2.0) == pytest.approx(2.0)


def test_zero_speed_is_the_instant_path_even_with_jitter():
    assert waifu_pace_delay("abcd", 0.0, jitter_ms=500) == 0.0


def test_jitter_is_bounded_by_the_configured_milliseconds():
    lo = waifu_pace_delay("abcdefghij", 1.0, jitter_ms=93, uniform=lambda a, b: a)
    hi = waifu_pace_delay("abcdefghij", 1.0, jitter_ms=93, uniform=lambda a, b: b)
    assert lo == pytest.approx(10 - 0.093)
    assert hi == pytest.approx(10 + 0.093)


def test_jitter_never_makes_a_wait_negative():
    # A tiny bubble (1 char = 1ms at 1000 cps) hit by the full -93ms jitter clamps to 0.
    assert waifu_pace_delay("x", 1000.0, jitter_ms=93, uniform=lambda a, b: a) == 0.0


def test_series_passes_the_configured_jitter(monkeypatch):
    seen: list = []
    stub = _make_stub(config=WaifuConfig(enabled=True, typing_speed=4.0, typing_jitter_ms=7), sends=[])

    def _record(text, typing_speed, jitter_ms=0):
        seen.append((text, typing_speed, jitter_ms))
        return 0.0

    monkeypatch.setattr(helpers, "waifu_pace_delay", _record)
    _run(stub._dispatch_final_text(_event(), "sk", "one\n\ntwo", {}, False, 0, lambda _r: None))
    assert seen == [("one", 4.0, 7), ("two", 4.0, 7)]


# ── the interruptible wait ────────────────────────────────────────────────────

def test_zero_delay_does_not_sleep():
    assert _run(wait_or_interrupt(0.0)) is True


def test_full_delay_elapses_without_a_guard():
    assert _run(wait_or_interrupt(0.01)) is True


def test_guard_firing_cuts_the_wait_short():
    async def scenario():
        guard = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, guard.set)
        completed = await wait_or_interrupt(30.0, guard)
        return completed

    assert _run(scenario()) is False


def test_already_set_guard_returns_immediately():
    async def scenario():
        guard = asyncio.Event()
        guard.set()
        return await asyncio.wait_for(wait_or_interrupt(30.0, guard), timeout=2.0)

    assert _run(scenario()) is False


def test_cancellation_propagates_out_of_the_wait():
    async def scenario():
        task = asyncio.ensure_future(wait_or_interrupt(30.0))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(scenario())


# ── pacing inside the series ──────────────────────────────────────────────────

def test_series_waits_per_bubble_before_sending(monkeypatch):
    order: list = []
    delays: list = []
    sends: list = []
    stub = _make_stub(config=WaifuConfig(enabled=True, typing_speed=10.0), sends=sends)

    def _record_delay(text, typing_speed, jitter_ms=0):
        delays.append((text, typing_speed))
        return 0.0

    async def _record_wait(delay, guard=None):
        order.append(("wait", delay))
        return True

    def _record_send(text):
        order.append(("send", text))

    monkeypatch.setattr(helpers, "waifu_pace_delay", _record_delay)
    monkeypatch.setattr(helpers, "wait_or_interrupt", _record_wait)

    async def _fake_final(event, key, text, metadata, is_ephemeral, ttl, record):
        _record_send(text)

    async def _fake_ledger(event, key, text, metadata, *, reply_to, is_ephemeral_response=False):
        _record_send(text)
        return SendResult(success=True, message_id="m"), stub

    stub._send_final_text = _fake_final
    stub.send_final_ledgered = _fake_ledger
    _run(stub._dispatch_final_text(_event(), "sk", "one\n\ntwo\n\nthree", {}, False, 0, lambda _r: None))

    assert delays == [("one", 10.0), ("two", 10.0), ("three", 10.0)]
    assert order == [("wait", 0.0), ("send", "one"), ("wait", 0.0), ("send", "two"),
                     ("wait", 0.0), ("send", "three")]


def test_off_path_never_waits_on_long_bubbles():
    """typing_speed = 0 is the plain send path: a long series must not spend time sleeping."""
    import time

    sends: list = []
    long_bubble = "x" * 4000
    stub = _make_stub(config=WaifuConfig(enabled=True, typing_speed=0.0), sends=sends)
    started = time.monotonic()
    _run(stub._dispatch_final_text(
        _event(), "sk", f"{long_bubble}\n\n{long_bubble}\n\n{long_bubble}", {}, False, 0, lambda _r: None))
    assert sends == [long_bubble] * 3
    assert time.monotonic() - started < 1.0


def test_set_interrupt_guard_does_not_stall_the_series():
    """A pending steer must not hold the already-planned bubbles asleep.

    At 0.5 chars/sec each 2-char bubble would sleep 4s (12s total); with the guard already set the
    series must finish immediately.
    """
    import time

    guard = asyncio.Event()
    guard.set()
    sends: list = []
    stub = _make_stub(config=WaifuConfig(enabled=True, typing_speed=0.5), sends=sends, guard=guard)

    async def scenario():
        return await asyncio.wait_for(
            stub._dispatch_final_text(_event(), "sk", "aa\n\nbb\n\ncc", {}, False, 0, lambda _r: None),
            timeout=5.0)

    started = time.monotonic()
    _run(scenario())
    assert sends == ["aa", "bb", "cc"]
    assert time.monotonic() - started < 2.0
