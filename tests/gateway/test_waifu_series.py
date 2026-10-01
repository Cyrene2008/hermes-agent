"""Tests for native Waifu-Mode series delivery (功能B).

Contract under test:
  * ``waifu.enabled=false`` runs the single-send path unchanged;
  * with it on, one assistant turn is delivered as an ordered SEQUENCE of bubbles, split per the
    mapping rules (explicit ``---`` delimiter, else paragraph atoms with fences/tables kept whole),
    re-chunked at the platform cap;
  * the conversation log gets exactly one assistant row per bubble (agent-side split), no duplicate
    combined row;
  * a mid-series send failure stops the series and is swallowed (never raised into
    ``_notify_turn_error``), leaving the log as the pre-send bubble split.
"""

from __future__ import annotations

import asyncio

import gateway.run as gateway_run
from agent import session_persistence as sp
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent
from gateway.platforms.helpers import split_waifu_bubbles
from gateway.session import SessionSource, SessionStore
from hermes_cli.waifu_config import WaifuConfig


class _StubAdapter:
    """Borrows the real series methods without a transport."""

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


def _make_stub(*, enabled, sends, finals, cap=None, fail_at=None, raise_at=None, config=None):
    stub = _StubAdapter()
    stub._waifu_delivery_enabled = lambda _event: enabled
    stub._waifu_config = lambda _event: config if config is not None else WaifuConfig()
    stub.max_message_length_for_chat = lambda _chat_id: cap
    stub.message_len_fn_for_chat = lambda _chat_id: len
    stub._waifu_ledger_record = lambda **_kw: None  # ledger covered by test_waifu_ledger

    async def _fake_final(event, session_key, text, metadata, is_ephemeral, ttl, record):
        finals.append(text)

    async def _fake_ledger(event, session_key, text, metadata, *, reply_to, is_ephemeral_response=False):
        sends.append(text)
        if raise_at is not None and len(sends) == raise_at:
            raise RuntimeError("transport exploded")
        ok = fail_at is None or len(sends) != fail_at
        return SendResult(success=ok, error=None if ok else "rejected by platform",
                          message_id=f"m{len(sends)}"), stub

    stub._send_final_text = _fake_final
    stub.send_final_ledgered = _fake_ledger
    return stub


def _run(coro):
    return asyncio.run(coro)


# ── splitter semantics ───────────────────────────────────────────────────────

def test_paragraph_split_and_fences_tables_kept_whole():
    text = "first para\n\n```py\nprint('x')\n```\n\n| a | b |\n| 1 | 2 |"
    bubbles = split_waifu_bubbles(text)
    assert bubbles == ["first para", "```py\nprint('x')\n```", "| a | b |\n| 1 | 2 |"]


def test_explicit_delimiter_splits_only_on_it():
    # With a delimiter present, blank lines do NOT split; the delimiter line is consumed.
    bubbles = split_waifu_bubbles("one\ntwo\n---\nthree\n\nthree-b")
    assert bubbles == ["one\ntwo", "three\n\nthree-b"]


def test_delimiter_inside_fence_is_not_a_delimiter():
    text = "```\n---\n```"
    assert split_waifu_bubbles(text) == [text]


def test_oversize_bubble_rechunked_at_cap():
    long_para = "y" * 25
    bubbles = split_waifu_bubbles("short\n\n" + long_para, max_length=10)
    assert bubbles[0] == "short"
    assert all(len(b) <= 10 for b in bubbles[1:])
    assert "".join(bubbles[1:]) == long_para


# ── gateway routing ──────────────────────────────────────────────────────────

def test_disabled_path_is_the_unchanged_single_send():
    sends: list = []
    finals: list = []
    stub = _make_stub(enabled=False, sends=sends, finals=finals)
    event, meta = _event(), {"k": "v"}
    recorded: list = []
    _run(stub._dispatch_final_text(event, "sk", "one\n\ntwo", meta, False, 0, recorded.append))
    assert finals == ["one\n\ntwo"]  # the whole reply, one call
    assert sends == []
    assert recorded == []


def test_enabled_multi_bubble_sent_in_order():
    sends: list = []
    finals: list = []
    stub = _make_stub(enabled=True, sends=sends, finals=finals)
    _run(stub._dispatch_final_text(_event(), "sk", "one\n\ntwo\n\nthree", {}, False, 0, lambda _r: None))
    assert sends == ["one", "two", "three"]
    assert finals == []


def test_enabled_single_bubble_falls_back_to_single_send():
    sends: list = []
    finals: list = []
    stub = _make_stub(enabled=True, sends=sends, finals=finals)
    _run(stub._dispatch_final_text(_event(), "sk", "just one line", {}, False, 0, lambda _r: None))
    assert sends == []
    assert finals == ["just one line"]


