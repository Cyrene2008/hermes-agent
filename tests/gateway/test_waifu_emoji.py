"""Tests for native Waifu-Mode emoji/sticker bubbles (功能B, slice: ``waifu.emojis_enabled``).

Contract under test:
  * a ``[emoji:NAME]`` token whose NAME maps to an existing file sends the sticker as a native
    IMAGE through the platform rich-media path (QQ ``msg_type=7``) ALONE — 表情包单独成一条,
    no caption — and the bubble's remaining text follows as the series' NEXT text bubble (an
    empty remainder means the sticker is the whole bubble);
  * a mapped-but-missing file logs and falls back to TEXT, and the token is never shown;
  * an unresolved token (NAME absent from the map) is dropped from the visible text;
  * ``emojis_enabled=false`` leaves the bubble byte-for-byte untouched (token and all) and sends
    nothing image-shaped — the deployed text path is unchanged.
"""

from __future__ import annotations

import asyncio
import logging

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent
from gateway.platforms.helpers import waifu_emoji_send
from gateway.session import SessionSource
from hermes_cli.waifu_config import WaifuConfig


class _StubAdapter:
    """Borrows the real series/emoji methods without a transport."""

    name = "stub"
    _dispatch_final_text = BasePlatformAdapter._dispatch_final_text
    _send_waifu_series = BasePlatformAdapter._send_waifu_series
    _send_waifu_sticker = BasePlatformAdapter._send_waifu_sticker
    _waifu_transport_chunks = BasePlatformAdapter._waifu_transport_chunks
    _waifu_interrupt_guard = BasePlatformAdapter._waifu_interrupt_guard
    _waifu_config = BasePlatformAdapter._waifu_config
    _waifu_delivery_enabled = BasePlatformAdapter._waifu_delivery_enabled


def _event(chat_id: str = "c1") -> MessageEvent:
    return MessageEvent(
        text="hi",
        source=SessionSource(platform=Platform.QQBOT, chat_id=chat_id, user_id="u"),
    )


def _make_stub(*, config, texts, images, error=None):
    stub = _StubAdapter()
    stub._waifu_delivery_enabled = lambda _event: True
    stub._waifu_config = lambda _event: config
    stub.max_message_length_for_chat = lambda _chat_id: None
    stub.message_len_fn_for_chat = lambda _chat_id: len
    stub._waifu_ledger_record = lambda **_kw: None  # ledger covered by test_waifu_ledger

    async def _fake_final(event, session_key, text, metadata, is_ephemeral, ttl, record):
        texts.append(text)

    async def _fake_ledger(event, session_key, text, metadata, *, reply_to, is_ephemeral_response=False):
        texts.append(text)
        return SendResult(success=True, message_id=f"t{len(texts)}"), stub

    async def _fake_image(chat_id, image_path, caption=None, reply_to=None, metadata=None, **kwargs):
        images.append((image_path, caption))
        if error is not None:
            return SendResult(success=False, error=error)
        return SendResult(success=True, message_id=f"i{len(images)}")

    stub._send_final_text = _fake_final
    stub.send_final_ledgered = _fake_ledger
    stub.send_image_file = _fake_image
    return stub


def _run(coro):
    return asyncio.run(coro)


# ── the pure resolver ────────────────────────────────────────────────────────

def test_waifu_emoji_send_resolves_first_mapped_token_and_strips_it():
    path, caption = waifu_emoji_send("[emoji:happy] good morning", {"happy": "/img/happy.png"})
    assert path == "/img/happy.png"
    assert caption == "good morning"


def test_waifu_emoji_send_strips_every_token_from_the_caption():
    path, caption = waifu_emoji_send("[emoji:x] hi [emoji:y]", {"x": "/img/x.png"})
    assert path == "/img/x.png"
    assert caption == "hi"


def test_waifu_emoji_send_unresolved_token_returns_text_unchanged():
    assert waifu_emoji_send("[emoji:nope] hi", {"happy": "/img/happy.png"}) == (None, "[emoji:nope] hi")
    assert waifu_emoji_send("plain", {}) == (None, "plain")


# ── series wiring ────────────────────────────────────────────────────────────

