"""Serve the dm20 MCP server over streamable HTTP behind owner OAuth sign-in.

Used when the server is hosted in the cloud and added to claude.ai as a custom
connector. stdio mode (Claude Desktop) does not touch any of this.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config
from .activity import ActivityMiddleware, touch
from .oauth import OwnerOAuthProvider
from .store import TokenStore

logger = logging.getLogger("dm20-protocol")

MCP_PATH = "/mcp"


def configure(mcp: Any, cfg: config.CloudConfig, store: TokenStore | None = None) -> OwnerOAuthProvider:
    """Attach OAuth, the health route and idle tracking to a FastMCP server."""
    provider = OwnerOAuthProvider(cfg, store or TokenStore(cfg.token_file))
    mcp.auth = provider

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:  # unauthenticated, reveals nothing
        return JSONResponse({"ok": True})

    if cfg.activity_file:
        touch(cfg.activity_file)  # a fresh start counts as activity
        mcp.add_middleware(ActivityMiddleware(cfg.activity_file))
    return provider


def build_app(mcp: Any, cfg: config.CloudConfig, store: TokenStore | None = None) -> Any:
    """Return the ASGI app (used by tests; `run` serves the same app)."""
    configure(mcp, cfg, store)
    return mcp.http_app(path=MCP_PATH, stateless_http=True, json_response=True)


def run(mcp: Any) -> None:
    cfg = config.load()
    configure(mcp, cfg)
    logger.info("Serving MCP over HTTP on %s:%s (public URL %s)", cfg.host, cfg.port, cfg.public_url)
    # Stateless: each request stands alone, so the connector keeps working after the
    # instance is stopped and started again (game state lives in the storage layer).
    mcp.run(
        transport="http",
        host=cfg.host,
        port=cfg.port,
        path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        show_banner=False,
    )