def test_series_rechunks_oversize_bubble_for_the_wire():
    sends: list = []
    stub = _make_stub(enabled=True, sends=sends, finals=[], cap=10)
    long_para = "z" * 25
    _run(stub._dispatch_final_text(_event(), "sk", "hi\n\n" + long_para, {}, False, 0, lambda _r: None))
    assert sends[0] == "hi"
    assert len(sends) > 2
    assert all(len(part) <= 10 for part in sends[1:])


def test_partial_failure_stops_series_and_never_raises():
    sends: list = []
    stub = _make_stub(enabled=True, sends=sends, finals=[], fail_at=2)
    recorded: list = []
    # No exception escapes into the caller (which would become _notify_turn_error).
    _run(stub._dispatch_final_text(_event(), "sk", "one\n\ntwo\n\nthree", {}, False, 0, recorded.append))
    assert sends == ["one", "two"]  # third bubble never attempted
    assert [r.success for r in recorded] == [True, False]


def test_raised_send_is_recorded_and_stops_series():
    sends: list = []
    stub = _make_stub(enabled=True, sends=sends, finals=[], raise_at=2)
    recorded: list = []
    _run(stub._dispatch_final_text(_event(), "sk", "one\n\ntwo\n\nthree", {}, False, 0, recorded.append))
    assert sends == ["one", "two"]
    assert [r.success for r in recorded] == [True, False]
    assert "transport exploded" in recorded[-1].error


def test_enabled_flag_reads_gateway_config(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    stub = _StubAdapter()
    stub._waifu_delivery_enabled = BasePlatformAdapter._waifu_delivery_enabled.__get__(stub)
    event = _event()
    assert stub._waifu_delivery_enabled(event) is False  # no config file → OFF
    (tmp_path / "config.yaml").write_text("waifu:\n  enabled: true\n", encoding="utf-8")
    assert stub._waifu_delivery_enabled(event) is True
    (tmp_path / "config.yaml").write_text("waifu:\n  enabled: 'yes'\n", encoding="utf-8")
    assert stub._waifu_delivery_enabled(event) is False  # malformed → OFF


# ── transcript: one row per bubble (agent-side split) ────────────────────────

def _agent_stub(db, session_id):
    agent = object.__new__(type("AgentStub", (), {}))
    agent.session_id = session_id
    agent._session_db = db
    agent._session_db_created = True
    agent._persist_disabled = False
    agent._persist_user_message_idx = None
    agent._persist_user_message_timestamp = None
    agent._pending_cli_user_message = None
    agent._mute_notification_reply = False
    agent._flushed_db_message_session_id = session_id
    agent._flushed_db_message_ids = set()
    agent._last_flushed_db_idx = 0
    agent._db_flush_scan_prefix = []
    agent._session_persist_lock = None
    agent._active_compression_lock_holder = None
    agent._active_session_turn_lease_holder = None
    agent._active_session_turn_lease_ttl_seconds = 300.0
    return agent


def _store(tmp_path, chat_id):
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    entry = store.get_or_create_session(
        SessionSource(platform=Platform.TELEGRAM, chat_id=chat_id, user_id="u"))
    return store, entry, store._db_for_session_id(entry.session_id)


def _flush(agent, messages):
    sp.SessionPersistenceMixin._flush_messages_to_session_db_unlocked(agent, messages)


def test_one_transcript_row_per_bubble_no_duplicate(tmp_path):
    store, entry, db = _store(tmp_path, "waifu-rows")
    sid = entry.session_id
    agent = _agent_stub(db, sid)
    agent._waifu_series_enabled_cache = True
    combined = "one\n\ntwo\n\nthree"
    messages = [{"role": "assistant", "content": combined}]
    _flush(agent, messages)
    rows = [m for m in db.get_messages(sid) if m["role"] == "assistant"]
    assert [r["content"] for r in rows] == ["one", "two", "three"]
    # Dedup marker set on the live dict → a second flush writes nothing more.
    _flush(agent, messages)
    rows = [m for m in db.get_messages(sid) if m["role"] == "assistant"]
    assert [r["content"] for r in rows] == ["one", "two", "three"]
    assert combined not in [r["content"] for r in rows]  # no combined row
    db.close()


def test_disabled_agent_persists_a_single_row(tmp_path):
    store, entry, db = _store(tmp_path, "waifu-off")
    sid = entry.session_id
    agent = _agent_stub(db, sid)
    agent._waifu_series_enabled_cache = False
    combined = "one\n\ntwo\n\nthree"
    _flush(agent, [{"role": "assistant", "content": combined}])
    rows = [m for m in db.get_messages(sid) if m["role"] == "assistant"]
    assert [r["content"] for r in rows] == [combined]
    db.close()
