"""Shared helpers for gateway platform adapters: message dedup, markdown
stripping, thread participation tracking, GFM table → bullets, mention-pattern
compilation, and fence-aware markdown chunking."""

import asyncio
import contextlib
import json
import logging
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, MutableMapping, Optional
from gateway.platforms.event import MessageEvent
from utils import atomic_json_write

logger = logging.getLogger(__name__)


class MessageDeduplicator:
    """TTL-based message deduplication cache (``if dedup.is_duplicate(msg_id): return``)."""

    def __init__(self, max_size: int = 2000, ttl_seconds: float = 300):
        self._seen: dict[str, float] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds

    def is_duplicate(self, msg_id: str) -> bool:
        """Return True if *msg_id* was already seen within the TTL window."""
        if not msg_id:
            return False
        now = time.time()
        if msg_id in self._seen:
            if now - self._seen[msg_id] < self._ttl:
                return True
            del self._seen[msg_id]  # expired: treat as new
        self._seen[msg_id] = now
        if len(self._seen) > self._max_size:
            cutoff = now - self._ttl
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
            if len(self._seen) > self._max_size:
                # All entries still fresh: keep the newest so max_size holds under load.
                self._seen = dict(sorted(self._seen.items(), key=lambda item: item[1])[-self._max_size:])
        return False

    def contains(self, msg_id: str) -> bool:
        """Return whether *msg_id* is live in the cache without inserting it."""
        seen_at = self._seen.get(msg_id) if msg_id else None
        if seen_at is None:
            return False
        if time.time() - seen_at < self._ttl:
            return True
        del self._seen[msg_id]
        return False

    def discard(self, msg_id: str) -> None:
        """Release a claimed message ID after cancelled/failed handoff."""
        self._seen.pop(msg_id, None)

    def clear(self):
        self._seen.clear()


# Worker-thread handoff used by the off-loop persist paths.  A module attribute
# so tests can replace THIS seam instead of patching ``asyncio.to_thread``
# globally.
_to_thread = asyncio.to_thread


async def cancel_task(task: Optional[asyncio.Task]) -> None:
    """Cancel *task* and wait for it to unwind. ``None``/finished tasks are no-ops; awaiting the
    current task would deadlock, so a self-cancel only requests cancellation. Exceptions the task
    dies with are swallowed: at teardown nobody is left to handle them."""
    if task is None or task.done():
        return
    task.cancel()
    if task is not asyncio.current_task():
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def bounded_put(store: MutableMapping[str, Any], key: str, value: Any, cap: int) -> None:
    """Insert into an insertion-ordered mapping with a hard size bound, evicting the oldest keys. A
    re-put moves the key to the newest position so live entries outlast stale ones."""
    store.pop(key, None)
    store[key] = value
    while len(store) > cap:
        del store[next(iter(store))]


# Markdown-stripping rules, applied in order: bold, italic, bold/italic underscore,
# code fence markers, inline code, headings, links, then newline squeeze.
_STRIP_RULES = (
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"\*(.+?)\*", re.DOTALL), r"\1"),
    (re.compile(r"\b__(?![\s_])(.+?)(?<![\s_])__\b", re.DOTALL), r"\1"),
    (re.compile(r"\b_(?![\s_])(.+?)(?<![\s_])_\b", re.DOTALL), r"\1"),
    (re.compile(r"```[a-zA-Z0-9_+-]*\n?"), ""),
    (re.compile(r"`(.+?)`"), r"\1"),
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
    (re.compile(r"\[([^\]]+)\]\([^\)]+\)"), r"\1"),
    (re.compile(r"\n{3,}"), "\n\n"),
)


def strip_markdown(text: str) -> str:
    """Strip markdown formatting for plain-text platforms (SMS, iMessage, etc.)."""
    for pattern, repl in _STRIP_RULES:
        text = pattern.sub(repl, text)
    return text.strip()


