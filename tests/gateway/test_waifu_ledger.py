"""Tests for the native Waifu-Mode delivery ledger (功能B).

Contract under test:
  * :data:`WAIFU_LEDGER_MAX_BYTES` is 32 MiB minus the documented 4 KiB headroom;
  * appends are JSONL — one parseable object per line, in order;
  * a write that would cross the cap rotates the full file to a single ``.1`` backup first;
  * the writer never raises (a bad destination is a ``False`` return);
  * ``_send_waifu_series`` ledgers every delivered bubble and sticker as it goes out.
"""

from __future__ import annotations

import asyncio
import json
import types

import gateway.platforms.waifu_ledger as waifu_ledger
from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent
from gateway.platforms.waifu_ledger import (
    WAIFU_LEDGER_MAX_BYTES, append_waifu_ledger, default_ledger_path)
from gateway.session import SessionSource
from hermes_cli.waifu_config import WaifuConfig


def test_cap_is_32mib_minus_4kib_headroom():
    assert WAIFU_LEDGER_MAX_BYTES == 33550336
    assert WAIFU_LEDGER_MAX_BYTES == 32 * 1024 * 1024 - 4096


def test_append_writes_ordered_jsonl(tmp_path):
    ledger = tmp_path / "waifu_ledger.jsonl"
    assert append_waifu_ledger({"n": 1}, path=ledger) is True
    assert append_waifu_ledger({"n": 2}, path=ledger) is True
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["n"] for line in lines] == [1, 2]


def test_rotation_keeps_one_backup_and_a_fresh_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(waifu_ledger, "WAIFU_LEDGER_MAX_BYTES", 150)
    ledger = tmp_path / "waifu_ledger.jsonl"
    record = lambda n: {"n": n, "pad": "x" * 80}  # ~100 bytes/line
    append_waifu_ledger(record(0), path=ledger)
    append_waifu_ledger(record(1), path=ledger)  # 100 + 100 > 150 → rotate
    backup = tmp_path / "waifu_ledger.jsonl.1"
    assert backup.exists()
    assert [json.loads(l)["n"] for l in backup.read_text(encoding="utf-8").splitlines()] == [0]
    assert [json.loads(l)["n"] for l in ledger.read_text(encoding="utf-8").splitlines()] == [1]


def test_default_path_honours_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom.jsonl"
    monkeypatch.setenv("HERMES_WAIFU_LEDGER", str(target))
    assert default_ledger_path() == target


def test_bad_destination_returns_false_without_raising(tmp_path):
    as_dir = tmp_path / "adir"
    as_dir.mkdir()
    assert append_waifu_ledger({"n": 1}, path=as_dir) is False


# ── series wiring ────────────────────────────────────────────────────────────

class _StubAdapter:
    name = "stub"
    _dispatch_final_text = BasePlatformAdapter._dispatch_final_text
    _send_waifu_series = BasePlatformAdapter._send_waifu_series
    _send_waifu_sticker = BasePlatformAdapter._send_waifu_sticker
    _waifu_ledger_record = BasePlatformAdapter._waifu_ledger_record
    _waifu_transport_chunks = BasePlatformAdapter._waifu_transport_chunks
    _waifu_interrupt_guard = BasePlatformAdapter._waifu_interrupt_guard
    _waifu_config = BasePlatformAdapter._waifu_config
    _waifu_delivery_enabled = BasePlatformAdapter._waifu_delivery_enabled


def _event() -> MessageEvent:
    return MessageEvent(
        text="hi",
        source=SessionSource(platform=Platform.QQBOT, chat_id="c1", user_id="u"),
    )


def _stub(*, config, texts=None, images=None):
    stub = _StubAdapter()
    stub._waifu_delivery_enabled = lambda _event: True
    stub._waifu_config = lambda _event: config
    stub.max_message_length_for_chat = lambda _chat_id: None
    stub.message_len_fn_for_chat = lambda _chat_id: len
    texts = texts if texts is not None else []
    images = images if images is not None else []

    async def _fake_final(event, session_key, text, metadata, is_ephemeral, ttl, record):
        texts.append(text)

    async def _fake_ledger(event, session_key, text, metadata, *, reply_to, is_ephemeral_response=False):
        texts.append(text)
        return SendResult(success=True, message_id=f"t{len(texts)}"), stub

    async def _fake_image(chat_id, image_path, caption=None, reply_to=None, metadata=None, **kwargs):
        images.append((image_path, caption))
        return SendResult(success=True, message_id=f"i{len(images)}")

    stub._send_final_text = _fake_final
    stub.send_final_ledgered = _fake_ledger
    stub.send_image_file = _fake_image
    return stub, texts, images


def test_series_ledgers_every_bubble_and_sticker(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_WAIFU_LEDGER", str(tmp_path / "waifu_ledger.jsonl"))
    img = tmp_path / "happy.png"
    img.write_bytes(b"\x89PNG\r\n")
    config = WaifuConfig(enabled=True, typing_speed=0.0, emojis_enabled=True,
                         emojis={"happy": str(img)})
    stub, _, _ = _stub(config=config)
    asyncio.run(stub._dispatch_final_text(
        _event(), "sk", "one\n\n[emoji:happy] two words\n\nthree", {}, False, 0, lambda _r: None))

    lines = [json.loads(l) for l in (tmp_path / "waifu_ledger.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [(r["kind"], r["index"], r["success"]) for r in lines] == [
        ("text", 1, True), ("sticker", 2, True), ("text", 2, True), ("text", 3, True)]
    assert [r["total"] for r in lines] == [3, 3, 3, 3]
    assert lines[1]["chars"] == 0                     # the sticker image carries no text
    assert lines[2]["chars"] == len("two words")      # the leftover text is its own bubble
    # Metadata only — the ledger never stores the chat text.
    assert "text" not in lines[0] and "content" not in lines[0]
