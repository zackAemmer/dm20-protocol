"""Read-aloud for Party Mode: neural speech in each player's language.

Uses Microsoft Edge TTS (the `edge-tts` package): natural voices for English and
Chinese at no cost. Audio is synthesized on the server once per message and
language, cached briefly, and sent to the phones that asked for it as MP3.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections import OrderedDict
from typing import Optional

from .lang import DEFAULT_LANGUAGE, NARRATOR_VOICES

logger = logging.getLogger("dm20-protocol.party.narration")

MAX_CHARS = 3000
CACHE_ITEMS = 24
SYNTH_TIMEOUT_S = 45


def available() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def speakable(text: str) -> str:
    """Strip markdown and table conventions so they are not read out as symbols."""
    t = str(text or "")
    t = re.sub(r"\[([^\[\]]+)\]\([^()\s]+\)", r"\1", t)        # links: [text](url)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)      # headings
    t = re.sub(r"^\s*[-*_]{3,}\s*$", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"[*_`#]+", "", t)                              # emphasis marks
    t = re.sub(r"^\[([^\]]+)\]", r"\1.", t, flags=re.MULTILINE)  # [Round 2 -- Goblin] -> spoken
    t = re.sub(r"\n{2,}", "\n", t).strip()
    if len(t) > MAX_CHARS:
        cut = t[:MAX_CHARS]
        stop = max(cut.rfind("."), cut.rfind("。"), cut.rfind("!"), cut.rfind("?"), cut.rfind("!"), cut.rfind("?"))
        t = cut[: stop + 1] if stop > MAX_CHARS // 2 else cut
    return t


class Narrator:
    """Synthesizes and caches speech. One instance per Party Mode server."""

    def __init__(self) -> None:
        self._cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def _synthesize(self, text: str, language: str) -> bytes:
        import edge_tts

        voice = NARRATOR_VOICES.get(language) or NARRATOR_VOICES[DEFAULT_LANGUAGE]
        audio = bytearray()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    async def audio_for(self, key: str, text: str, language: str) -> Optional[bytes]:
        """MP3 bytes for `text` in `language`, or None when speech is unavailable."""
        clean = speakable(text)
        if not clean or not available():
            return None
        cache_key = (key, language)
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:  # two phones asking for the same message synthesize once
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key]
            try:
                data = await asyncio.wait_for(self._synthesize(clean, language), SYNTH_TIMEOUT_S)
            except Exception as exc:  # network, service or timeout: text still arrives
                logger.warning("Read-aloud failed (%s): %s", language, exc)
                return None
            finally:
                self._locks.pop(cache_key, None)
            if not data:
                return None
            self._cache[cache_key] = data
            while len(self._cache) > CACHE_ITEMS:
                self._cache.popitem(last=False)
            return data


def audio_message(data: bytes, response_id: str | None, language: str) -> dict:
    return {
        "type": "audio",
        "format": "mp3",
        "response_id": response_id,
        "language": language,
        "data": base64.b64encode(data).decode("ascii"),
    }
