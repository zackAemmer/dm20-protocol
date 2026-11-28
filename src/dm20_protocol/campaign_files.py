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


PICTURE_DIRNAME = "png"
MAX_PICTURE_BYTES = 1_000_000
_PICTURE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def pictures_dir(campaign_dir: Path) -> Path:
    """Rendered pictures kept by name (art/png/<name>.png). Backed up with the campaign."""
    return Path(campaign_dir) / ART_DIRNAME / PICTURE_DIRNAME


def list_pictures(campaign_dir: Path) -> list[str]:
    folder = pictures_dir(campaign_dir)
    return sorted(p.stem for p in folder.glob("*.png")) if folder.is_dir() else []


def _picture_file(campaign_dir: Path, name: str) -> Path:
    if not _PICTURE_NAME_RE.match(name or ""):
        raise ValueError(
            f"invalid picture name '{name}': use letters, digits, '_' or '-' (the scene's name, e.g. portrait_rafe)"
        )
    return pictures_dir(campaign_dir) / f"{name}.png"


def picture_path(campaign_dir: Path, name: str) -> Path:
    """Path of a saved picture. Raises ValueError (listing what exists) if there is none."""
    path = _picture_file(campaign_dir, name)
    if not path.exists():
        available = ", ".join(list_pictures(campaign_dir)) or "none"
        raise ValueError(f"no saved picture called '{name}'. Saved pictures: {available}")
    return path


def save_picture(campaign_dir: Path, name: str, image_base64: str) -> Path:
    """Save a PNG under a name so it can be shown again with image_name. Raises ValueError."""
    import base64
    import binascii

    path = _picture_file(campaign_dir, name)
    payload = image_base64.strip()
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("image_base64 is not valid base64")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("a saved picture must be a PNG")
    if len(data) > MAX_PICTURE_BYTES:
        raise ValueError(f"picture is {len(data)} bytes; the limit is {MAX_PICTURE_BYTES}. Send pixel art at 1x")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


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
    def write_campaign_arc(
        content: Annotated[str, Field(description="The complete new text of the arc (replaces the whole file).")],
    ) -> str:
        """Write the loaded campaign's secret plot arc (CAMPAIGN_ARC.md). DM EYES ONLY.

        Use it to write the arc before session 1 and to keep it current afterwards (the
        clock advancing, a beat resolved, a new secret). It replaces the whole file, so
        read it first with read_campaign_arc and send back the full updated text. The
        same secrecy applies: never show any of it to players.
        """
        loaded = _campaign()
        if not loaded:
            return no_campaign
        campaign, campaign_dir = loaded
        size = len(content.encode("utf-8"))
        if not content.strip():
            return "Error: content is empty; the arc was not changed."
        if size > MAX_ART_BYTES:
            return f"Error: content is {size} bytes; the limit is {MAX_ART_BYTES}. The arc was not changed."
        path = campaign_dir / ARC_FILENAME
        existed = path.exists()
        _write_atomic(path, content)
        return f"{'Updated' if existed else 'Created'} {ARC_FILENAME} ({size} bytes) for campaign '{campaign.name}'. DM only."

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
        content: Annotated[str, Field(description="With append=false: the complete new contents of the art source file (replaces the whole file). With append=true: only the new text (new parts, sprites and scenes) to add at the end of the file.")],
        name: Annotated[str | None, Field(description="Art source file name ending in .px.js. Omit for the campaign's main art file (art/<campaign-slug>.px.js).")] = None,
        append: Annotated[bool, Field(description="True adds `content` to the end of the existing file instead of replacing it. Use this to save a new scene: send only the new code, not the whole file. Use false only to change or remove something already in the file.")] = False,
    ) -> str:
        """Write the loaded campaign's pixel-art source file (art/<name>.px.js).

        To save a new scene, pass append=true with only the new code. Without append it
        replaces the whole file, so read it first with read_campaign_art and send back the
        full updated text. The file is included in the next campaign backup.
        """
        loaded = _campaign()
        if not loaded:
            return no_campaign
        campaign, campaign_dir = loaded
        try:
            path = _art_path(campaign_dir, campaign.name, name)
        except ValueError as e:
            return f"Error: {e}"
        if not content.strip():
            return "Error: content is empty; the art file was not changed."
        existed = path.exists()
        if append:
            if not existed:
                return f"Error: there is no {ART_DIRNAME}/{path.name} to append to. Write the whole file first (append=false)."
            current = path.read_text(encoding="utf-8")
            if content.strip() in current:
                return f"Not changed: {ART_DIRNAME}/{path.name} already contains this text."
            content = current.rstrip("\n") + "\n\n" + content.strip("\n") + "\n"
        size = len(content.encode("utf-8"))
        if size > MAX_ART_BYTES:
            return f"Error: content is {size} bytes; the limit is {MAX_ART_BYTES}. The art file was not changed."
        _write_atomic(path, content)
        verb = "Appended to" if append else ("Updated" if existed else "Created")
        return f"{verb} {ART_DIRNAME}/{path.name} ({size} bytes) in campaign '{campaign.name}'."

    @mcp.tool
    def save_campaign_picture(
        name: Annotated[str, Field(description="Name to keep the picture under: the scene's name in the art source, e.g. 'portrait_rafe' (letters, digits, '_' or '-').")],
        image_base64: Annotated[str, Field(description="The rendered PNG at real size (1x), as base64 or a data: URL.")],
    ) -> str:
        """Keep a rendered picture in the campaign's picture library (art/png/<name>.png).

        Do this in prep for every picture likely to be needed (portraits, places, maps).
        In play, `party_push` / `party_resolve_action` then show it with
        `image_name="<name>"` and no image data, so a prepared picture costs nothing to
        send. Saving under an existing name replaces that picture. Backed up with the campaign.
        """
        loaded = _campaign()
        if not loaded:
            return no_campaign
        campaign, campaign_dir = loaded
        try:
            path = save_picture(campaign_dir, name, image_base64)
        except ValueError as e:
            return f"Error: {e}."
        saved = ", ".join(list_pictures(campaign_dir))
        return f"Saved picture '{name}' ({path.stat().st_size} bytes) in campaign '{campaign.name}'. Saved pictures: {saved}."

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
        "write_campaign_arc": write_campaign_arc,
        "read_campaign_art": read_campaign_art,
        "write_campaign_art": write_campaign_art,
        "save_campaign_picture": save_campaign_picture,
        "backup_campaigns": backup_campaigns,
    }
