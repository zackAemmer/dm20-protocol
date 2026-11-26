"""Small key-value store for OAuth state (pending logins, codes, tokens).

Keys are SHA-256 hashed before storage, so the token file never contains a usable
token. Access and refresh tokens are persisted to a JSON file (mode 0600) so that
the claude.ai connector stays signed in across server restarts: the cloud instance
is stopped between sessions. Short-lived state stays in memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

PERSISTED_KINDS = ("access", "refresh")


def _pk(kind: str, key: str) -> str:
    return f"{kind}#{hashlib.sha256(key.encode()).hexdigest()}"


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._d: dict[str, tuple[float, dict[str, Any]]] = {}
        if path and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                now = time.time()
                self._d = {k: (float(v[0]), v[1]) for k, v in raw.items() if float(v[0]) > now}
            except (OSError, ValueError, TypeError, IndexError, KeyError):
                self._d = {}  # unreadable file: start empty (owner signs in again)

    def put(self, kind: str, key: str, value: dict[str, Any], ttl_s: int) -> None:
        with self._lock:
            self._d[_pk(kind, key)] = (time.time() + ttl_s, value)
            self._save(kind)

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._d.get(_pk(kind, key))
            if not item or item[0] < time.time():
                return None
            return item[1]

    def delete(self, kind: str, key: str) -> None:
        with self._lock:
            if self._d.pop(_pk(kind, key), None) is not None:
                self._save(kind)

    def incr(self, kind: str, key: str, ttl_s: int) -> int:
        """Increment a counter that expires ttl_s after its first increment."""
        with self._lock:
            pk = _pk(kind, key)
            item = self._d.get(pk)
            if not item or item[0] < time.time():
                item = (time.time() + ttl_s, {"n": 0})
            n = item[1]["n"] + 1
            self._d[pk] = (item[0], {"n": n})
            return n

    def _save(self, kind: str) -> None:
        if not self._path or kind not in PERSISTED_KINDS:
            return
        now = time.time()
        data = {
            k: [exp, v]
            for k, (exp, v) in self._d.items()
            if exp > now and k.split("#", 1)[0] in PERSISTED_KINDS
        }
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".tokens-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
