"""Idle tracking for the cloud instance.

Every MCP tool call touches an activity file. The idle-shutdown timer on the
instance (deploy/bin/dm20-idle-check) stops the machine when that file has not
been touched for a while.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger("dm20-protocol")


def touch(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError as exc:  # never fail a tool call because of idle bookkeeping
        logger.warning("Could not update activity file %s: %s", path, exc)


class ActivityMiddleware(Middleware):
    def __init__(self, path: Path) -> None:
        self.path = path

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        touch(self.path)
        return await call_next(context)
