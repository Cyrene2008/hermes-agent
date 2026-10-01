"""Tests for the 句末标点处理 rule (``waifu.strip_trailing_punctuation``).

Partner's correction, verbatim: 「句末标点不是啥都去掉，有时候表示无语惊讶或者疑惑，还是要[标点]的」.
So the outbound strip removes ONLY sentence-reading tails (。．.，,、;； + trailing spaces) and must
PRESERVE every emotion-bearing ending: ？ ! … (including repeats like ``……？`` ``？？`` ``！！``), ``~``,
``♪``, emoji, and any character not in the strip set.

Contract:
  * the pure helper :func:`strip_waifu_trailing_punctuation` follows that rule;
  * the gateway series applies it to each OUTBOUND bubble only when the switch is on;
  * with the switch OFF the sent bytes are identical to the source (byte-for-byte);
  * the conversation log keeps the original text (strip is a send-time display concern).
"""

from __future__ import annotations

import asyncio

import pytest

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter
from gateway.platforms.event import MessageEvent
from gateway.platforms.helpers import strip_waifu_trailing_punctuation
from gateway.session import SessionSource
from hermes_cli.waifu_config import WaifuConfig


# ── helper semantics ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    # plain sentence-reading tails are stripped
    ("好呀。", "好呀"),
    ("我服了。", "我服了"),
    ("好呀．", "好呀"),
    ("好呀.", "好呀"),
    ("好呀，", "好呀"),
    ("好呀,", "好呀"),
    ("好呀、", "好呀"),
    ("好呀；", "好呀"),
    ("好呀;", "好呀"),
    ("好呀。。。", "好呀"),          # a run of periods all goes
    ("前一句。后一句。", "前一句。后一句"),  # only the TAIL period goes
])
def test_plain_tail_punctuation_stripped(raw, expected):
    assert strip_waifu_trailing_punctuation(raw) == expected


@pytest.mark.parametrize("raw", [
    "真的吗？", "真的吗?",          # question
    "啊！", "啊!",                  # exclamation
    "可是…", "…",                   # ellipsis
])
def test_emotion_bearing_tail_kept(raw):
    assert strip_waifu_trailing_punctuation(raw) == raw


@pytest.mark.parametrize("raw", [
    "……？", "？？", "！！", "？！", "……", "……！",
    "好呀。……？",   # interior period untouched, emotional repeat tail survives
])
def test_repeated_emotion_tails_kept_intact(raw):
    assert strip_waifu_trailing_punctuation(raw) == raw


@pytest.mark.parametrize("raw", [
    "好呀~", "好呀～", "好呀♪", "好呀♫", "好呀😊", "好呀🥺", "好呀💢",
])
def test_tilde_note_emoji_tails_kept(raw):
    assert strip_waifu_trailing_punctuation(raw) == raw


@pytest.mark.parametrize("raw, expected", [
    ("好呀。 ", "好呀"),
    ("好呀。  ", "好呀"),
    ("好呀 ", "好呀"),
    ("好呀\u3000", "好呀"),        # full-width space
    ("好呀。\t", "好呀"),
])
def test_trailing_spaces_trimmed_along_with_tail(raw, expected):
    assert strip_waifu_trailing_punctuation(raw) == expected


def test_all_punctuation_bubble_is_never_emptied():
    # An all-punctuation bubble is returned as-is so the series never sends an empty message.
    assert strip_waifu_trailing_punctuation("。。。") == "。。。"
    assert strip_waifu_trailing_punctuation("   ") == "   "


def test_non_string_and_empty_pass_through():
    assert strip_waifu_trailing_punctuation("") == ""
    assert strip_waifu_trailing_punctuation(None) is None


# ── gateway series integration ───────────────────────────────────────────────

class _StubAdapter:
    name = "stub"
    _dispatch_final_text = BasePlatformAdapter._dispatch_final_text
    _send_waifu_series = BasePlatformAdapter._send_waifu_series
    _waifu_transport_chunks = BasePlatformAdapter._waifu_transport_chunks
    _waifu_interrupt_guard = BasePlatformAdapter._waifu_interrupt_guard
    _waifu_config = BasePlatformAdapter._waifu_config
    _waifu_delivery_enabled = BasePlatformAdapter._waifu_delivery_enabled


def _event(chat_id: str = "c1") -> MessageEvent:
    return MessageEvent(
        text="hi",
        source=SessionSource(platform=Platform.TELEGRAM, chat_id=chat_id, user_id="u"),
    )


def _make_stub(*, sends, finals, config):
    stub = _StubAdapter()
    stub._waifu_delivery_enabled = lambda _event: True
    stub._waifu_config = lambda _event: config
    stub.max_message_length_for_chat = lambda _chat_id: None
    stub.message_len_fn_for_chat = lambda _chat_id: len
    stub._waifu_ledger_record = lambda **_kw: None

    async def _fake_final(event, session_key, text, metadata, is_ephemeral, ttl, record):
        finals.append(text)

    async def _fake_ledger(event, session_key, text, metadata, *, reply_to, is_ephemeral_response=False):
        sends.append(text)
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=f"m{len(sends)}"), stub

    stub._send_final_text = _fake_final
    stub.send_final_ledgered = _fake_ledger
    return stub


def _run(coro):
    return asyncio.run(coro)


def test_series_strips_plain_period_but_keeps_questions():
    sends: list = []
    stub = _make_stub(sends=sends, finals=[], config=WaifuConfig(strip_trailing_punctuation=True))
    _run(stub._dispatch_final_text(
        _event(), "sk", "好呀。\n\n真的吗？\n\n我服了。", {}, False, 0, lambda _r: None))
    assert sends == ["好呀", "真的吗？", "我服了"]


def test_series_disabled_path_is_byte_for_byte_unchanged():
    sends: list = []
    stub = _make_stub(sends=sends, finals=[], config=WaifuConfig(strip_trailing_punctuation=False))
    source = "好呀。\n\n真的吗？\n\n……"
    _run(stub._dispatch_final_text(_event(), "sk", source, {}, False, 0, lambda _r: None))
    assert sends == ["好呀。", "真的吗？", "……"]


def test_series_strips_multi_char_emotion_tail_kept_intact():
    sends: list = []
    stub = _make_stub(sends=sends, finals=[], config=WaifuConfig(strip_trailing_punctuation=True))
    _run(stub._dispatch_final_text(
        _event(), "sk", "好呀。\n\n……？\n\n啊！\n\n好呀♪", {}, False, 0, lambda _r: None))
    assert sends == ["好呀", "……？", "啊！", "好呀♪"]


def test_single_bubble_enabled_strip_applies_to_the_fallback_send():
    sends: list = []
    finals: list = []
    stub = _make_stub(sends=sends, finals=finals, config=WaifuConfig(strip_trailing_punctuation=True))
    _run(stub._dispatch_final_text(_event(), "sk", "就这一句。", {}, False, 0, lambda _r: None))
    assert sends == []
    assert finals == ["就这一句"]
