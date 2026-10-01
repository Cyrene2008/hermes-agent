"""Append-only JSONL ledger for native Waifu-Mode deliveries (功能B).

One JSON object per delivered bubble/sticker is appended as the series goes out, so what a paced
turn ACTUALLY sent can be reconstructed later even after the chat scrolls away. The ledger is a
single file capped at :data:`WAIFU_LEDGER_MAX_BYTES`: when the next record would cross the cap the
file rotates to a single ``.1`` backup (best-effort ``os.replace``, falling back to a truncate)
and a fresh ledger starts, so the file never grows without bound and the newest turn always fits.

The writer never raises into the send path: any failure returns ``False`` and is the caller's to
log. Records carry delivery metadata (index, kind, size, success, error) — never raw chat text.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Rotation cap in bytes: 32 MiB minus a 4 KiB headroom (33550336 == 32 * 1024 * 1024 - 4096),
#: so a record that would cross the line rotates the ledger before it ever reaches a round
#: 32 MiB and leaves the 4 KiB cushion for filesystem slack.
WAIFU_LEDGER_MAX_BYTES = 33550336

LEDGER_FILENAME = "waifu_ledger.jsonl"


def default_ledger_path() -> Path:
    """Ledger location: ``HERMES_WAIFU_LEDGER`` if set, else ``$HERMES_HOME``/else ``~/.hermes``."""
    override = os.environ.get("HERMES_WAIFU_LEDGER")
    if override:
        return Path(override)
    base = os.environ.get("HERMES_HOME")
    root = Path(base) if base else Path.home() / ".hermes"
    return root / LEDGER_FILENAME


def _rotate(target: Path) -> None:
    """Move the full ledger aside to a single ``.1`` backup (or drop it if the move fails)."""
    backup = target.with_name(target.name + ".1")
    try:
        os.replace(target, backup)
    except OSError:
        try:
            target.unlink()
        except OSError:
            pass


def append_waifu_ledger(record: Mapping[str, Any], *, path: Optional["str | os.PathLike[str]"] = None) -> bool:
    """Append ``record`` as one JSONL line, rotating first when the write would cross the cap.

    Returns ``True`` on a completed write, ``False`` on any failure (never raises).
    """
    target = Path(path) if path is not None else default_ledger_path()
    try:
        line = (json.dumps(dict(record), ensure_ascii=False, default=str) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        logger.debug("waifu ledger record not serializable: %s", exc)
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size + len(line) > WAIFU_LEDGER_MAX_BYTES:
            _rotate(target)
            logger.info("waifu ledger rotated at %d bytes -> %s.1", WAIFU_LEDGER_MAX_BYTES, target)
        with open(target, "ab") as handle:
            handle.write(line)
        return True
    except OSError as exc:
        logger.debug("waifu ledger append failed for %s: %s", target, exc)
        return False