class ThreadParticipationTracker:
    """Persistent set of threads the bot has participated in (``<platform>_threads.json``);
    ``thread_id in tracker`` checks membership, ``tracker.mark(thread_id)`` persists."""

    _MAX_TRACKED = 500

    def __init__(self, platform_name: str, max_tracked: int = 500):
        self._platform = platform_name
        self._max_tracked = max_tracked
        # ``mark_async`` runs the persist on a worker thread, which removes the
        # accidental serialization the event loop used to provide.  Two locks,
        # never nested the other way round:
        #   ``_lock``    guards ONLY the in-memory set (``_remember``,
        #                ``__contains__``, ``clear`` and the snapshot in
        #                ``_save``); held for microseconds, safe on the loop.
        #   ``_io_lock`` worker-only; serializes snapshot+write in ``_save`` so
        #                two persists cannot interleave and lose an entry.
        # ``_save`` must NOT hold ``_lock`` across ``os.replace``: the adapters'
        # ``thread_id in tracker`` gate runs on the loop thread and would stall
        # for the whole rename.
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._threads: dict[str, None] = dict.fromkeys(str(t) for t in self._load())

    def _state_path(self) -> Path:
        from hermes_constants import get_hermes_home
        return get_hermes_home() / f"{self._platform}_threads.json"

    def _load(self) -> list[str]:
        try:
            data = json.loads(self._state_path().read_text(encoding="utf-8"))
        except Exception:
            return []
        return [str(thread_id) for thread_id in data] if isinstance(data, list) else []

    def _save(self) -> None:
        with self._io_lock:
            with self._lock:
                thread_list = list(self._threads)
                if len(thread_list) > self._max_tracked:
                    thread_list = thread_list[-self._max_tracked:]
                    self._threads = dict.fromkeys(thread_list)
            atomic_json_write(self._state_path(), thread_list, indent=None)

    def _remember(self, thread_id: str) -> bool:
        """Record *thread_id* in memory; ``True`` when a persist is still owed.

        The in-memory half stays synchronous even for :meth:`mark_async` so that
        a ``thread_id in tracker`` check immediately after marking is correct --
        the mention-gating logic in the adapters depends on that.
        """
        with self._lock:
            if thread_id in self._threads:
                return False
            self._threads[thread_id] = None
            return True

    def mark(self, thread_id: str) -> None:
        """Mark *thread_id* as participated and persist.

        Blocking: ends in ``atomic_json_write`` -> ``os.replace``.  Coroutines
        must use :meth:`mark_async` instead.
        """
        if self._remember(thread_id):
            self._save()

    async def mark_async(self, thread_id: str) -> None:
        """Off-loop form of :meth:`mark`.

        ``_save`` ends in ``atomic_json_write`` -> ``os.replace``, whose
        duration is unbounded under filesystem pressure.  Every caller of this
        tracker sits on an inbound-message coroutine in the Discord and Matrix
        adapters, so the rename must not be paid inline on the event loop -- it
        stalls every other adapter's polling and every in-flight turn for as
        long as it runs.
        """
        if self._remember(thread_id):
            await _to_thread(self._save)

    def __contains__(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._threads

    def clear(self) -> None:
        with self._lock:
            self._threads.clear()


def redact_phone(phone: str) -> str:
    """Redact a phone number for logging, preserving country code and last 4."""
    if not phone:
        return "<none>"
    if len(phone) <= 8:
        return phone[:2] + "****" + phone[-2:] if len(phone) > 4 else "****"
    return phone[:4] + "****" + phone[-4:]


# ─── GFM table → bullets. Discord calls convert_table_to_bullets(); Telegram imports the
# primitives but keeps its own MarkdownV2-aware renderer.
# Delimiter row: optional outer pipes, dash cells (optional alignment colons). Requires at
# least one internal '|' so a lone '---' rule is NOT matched.
TABLE_SEPARATOR_RE = re.compile(r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$')


def is_table_row(line: str) -> bool:
    """Return True if *line* could plausibly be a table data row."""
    return '|' in line.strip()


def split_markdown_table_row(line: str) -> list[str]:
    """Split a GFM table row into stripped cells (delegates to agent.markdown_tables)."""
    from agent.markdown_tables import split_table_row
    return split_table_row(line)


def _render_table_block(table_block: list[str]) -> str:
    """Render a GFM table as bold-heading + bullet groups (same alignment logic as Telegram's
    renderer: without a row-label column the full row is data and the heading bullet is skipped)."""
    headers = split_markdown_table_row(table_block[0]) if len(table_block) >= 3 else []
    if len(headers) < 2:
        return "\n".join(table_block)
    has_row_label_col = len(split_markdown_table_row(table_block[2])) == len(headers) + 1
    rendered_groups: list[str] = []
    for index, row in enumerate(table_block[2:], start=1):
        cells = split_markdown_table_row(row)
        if has_row_label_col:
            heading = cells[0] if cells and cells[0] else f"Row {index}"
            data_cells = cells[1:]
        else:
            heading = next((cell for cell in cells if cell), f"Row {index}")
            data_cells = cells
        data_cells = (data_cells + [""] * len(headers))[: len(headers)]
        bullets = [f"• {header}: {value}" for header, value in zip(headers, data_cells)
                   if has_row_label_col or value != heading]
        rendered_groups.append("\n".join([f"**{heading}**", *bullets]))
    return "\n\n".join(rendered_groups)


def convert_table_to_bullets(text: str) -> str:
    """Rewrite GFM pipe tables into bold-heading + bullet groups; fenced code is left alone."""
    if '|' not in text or '-' not in text:
        return text
    lines = text.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        is_fence_line = line.lstrip().startswith('```')
        in_fence ^= is_fence_line
        if not (in_fence or is_fence_line) and '|' in line and i + 1 < len(lines) \
                and TABLE_SEPARATOR_RE.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and is_table_row(lines[j]):
                j += 1
            out.append(_render_table_block(lines[i:j]))
            i = j
        else:
            out.append(line)
            i += 1
    return '\n'.join(out)


def compile_mention_patterns(raw, *, log_prefix: str, platform_label: str | None = None,
                             display_label: str | None = None, defaults: 'list[str] | None' = None,
                             logger_: 'logging.Logger | None' = None) -> 'list[re.Pattern]':
    """Compile regex wake-word/mention patterns from config or env values.

    * **Config-style** (dingtalk, telegram): pass ``platform_label``. ``raw`` must be a
      list or string (else warn + ``[]``); non-string entries skipped; info log on load.
    * **Wakeword-style** (photon, bluebubbles): pass ``defaults``. ``raw`` may be None
      (defaults), a string (JSON list or comma/newline separated), a list, or a scalar.
    ``log_prefix`` is interpolated into every log line so per-adapter output stays
    byte-identical to the historical inline implementations.
    """
    log = logger_ or logger

    def _compile(patterns, warn_fmt, *warn_args):
        compiled: list[re.Pattern] = []
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                log.warning(warn_fmt, log_prefix, *warn_args, pattern, exc)
        return compiled

    if platform_label is not None:
        display = display_label or platform_label
        if raw is None:
            return []
        patterns = [raw] if isinstance(raw, str) else raw
        if not isinstance(patterns, list):
            log.warning("[%s] %s mention_patterns must be a list or string; got %s",
                        log_prefix, platform_label, type(patterns).__name__)
            return []
        compiled = _compile([p for p in patterns if isinstance(p, str) and p.strip()],
                            "[%s] Invalid %s mention pattern %r: %s", display)
        if compiled:
            log.info("[%s] Loaded %d %s mention pattern(s)", log_prefix, len(compiled), display)
        return compiled
    if raw is None:
        patterns = list(defaults or [])
    elif isinstance(raw, str):
        text = raw.strip()
        try:
            loaded = json.loads(text) if text else []
        except Exception:
            loaded = None
        patterns = loaded if isinstance(loaded, list) else [
            part.strip() for line in text.splitlines() for part in line.split(",")]
    else:
        patterns = raw if isinstance(raw, list) else [raw]
    texts = [t for t in (str(p).strip() for p in patterns) if t]
    return _compile(texts, "[%s] Invalid mention pattern %r: %s")


# ─── Fence-Aware Markdown Chunking ───────────────────────────────────────────
# Shared core for gateway/stream_consumer.py (``prefer_paragraphs=False, balance_fences=True``),
# yuanbao (``prefer_paragraphs=True, balance_fences=False``) and weixin (``greedy_pack_blocks``).


def text_has_unclosed_fence(text: str) -> bool:
    """Return True when *text* ends inside an unclosed ``` code fence."""
    return sum(line.startswith('```') for line in text.split('\n')) % 2 == 1


def text_ends_with_table_row(text: str) -> bool:
    """True when the last non-empty line starts and ends with ``|``."""
    return _is_pipe_row(text.rstrip().split('\n')[-1])


def is_fence_atom(text: str) -> bool:
    """True when an atomic block is a code block (starts with ```)."""
    return text.lstrip().startswith('```')


def _is_pipe_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith('|') and stripped.endswith('|')


def is_table_atom(text: str) -> bool:
    """True when an atomic block is a table (first line is ``|...|``)."""
    return _is_pipe_row(text.split('\n')[0])


_SENTENCE_END_NEWLINE_RE = re.compile(r'[。！？.!?]\n')


def _cp_budget(text, budget, len_fn):
    """Code-point count of the longest prefix of *text* within *budget* ``len_fn`` units
    (callers guarantee ``len_fn(text) > budget``, so plain ``len`` needs no search)."""
    if len_fn is len:
        return budget
    from gateway.platforms.base import _custom_unit_to_cp  # heavyweight; lazy
    return _custom_unit_to_cp(text, budget, len_fn)


def split_at_paragraph_boundary(text, max_chars, len_fn=None):
    """Split at the nearest paragraph boundary within *max_chars*; return (head, tail).

    Priority: blank line → newline after sentence-ending punctuation (CJK/ASCII)
    → last newline → forced split at the window boundary. ``head + tail == text``
    always holds. *len_fn* measures in custom units (e.g. UTF-16 code units).
    """
    _len = len_fn or len
    if _len(text) <= max_chars:
        return text, ''
    window = text[:_cp_budget(text, max_chars, _len)]
    pos = window.rfind('\n\n')
    sentence_ends = [m.end() for m in _SENTENCE_END_NEWLINE_RE.finditer(window)]
    cut = pos + 2 if pos > 0 else (sentence_ends[-1] if sentence_ends else 0)
    if not cut:
        pos = window.rfind('\n')
        cut = pos + 1 if pos > 0 else len(window)
    return text[:cut], text[cut:]


def split_markdown_atoms(text: str) -> "list[str]":
    """Split markdown into indivisible atoms: fenced code blocks, tables
    (consecutive ``|...|`` lines) and paragraphs. Blank lines belong to no atom."""
    atoms: "list[str]" = []
    current_lines: "list[str]" = []
    in_fence = False

    def _flush_current() -> None:
        if current_lines:
            atom = '\n'.join(current_lines)
            if atom.strip():
                atoms.append(atom)
            current_lines.clear()

    for line in text.split('\n'):
        if in_fence:
            current_lines.append(line)
            if line.startswith('```'):
                in_fence = False
                _flush_current()
        elif line.startswith('```'):
            _flush_current()
            in_fence = True
            current_lines.append(line)
        elif line.strip() == '':
            _flush_current()
        else:
            # A table line and a non-table line never share an atom.
            if current_lines and _is_pipe_row(current_lines[-1]) != _is_pipe_row(line):
                _flush_current()
            current_lines.append(line)
    _flush_current()
    return atoms


#: Standalone line the model may emit to end a bubble — the same token the streaming frame
#: composer joins with (``gateway/stream_consumer.py``), so the contract is one delimiter.
WAIFU_BUBBLE_DELIMITER = "---"


def _split_waifu_on_delimiter(text: str) -> "list[str] | None":
    """Pieces split on standalone ``---`` lines (consumed); ``None`` when no delimiter is present.

    A ``---`` inside a fenced code block is code, never a delimiter.
    """
    pieces: "list[str]" = []
    current: "list[str]" = []
    in_fence = False
    found = False
    for line in text.split("\n"):
        stripped = line.strip()
        if in_fence:
            current.append(line)
            if stripped.startswith("```"):
                in_fence = False
            continue
        if stripped.startswith("```"):
            in_fence = True
            current.append(line)
            continue
        if stripped == WAIFU_BUBBLE_DELIMITER:
            found = True
            pieces.append("\n".join(current).strip())
            current = []
            continue
        current.append(line)
    if not found:
        return None
    pieces.append("\n".join(current).strip())
    return pieces


def split_waifu_bubbles(text, max_length=None, len_fn=None) -> "list[str]":
    """Split one assistant reply into Waifu-Mode chat bubbles.

    An explicit delimiter — a standalone ``---`` line — switches to deterministic splitting: only
    those lines cut the reply, so the model fully controls its bubble count. Without one, the reply
    splits on paragraph atoms via :func:`split_markdown_atoms`, so fenced code and tables stay
    whole. Empty pieces are dropped.

    *max_length* (platform cap in ``len_fn`` units) re-chunks any remaining oversize bubble with
    :func:`split_text_fence_aware`, closing/reopening fences so each send renders standalone.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    _len = len_fn or len
    pieces = _split_waifu_on_delimiter(text)
    if pieces is None:
        pieces = split_markdown_atoms(text)
    bubbles = [piece for piece in pieces if piece and piece.strip()]
    if max_length:
        re_chunked: "list[str]" = []
        for bubble in bubbles:
            if _len(bubble) > max_length:
                re_chunked.extend(
                    chunk for chunk in split_text_fence_aware(
                        bubble, max_length, len_fn, balance_fences=True) if chunk and chunk.strip())
            else:
                re_chunked.append(bubble)
        bubbles = re_chunked
    return bubbles


# ── Waifu-Mode pacing + stickers (see hermes_cli/waifu_config.py for the section) ─────────────

#: ``[emoji:NAME]`` marks a bubble as a sticker; NAME keys the ``waifu.emojis`` mapping.
WAIFU_EMOJI_TOKEN_RE = re.compile(r"\[emoji:([^\]\s]+)\]")


def waifu_emoji_send(text, emojis) -> "tuple[str | None, str]":
    """Resolve the first ``[emoji:NAME]`` token of one bubble against ``waifu.emojis``.

    Returns ``(image_path, remainder)``: the first NAME that maps to a path becomes the sticker
    image, and ``remainder`` is the bubble's remaining text with ALL tokens stripped. The caller
    delivers them as TWO messages — 表情包单独成一条: the image goes out ALONE, then ``remainder``
    (when non-empty) as the bubble's own next text bubble. ``(None, text)`` when no token resolves,
    so a bubble the user did not map goes out as plain text — the model's words are never dropped.
    """
    if not isinstance(text, str) or not emojis:
        return None, text
    for name in WAIFU_EMOJI_TOKEN_RE.findall(text):
        path = emojis.get(name)
        if isinstance(path, str) and path:
            return path, WAIFU_EMOJI_TOKEN_RE.sub("", text).strip()
    return None, text


#: Trailing sentence-READING characters the ``strip_trailing_punctuation`` switch removes from an
#: outbound Waifu bubble: the period/comma/semicolon tails (CJK and ASCII) plus trailing whitespace.
#: Emotion-bearing marks are deliberately ABSENT — ``？ ? ！ ! … ~ ～ ♪`` and emoji are not here, so
#: the scan below stops on them and a question/exclamation/ellipsis ending survives verbatim.
WAIFU_STRIPPABLE_TAIL_CHARS = "。．.，,、;； \t\n\r\x0b\x0c\u3000"


def strip_waifu_trailing_punctuation(text):
    """Drop ONLY sentence-reading tail characters from one outbound Waifu bubble (句末标点处理).

    去掉句读类结尾（。．. ／ ，, ／ 、 ／ ; ； 以及尾部空白），遇到第一个承载情绪或语义的字符就
    停：``？ ? ！ ! …``（含 ``……？`` ``？？`` ``！！`` 这类重复）、``~ ～ ♪``、emoji 以及任何不
    在去除集里的字符全部原样保留 —— ``好呀。`` -> ``好呀``，``我服了。`` -> ``我服了``，而
    ``真的吗？`` / ``……`` / ``啊！`` / ``好呀♪`` / ``好呀😊`` 一律不变。

    只影响出站显示：对话日志在发送前就按原文写入，这里改不到它。整条气泡若全是被去除的标点则原样
    返回，绝不产出空串（网关侧会丢弃空气泡）。
    """
    if not isinstance(text, str) or not text:
        return text
    end = len(text)
    while end > 0 and text[end - 1] in WAIFU_STRIPPABLE_TAIL_CHARS:
        end -= 1
    if end == 0:
        return text
    return text[:end]


def waifu_pace_delay(text, typing_speed, jitter_ms=0, *, uniform: Callable[[float, float], float] = random.uniform) -> float:
    """Seconds to wait before sending one Waifu-Mode bubble at ``typing_speed`` chars/sec.

    ``typing_speed <= 0`` is the instant path: no delay, not even jitter. Otherwise the wait is
    ``len(text) / typing_speed`` plus a uniform +/- ``jitter_ms`` of human typing wobble, clamped
    at 0 so one unlucky draw can never turn a wait negative.
    """
    if typing_speed <= 0:
        return 0.0
    delay = len(text) / typing_speed
    if jitter_ms > 0:
        delay += uniform(-jitter_ms, jitter_ms) / 1000.0
    return max(0.0, delay)


async def wait_or_interrupt(delay: float, guard: Optional[asyncio.Event] = None) -> bool:
    """Sleep *delay* seconds for typing pacing, cut short when *guard* is set.

    *guard* is the session's interrupt event, set when a new inbound user message arrives (steer):
    an already-planned series must not keep the delivery path asleep after the user has spoken
    again. Returns True when the full delay elapsed, False when the guard fired first. Task
    cancellation propagates untouched, so a cancelled turn never waits on the sleep.
    """
    if delay <= 0:
        return True
    if guard is None:
        await asyncio.sleep(delay)
        return True
    try:
        await asyncio.wait_for(guard.wait(), timeout=delay)
        return False
    except asyncio.TimeoutError:
        return True


def infer_block_separator(prev_chunk: str, next_chunk: str) -> str:
    """``'\\n'`` when the boundary sits at a code fence or a continued table, else ``'\\n\\n'``."""
    prev_trimmed = prev_chunk.rstrip()
    next_trimmed = next_chunk.lstrip()
    if prev_trimmed.endswith('```') or next_trimmed.startswith('```'):
        return '\n'
    if text_ends_with_table_row(prev_chunk) and next_trimmed and _is_pipe_row(next_trimmed.split('\n')[0]):
        return '\n'
    return '\n\n'


def merge_streaming_fences(chunks: "list[str]") -> "list[str]":
    """Rejoin chunks truncated mid-fence: while chunk *i* has an unclosed fence
    and a successor exists, merge the successor in via :func:`infer_block_separator`."""
    result: "list[str]" = []
    i = 0
    while i < len(chunks):
        current = chunks[i]
        while text_has_unclosed_fence(current) and i + 1 < len(chunks):
            current = current + infer_block_separator(current, chunks[i + 1]) + chunks[i + 1]
            i += 1
        result.append(current)
        i += 1
    return result


def balance_fences_across_chunks(chunks: "list[str]") -> "list[str]":
    """Close orphaned ``` fences at each chunk boundary and reopen (with the
    original language tag) on the next, so every chunk is fence-balanced alone."""
    if len(chunks) <= 1:
        return chunks
    out: "list[str]" = []
    carry_lang = None
    for chunk in chunks:
        body = f"```{carry_lang}\n{chunk}" if carry_lang is not None else chunk
        in_code, lang = fence_state_after(chunk, carry_lang is not None, carry_lang or "")
        carry_lang = lang if in_code else None
        out.append(body + "\n```" if in_code else body)
    return out


def fence_state_after(text: str, in_code: bool = False, lang: str = "") -> "tuple[bool, str]":
    """Walk ``text`` line by line toggling on ``` lines; return the final (in_code, lang)."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            tag = stripped[3:].split()
            in_code, lang = (False, "") if in_code else (True, tag[0] if tag else "")
    return in_code, lang


def greedy_pack_blocks(blocks, max_length, len_fn=None, sep="\n\n", overflow=None):
    """Greedily pack *blocks* (joined with *sep*) into chunks of at most *max_length*; an
    oversized block goes through *overflow(block)* (-> list of chunks) if given, else as-is."""
    _len = len_fn or len
    packed: "list[str]" = []
    current = ""
    for block in blocks:
        candidate = block if not current else f"{current}{sep}{block}"
        if _len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            packed.append(current)
            current = ""
        if _len(block) <= max_length:
            current = block
        elif overflow is not None:
            packed.extend(overflow(block))
        else:
            packed.append(block)
    if current:
        packed.append(current)
    return packed


def split_text_fence_aware(text, limit, len_fn=None, *, prefer_paragraphs=True,
                           balance_fences=False):
    """Split markdown into chunks of at most *limit*, respecting fences.

    ``prefer_paragraphs=True`` (yuanbao-derived): atoms (fences, tables, paragraphs) are
    greedily merged, oversized non-atomic chunks split at paragraph boundaries, small
    neighbours re-merged; a single atom larger than *limit* is emitted oversize, not broken.
    ``prefer_paragraphs=False`` (stream_consumer-derived): newline-preferred hard splitting
    with headroom reserved for fence markers. ``balance_fences=True`` closes/reopens fences
    at chunk boundaries so each chunk renders standalone.
    """
    if not text:
        return []
    if prefer_paragraphs:
        chunks = _chunk_markdown_paragraphs(text, limit, len_fn)
    else:
        chunks = _chunk_newline_preferred(text, limit, len_fn or len)
    return balance_fences_across_chunks(chunks) if balance_fences else chunks


def _chunk_markdown_paragraphs(text, max_chars, len_fn=None):
    """Yuanbao-derived paragraph/atom chunking pipeline (see module docs)."""
    _len = len_fn or len
    if _len(text) <= max_chars:
        return [text]
    # Phase 2: greedy merge; oversized fence/table atoms stay indivisible.
    chunks: "list[str]" = []
    indivisible_set: "set[int]" = set()
    current_parts: "list[str]" = []
    current_len = 0
    for atom in split_markdown_atoms(text):
        atom_len = _len(atom)
        sep_len = 2 if current_parts else 0
        if current_len + sep_len + atom_len > max_chars and current_parts:
            chunks.append('\n\n'.join(current_parts))
            current_parts, current_len, sep_len = [], 0, 0
        if (not current_parts and atom_len > max_chars
                and (is_fence_atom(atom) or is_table_atom(atom))):
            indivisible_set.add(len(chunks))
            chunks.append(atom)
            continue
        current_parts.append(atom)
        current_len += sep_len + atom_len
    if current_parts:
        chunks.append('\n\n'.join(current_parts))
    # Phase 3: split still-oversized divisible chunks at paragraph boundaries.
    result: "list[str]" = []
    for idx, chunk in enumerate(chunks):
        if _len(chunk) <= max_chars or idx in indivisible_set or text_has_unclosed_fence(chunk):
            result.append(chunk)
            continue
        remaining = chunk
        while _len(remaining) > max_chars:
            head, remaining = split_at_paragraph_boundary(remaining, max_chars, len_fn=len_fn)
            if not head:
                head, remaining = remaining[:max_chars], remaining[max_chars:]
            if head:
                result.append(head)
        if remaining:
            result.append(remaining)
    # Phase 4: merge small chunks with neighbours.
    merged: "list[str]" = result[:1]
    for chunk in result[1:]:
        combined = merged[-1] + '\n\n' + chunk
        if _len(combined) <= max_chars:
            merged[-1] = combined
        else:
            merged.append(chunk)
    return [c for c in merged if c]


def _chunk_newline_preferred(text, limit, len_fn):
    """Stream-consumer-derived newline-preferred splitting (no balancing)."""
    if len_fn(text) <= limit:
        return [text]
    # Reserve headroom for fence markers a balancing pass may add.
    split_limit = max(limit - 16, limit // 2, 1) if "```" in text else limit
    chunks: "list[str]" = []
    remaining = text
    while len_fn(remaining) > split_limit:
        budget = _cp_budget(remaining, split_limit, len_fn)
        split_at = remaining.rfind("\n", 0, budget)
        if split_at < budget // 2:
            split_at = budget
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


# ─── Discord channel obfuscation (Bot API change, mandatory Nov 16 2026) ───────
# Channels a bot lacks VIEW_CHANNEL on are still dispatched over the Gateway with the
# name replaced by "___hidden___", sensitive fields nulled and flag 1 << 17 set; over
# HTTP they are omitted. https://discord.com/developers/docs/change-log (Aug 12, 2026)
DISCORD_CHANNEL_OBFUSCATED_FLAG = 1 << 17
DISCORD_OBFUSCATED_CHANNEL_NAME = "___hidden___"


def is_discord_channel_obfuscated(channel) -> bool:
    """True when a discord.py channel object is an obfuscated placeholder.

    Skip these wherever guild channels are enumerated (channel directory, message
    backfill): history reads and sends always fail with a permission error. Flag
    first; the sentinel name is the fallback for discord.py builds that don't
    expose the flag (a visible channel literally named ``___hidden___`` is then
    also skipped — deliberate bias toward hiding). #90154
    """
    try:
        flag_value = channel.flags.value
    except AttributeError:
        flag_value = None
    if isinstance(flag_value, int) and flag_value & DISCORD_CHANNEL_OBFUSCATED_FLAG:
        return True
    return getattr(channel, "name", None) == DISCORD_OBFUSCATED_CHANNEL_NAME


# ---- BEGIN PLUGIN-COMPAT (revert-scheduled; see COMPAT_MANIFEST.md) ----
# Names external plugins imported from this module before the Sep 2026 decomposition.
# Internal code MUST NOT use these (scripts/check_compat_pointers.py fails CI if it does).
# The whole block is removed by reverting the commit that added it.
from typing import Dict  # noqa: F401,E402
from typing import TYPE_CHECKING  # noqa: F401,E402
import asyncio  # noqa: F401,E402
import asyncio  # noqa: F401,E402

class TextBatchAggregator:
    """Aggregates rapid-fire text events into single messages.

    Replaces the ``_enqueue_text_event`` / ``_flush_text_batch`` pattern
    previously duplicated in telegram, discord, matrix, wecom, and feishu.

    Usage::

        self._text_batcher = TextBatchAggregator(
            handler=self._message_handler,
            batch_delay=0.6,
            split_threshold=1900,
        )

        # In message dispatch:
        if msg_type == MessageType.TEXT and self._text_batcher.is_enabled():
            self._text_batcher.enqueue(event, session_key)
            return
    """

    def __init__(
        self,
        handler,
        *,
        batch_delay: float = 0.6,
        split_delay: float = 2.0,
        split_threshold: int = 4000,
    ):
        self._handler = handler
        self._batch_delay = batch_delay
        self._split_delay = split_delay
        self._split_threshold = split_threshold
        self._pending: Dict[str, MessageEvent] = {}
        self._pending_tasks: Dict[str, asyncio.Task] = {}

    def is_enabled(self) -> bool:
        """Return True if batching is active (delay > 0)."""
        return self._batch_delay > 0

    def enqueue(self, event: MessageEvent, key: str) -> None:
        """Add *event* to the pending batch for *key*."""
        chunk_len = len(event.text or "")
        existing = self._pending.get(key)
        if not existing:
            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            self._pending[key] = event
        else:
            existing.text = f"{existing.text}\n{event.text}"
            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]

        # Cancel prior flush timer, start a new one
        prior = self._pending_tasks.get(key)
        if prior and not prior.done():
            prior.cancel()
        self._pending_tasks[key] = asyncio.create_task(self._flush(key))

    async def _flush(self, key: str) -> None:
        """Wait then dispatch the batched event for *key*."""
        current_task = self._pending_tasks.get(key)
        pending = self._pending.get(key)
        last_len = getattr(pending, "_last_chunk_len", 0) if pending else 0

        # Use longer delay when the last chunk looks like a split message
        delay = self._split_delay if last_len >= self._split_threshold else self._batch_delay
        await asyncio.sleep(delay)

        event = self._pending.pop(key, None)
        if event:
            try:
                await self._handler(event)
            except Exception:
                logger.exception("[TextBatchAggregator] Error dispatching batched event for %s", key)

        if self._pending_tasks.get(key) is current_task:
            self._pending_tasks.pop(key, None)

    def cancel_all(self) -> None:
        """Cancel all pending flush tasks."""
        for task in self._pending_tasks.values():
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()
        self._pending.clear()
# ---- END PLUGIN-COMPAT ----
