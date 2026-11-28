"""How long a moment takes on the DM's side.

A moment starts when the DM collects the players' actions and ends when the narrative
is sent. Each one is appended to ``<campaign>/party/timing.jsonl`` with its duration
and the number of tool calls it took, so slow turns can be measured instead of guessed.

The time before the collect (the DM reading the chat message) and after the send
(writing the chat reply) is not visible to the server and is not counted.
"""

from __future__ import annotations

import json
import logging
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger("dm20-protocol")

_lock = threading.Lock()
_started: Optional[float] = None
_calls = 0


class ToolCallCounter(Middleware):
    """Counts tool calls so a moment's record can say how many it took."""

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        count_call()
        return await call_next(context)


def count_call() -> None:
    global _calls
    with _lock:
        _calls += 1


def start_moment() -> None:
    """Called by the collect tools. The collect call itself is the first call of the moment."""
    global _started, _calls
    with _lock:
        _started = time.monotonic()
        _calls = 1


def finish_moment(campaign_dir: Path, kind: str, narrative_chars: int = 0, has_image: bool = False) -> str:
    """Called when a public narrative goes out. Returns a short note for the tool result."""
    global _started, _calls
    with _lock:
        if _started is None:
            return ""
        seconds = round(time.monotonic() - _started, 1)
        calls = max(_calls, 2)  # at least the collect and this send
        _started = None
        _calls = 0
    record = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds": seconds,
        "tool_calls": calls,
        "kind": kind,
        "narrative_chars": narrative_chars,
        "image": has_image,
    }
    try:
        path = Path(campaign_dir) / "party" / "timing.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:  # timing must never break a send
        logger.warning("Could not write timing log: %s", exc)
    return f"\nMoment: {seconds:g} s from collect to send, {calls} tool calls."


def summary(campaign_dir: Path, last: int = 20) -> str:
    """A readable summary of the most recent moments."""
    path = Path(campaign_dir) / "party" / "timing.jsonl"
    if not path.exists():
        return "No moments timed yet."
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not records:
        return "No moments timed yet."
    recent = records[-last:]
    seconds = [r["seconds"] for r in recent]
    calls = [r["tool_calls"] for r in recent]
    lines = [
        f"Moments timed: {len(records)} in total; the last {len(recent)}:",
        f"- collect to send: median {statistics.median(seconds):g} s, slowest {max(seconds):g} s, fastest {min(seconds):g} s",
        f"- tool calls per moment: median {statistics.median(calls):g}, most {max(calls)}",
        f"- with a picture: {sum(1 for r in recent if r.get('image'))}",
        "Not counted: the time before the collect and the time writing the chat reply.",
        "",
    ]
    lines += [
        f"{r['at']}  {r['seconds']:>6g} s  {r['tool_calls']:>2} calls  {r['narrative_chars']:>5} chars"
        + ("  +picture" if r.get("image") else "")
        for r in recent
    ]
    return "\n".join(lines)
