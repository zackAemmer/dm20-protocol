"""Per-player language for Party Mode.

Each player picks the language their device shows. The DM (Claude) writes the
narrative once per language in use; every device receives only its own version.
Nothing is machine-translated here: a language without a translation falls back
to the default text.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("dm20-protocol.party.lang")

DEFAULT_LANGUAGE = "en"

# code -> name shown in the language picker
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "zh": "简体中文",
    "zh-Hant": "繁體中文",
}

_ALIASES = {
    "zh-cn": "zh", "zh-hans": "zh", "zh-sg": "zh", "cn": "zh", "chinese": "zh", "中文": "zh",
    "zh-tw": "zh-Hant", "zh-hk": "zh-Hant", "zh-mo": "zh-Hant", "zh-hant": "zh-Hant",
    "en-us": "en", "en-gb": "en", "english": "en",
}


def normalize(code: Any) -> Optional[str]:
    """Map a language tag or name to a supported code, or None."""
    if not isinstance(code, str) or not code.strip():
        return None
    raw = code.strip().replace("_", "-")
    for known in SUPPORTED_LANGUAGES:
        if raw.lower() == known.lower():
            return known
    low = raw.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    for prefix in ("zh-hant", "zh-hans"):
        if low.startswith(prefix):
            return _ALIASES[prefix]
    base = low.split("-")[0]
    if base in SUPPORTED_LANGUAGES:
        return base
    return None


def parse_translations(raw: Any) -> tuple[dict[str, dict[str, str]], Optional[str]]:
    """Parse the DM's `translations` argument.

    Accepts a dict or a JSON object string mapping a language to either the narrative
    text or an object with "narrative" and/or "image_caption".
    Returns ({lang: {"narrative": ..., "image_caption": ...}}, error).
    """
    if raw in (None, "", {}):
        return {}, None
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return {}, 'translations must be a JSON object like {"zh": "..."}'
    if not isinstance(data, dict):
        return {}, 'translations must be a JSON object like {"zh": "..."}'
    out: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        code = normalize(key)
        if code is None:
            return {}, f"unknown language '{key}' in translations (use one of: {', '.join(SUPPORTED_LANGUAGES)})"
        if isinstance(value, str):
            entry = {"narrative": value}
        elif isinstance(value, dict):
            entry = {k: v for k, v in value.items() if k in ("narrative", "image_caption") and isinstance(v, str)}
        else:
            return {}, f"translation for '{key}' must be text or an object"
        if any(v.strip() for v in entry.values()):
            out[code] = entry
    return out, None


def pick(translations: Any, language: Optional[str]) -> Optional[dict[str, str]]:
    """The translation entry for a language: exact match first, then the same base language."""
    if not isinstance(translations, dict) or not language:
        return None
    if language in translations:
        return translations[language]
    base = language.split("-")[0]
    for code, entry in translations.items():
        if code.split("-")[0] == base:
            return entry
    return None


def localize(filtered: dict[str, Any], raw_response: dict[str, Any], language: Optional[str]) -> dict[str, Any]:
    """Swap the public narrative and image caption for the player's language, if provided."""
    entry = pick(raw_response.get("translations"), language)
    if not entry:
        return filtered
    if entry.get("narrative", "").strip():
        filtered["narrative"] = entry["narrative"]
    if entry.get("image_caption", "").strip() and isinstance(filtered.get("image"), dict):
        filtered["image"] = {**filtered["image"], "caption": entry["image_caption"]}
    return filtered


def missing_for(translations: Any, languages: set[str], has_text: bool) -> list[str]:
    """Languages in use that would only see the default-language text."""
    if not has_text:
        return []
    return sorted(code for code in languages if code != DEFAULT_LANGUAGE and not pick(translations, code))


class LanguagePrefs:
    """player_id -> language, kept in <campaign>/party/languages.json."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._prefs: dict[str, str] = {}
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._prefs = {str(k): c for k, v in data.items() if (c := normalize(v))}
            except (OSError, ValueError, AttributeError):
                logger.warning("Could not read %s; starting with no language preferences", path)

    def get(self, player_id: str) -> str:
        with self._lock:
            return self._prefs.get(player_id, DEFAULT_LANGUAGE)

    def set(self, player_id: str, language: Any) -> Optional[str]:
        code = normalize(language)
        if code is None:
            return None
        with self._lock:
            if self._prefs.get(player_id) == code:
                return code
            self._prefs[player_id] = code
            if self._path:
                try:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self._path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(self._prefs, indent=2, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(self._path)
                except OSError as exc:
                    logger.warning("Could not save language preferences: %s", exc)
        return code

    def all(self) -> dict[str, str]:
        with self._lock:
            return dict(self._prefs)
