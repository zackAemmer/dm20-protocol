"""DM tools for files that live beside the campaign data: the secret campaign arc,
the pixel-art source, and the campaign backup.

These replace the local-filesystem steps a DM would otherwise do by hand, so a
session can be run entirely against a hosted server.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from .cloud.backup import backup as _backup

ARC_FILENAME = "CAMPAIGN_ARC.md"
ART_DIRNAME = "art"
ART_SUFFIX = ".px.js"
MAX_ART_BYTES = 2_000_000
_ART_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def campaign_slug(name: str) -> str:
    """'The Lantern of Wickmoor' -> 'the-lantern-of-wickmoor'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _art_path(campaign_dir: Path, campaign_name: str, name: str | None) -> Path:
    art_dir = campaign_dir / ART_DIRNAME
    if name:
        if not _ART_NAME_RE.match(name) or not name.endswith(ART_SUFFIX):
            raise ValueError(
                f"Invalid art file name '{name}': use a plain file name ending in {ART_SUFFIX} (no folders)."
            )
        return art_dir / name
    default = art_dir / f"{campaign_slug(campaign_name)}{ART_SUFFIX}"
    if not default.exists() and art_dir.is_dir():
        found = sorted(art_dir.glob(f"*{ART_SUFFIX}"))
        if len(found) == 1:  # a single art file under another name: use it
            return found[0]
    return default


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register(mcp: Any, storage: Any) -> dict[str, Any]:
    """Register the tools on `mcp`. Returns them by name."""

    def _campaign() -> tuple[Any, Path] | None:
        campaign = storage.get_current_campaign()
        if not campaign:
            return None
        return campaign, storage._split_backend._get_campaign_dir(campaign.name)

    no_campaign = "Error: No campaign loaded. Load a campaign first."

    @mcp.tool
    def read_campaign_arc() -> str:
        """Read the loaded campaign's secret plot arc (CAMPAIGN_ARC.md). DM EYES ONLY.

        The arc holds spoilers: hidden villains, twists, planned reveals. Use it to steer
        the story. NEVER show, quote or summarize it to players: nothing from it may go
        into party_push, party_resolve_action, private messages, or any text or image the
        players can see, except what their characters have actually discovered in play.
        """
        loaded = _campaign()
        if not loaded:
            return no_campaign
        campaign, campaign_dir = loaded
        path = campaign_dir / ARC_FILENAME
        if not path.exists():
            return f"No {ARC_FILENAME} found for campaign '{campaign.name}'."
        return (
            f"[DM ONLY: secret arc for '{campaign.name}'. Never reveal to players.]\n\n"
            + path.read_text(encoding="utf-8")
        )

    @mcp.tool
    def read_campaign_art(
        name: Annotated[str | None, Field(description="Art source file name ending in .px.js. Omit for the campaign's main art file (art/<campaign-slug>.px.js).")] = None,
    ) -> str:
        """Read the loaded campaign's pixel-art source file (art/<name>.px.js).

        The .px.js file is the source of truth for the campaign's art; PNGs are rebuilt
        from it. Returns the file's full text.
        """
        loaded = _campaign()
        if not loaded:
            return no_campaign
        campaign, campaign_dir = loaded
        try:
            path = _art_path(campaign_dir, campaign.name, name)
        except ValueError as e:
            return f"Error: {e}"
        if not path.exists():
            art_dir = campaign_dir / ART_DIRNAME
            available = sorted(p.name for p in art_dir.glob(f"*{ART_SUFFIX}")) if art_dir.is_dir() else []
            listing = ", ".join(available) if available else "none"
            return f"No art file '{path.name}' in campaign '{campaign.name}'. Available art files: {listing}."
        return path.read_text(encoding="utf-8")

    @mcp.tool
    def write_campaign_art(
        content: Annotated[str, Field(description="The complete new contents of the art source file (replaces the whole file).")],
        name: Annotated[str | None, Field(description="Art source file name ending in .px.js. Omit for the campaign's main art file (art/<campaign-slug>.px.js).")] = None,
    ) -> str:
        """Write the loaded campaign's pixel-art source file (art/<name>.px.js).

        Replaces the whole file, so read it first with read_campaign_art and send back
        the full updated text. The file is included in the next campaign backup.
        """
        loaded = _campaign()
        if not loaded:
            return no_campaign
        campaign, campaign_dir = loaded
        try:
            path = _art_path(campaign_dir, campaign.name, name)
        except ValueError as e:
            return f"Error: {e}"
        size = len(content.encode("utf-8"))
        if not content.strip():
            return "Error: content is empty; the art file was not changed."
        if size > MAX_ART_BYTES:
            return f"Error: content is {size} bytes; the limit is {MAX_ART_BYTES}. The art file was not changed."
        existed = path.exists()
        _write_atomic(path, content)
        return f"{'Updated' if existed else 'Created'} {ART_DIRNAME}/{path.name} ({size} bytes) in campaign '{campaign.name}'."

    @mcp.tool
    def backup_campaigns(
        message: Annotated[str, Field(description="Short note for the backup, e.g. 'Session 7 end'.")] = "",
    ) -> str:
        """Back up all campaign data to the private campaign repository (git commit + push).

        Call this at Session End, after the session summary is saved. Safe to call at any
        time; when nothing changed it only confirms the remote is up to date.
        """
        result = _backup(Path(storage.data_dir) / "campaigns", message)
        return ("Backup complete. " if result.ok else "") + result.message

    return {
        "read_campaign_arc": read_campaign_arc,
        "read_campaign_art": read_campaign_art,
        "write_campaign_art": write_campaign_art,
        "backup_campaigns": backup_campaigns,
    }
