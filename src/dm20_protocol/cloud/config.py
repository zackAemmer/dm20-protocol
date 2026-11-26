"""Settings for HTTP (cloud) mode, read from environment variables.

Secrets (the owner password) are supplied by the environment only; on the cloud
instance the boot script reads them from AWS SSM Parameter Store into a root-only
env file on tmpfs. They are never written to the repository or to logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# claude.ai's OAuth callback URLs: the only places an authorization code may be sent.
CLAUDE_REDIRECT_URIS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]

MIN_PASSWORD_LENGTH = 16


class ConfigError(RuntimeError):
    """HTTP mode was requested but is not configured correctly."""


@dataclass(frozen=True)
class CloudConfig:
    public_url: str  # e.g. https://dnd-mcp.example.com (no trailing slash)
    owner_password: str = field(repr=False)
    client_id: str = "dm20-claude"
    host: str = "127.0.0.1"
    port: int = 8000
    token_file: Path | None = None  # None -> tokens kept in memory only
    activity_file: Path | None = None  # touched on every tool call (idle shutdown)
    redirect_uris: tuple[str, ...] = tuple(CLAUDE_REDIRECT_URIS)
    access_token_ttl_s: int = 8 * 3600
    refresh_token_ttl_s: int = 90 * 24 * 3600


def http_mode_requested(argv: list[str] | None = None, env: dict[str, str] | None = None) -> bool:
    """True when the server should serve streamable HTTP instead of stdio."""
    env = os.environ if env is None else env
    argv = argv or []
    return "--http" in argv or env.get("DM20_TRANSPORT", "stdio").lower() in ("http", "streamable-http")


def load(env: dict[str, str] | None = None) -> CloudConfig:
    env = dict(os.environ if env is None else env)
    public_url = env.get("DM20_PUBLIC_URL", "").rstrip("/")
    password = env.get("DM20_OWNER_PASSWORD", "")
    if not public_url:
        raise ConfigError("DM20_PUBLIC_URL must be set in HTTP mode (the public https URL of this server)")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ConfigError(
            f"DM20_OWNER_PASSWORD must be set to at least {MIN_PASSWORD_LENGTH} characters in HTTP mode"
        )
    storage_dir = Path(env.get("DM20_STORAGE_DIR", "")).resolve()
    token_file = env.get("DM20_TOKEN_FILE") or str(storage_dir / ".auth" / "tokens.json")
    activity = env.get("DM20_ACTIVITY_FILE")
    return CloudConfig(
        public_url=public_url,
        owner_password=password,
        client_id=env.get("DM20_OAUTH_CLIENT_ID", "dm20-claude"),
        host=env.get("DM20_HTTP_HOST", "127.0.0.1"),
        port=int(env.get("DM20_HTTP_PORT", "8000")),
        token_file=Path(token_file),
        activity_file=Path(activity) if activity else None,
    )