def test_token_maps_to_existing_file_sends_image_alone_then_leftover_text(tmp_path):
    img = tmp_path / "happy.png"
    img.write_bytes(b"\x89PNG\r\n")
    config = WaifuConfig(enabled=True, typing_speed=0.0, emojis_enabled=True,
                         emojis={"happy": str(img)})
    texts: list = []
    images: list = []
    stub = _make_stub(config=config, texts=texts, images=images)
    _run(stub._dispatch_final_text(
        _event(), "sk", "hi\n\n[emoji:happy] good morning", {}, False, 0, lambda _r: None))
    # 表情包单独成一条: the sticker is its own message (no caption), then the leftover text is the
    # very next text bubble in the series; the plain bubble before it is unaffected.
    assert texts == ["hi", "good morning"]
    assert images == [(str(img), None)]


def test_empty_leftover_sends_the_sticker_alone(tmp_path):
    img = tmp_path / "happy.png"
    img.write_bytes(b"\x89PNG\r\n")
    config = WaifuConfig(enabled=True, typing_speed=0.0, emojis_enabled=True,
                         emojis={"happy": str(img)})
    texts: list = []
    images: list = []
    stub = _make_stub(config=config, texts=texts, images=images)
    _run(stub._dispatch_final_text(
        _event(), "sk", "hi\n\n[emoji:happy]", {}, False, 0, lambda _r: None))
    assert texts == ["hi"]              # nothing left over: no empty text bubble
    assert images == [(str(img), None)]


def test_missing_image_logs_and_falls_back_to_text(tmp_path, caplog):
    missing = str(tmp_path / "gone.png")
    config = WaifuConfig(enabled=True, typing_speed=0.0, emojis_enabled=True,
                         emojis={"happy": missing})
    texts: list = []
    images: list = []
    stub = _make_stub(config=config, texts=texts, images=images)
    with caplog.at_level(logging.WARNING):
        _run(stub._dispatch_final_text(
            _event(), "sk", "hi\n\n[emoji:happy] yay", {}, False, 0, lambda _r: None))
    assert images == []                          # no image attempted
    assert texts == ["hi", "yay"]                # token stripped, words kept
    assert any("waifu emoji image missing" in r.getMessage() for r in caplog.records)


def test_unresolved_token_sends_text_without_the_token():
    config = WaifuConfig(enabled=True, typing_speed=0.0, emojis_enabled=True,
                         emojis={"happy": "/img/happy.png"})
    texts: list = []
    images: list = []
    stub = _make_stub(config=config, texts=texts, images=images)
    _run(stub._dispatch_final_text(
        _event(), "sk", "hi\n\n[emoji:unknown] hello there", {}, False, 0, lambda _r: None))
    assert images == []
    assert texts == ["hi", "hello there"]


def test_emojis_disabled_leaves_the_bubble_untouched(tmp_path):
    img = tmp_path / "happy.png"
    img.write_bytes(b"\x89PNG\r\n")
    config = WaifuConfig(enabled=True, typing_speed=0.0, emojis_enabled=False,
                         emojis={"happy": str(img)})
    texts: list = []
    images: list = []
    stub = _make_stub(config=config, texts=texts, images=images)
    _run(stub._dispatch_final_text(
        _event(), "sk", "hi\n\n[emoji:happy] bye", {}, False, 0, lambda _r: None))
    assert images == []
    assert texts == ["hi", "[emoji:happy] bye"]  # byte-for-byte, token retained


def test_sticker_send_failure_is_recorded_and_series_continues(tmp_path, caplog):
    img = tmp_path / "happy.png"
    img.write_bytes(b"\x89PNG\r\n")
    config = WaifuConfig(enabled=True, typing_speed=0.0, emojis_enabled=True,
                         emojis={"happy": str(img)})
    texts: list = []
    images: list = []
    stub = _make_stub(config=config, texts=texts, images=images, error="upload refused")
    recorded: list = []
    with caplog.at_level(logging.WARNING):
        _run(stub._dispatch_final_text(
            _event(), "sk", "[emoji:happy] one\n\nplain two", {}, False, 0, recorded.append))
    assert images == [(str(img), None)]          # image alone, no caption
    assert texts == ["one", "plain two"]         # leftover words still ride out; series kept going
    assert [r.success for r in recorded] == [False, True, True]
