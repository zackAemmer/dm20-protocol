#!/usr/bin/env python3
"""Smoke test for a dm20 server running in HTTP mode.

Signs in through the OAuth flow (as claude.ai would), then plays a tiny mock session
over MCP: campaign, characters, a labelled roll, a combat action, a character update,
a session note, the arc/art tools, Party Mode with a live WebSocket and a picture,
and a backup.

Run it against a throwaway storage directory, never against real campaign data:

    export DM20_OWNER_PASSWORD=$(openssl rand -hex 16)
    DM20_STORAGE_DIR=$(mktemp -d) DM20_TRANSPORT=http DM20_PUBLIC_URL=http://127.0.0.1:8000 \
        uv run dm20-protocol &
    uv run python deploy/smoke_test.py http://127.0.0.1:8000
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import httpx
import websockets
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
CAMPAIGN = "Smoke Test Campaign"
TINY_PNG = (  # 1x1 transparent PNG
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


async def sign_in(base: str, password: str, client_id: str) -> str:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    async with httpx.AsyncClient(base_url=base) as http:
        r = await http.post(f"{base}/mcp", json={})
        assert r.status_code == 401, f"/mcp without a token should be 401, got {r.status_code}"
        r = await http.get("/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "smoke",
        })
        assert r.status_code == 302, r.text
        req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
        r = await http.post("/login", data={"req": req, "password": password})
        assert r.status_code == 302, f"login failed: {r.status_code}"
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        r = await http.post("/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
            "client_id": client_id, "code_verifier": verifier,
        })
        r.raise_for_status()
        return r.json()["access_token"]


def text(result) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


async def main(base: str) -> None:
    password = os.environ["DM20_OWNER_PASSWORD"]
    token = await sign_in(base, password, os.environ.get("DM20_OAUTH_CLIENT_ID", "dm20-claude"))
    print("ok  OAuth sign-in")

    async with Client(StreamableHttpTransport(f"{base}/mcp", auth=BearerAuth(token))) as mcp:
        async def call(tool: str, /, **args) -> str:
            out = text(await mcp.call_tool(tool, args))
            print(f"ok  {tool}: {out.splitlines()[0][:90] if out else ''}")
            return out

        tools = {t.name for t in await mcp.list_tools()}
        print(f"ok  {len(tools)} tools listed")
        for needed in ("roll_dice", "combat_action", "get_character", "load_campaign", "start_party_mode",
                       "party_push", "read_campaign_arc", "write_campaign_art", "backup_campaigns"):
            assert needed in tools, f"missing tool {needed}"

        await call("create_campaign", name=CAMPAIGN, description="Automated smoke test")
        await call("load_campaign", name=CAMPAIGN)
        await call("load_rulebook", source="srd")
        for name, cls in (("Aldric", "Fighter"), ("Mira", "Wizard")):
            await call("create_character", name=name, character_class=cls, class_level=1,
                       race="Human", player_name=f"{name}Player")

        roll = await call("roll_dice", dice_notation="1d20+3", label="Aldric perception check")
        assert re.search(r"\d", roll)

        await call("start_combat", participants=[{"name": "Aldric", "initiative": 15},
                                                 {"name": "Mira", "initiative": 10}])
        action = await call("combat_action", attacker="Aldric", target="Mira", action_type="attack")
        assert "not found" not in action, action

        await call("update_character", name_or_id="Mira", hit_points_current=3)
        assert "3" in await call("get_character", name_or_id="Mira")

        await call("add_session_note", session_number=1, summary="The party met in the smoke test.",
                   title="Smoke")

        assert "No CAMPAIGN_ARC.md" in await call("read_campaign_arc")
        await call("write_campaign_art", content="// smoke test art\n")
        assert "smoke test art" in await call("read_campaign_art")

        party = await call("start_party_mode", port=8080)
        match = re.search(r"(https?://\S+)/play\?token=(\S+)", party)
        assert match, party
        play_base, player_token = match.group(1), match.group(2)
        local = "http://127.0.0.1:8080"  # test the server directly, whatever URL it advertises
        async with httpx.AsyncClient() as http:
            page = await http.get(f"{local}/play", params={"token": player_token})
            assert page.status_code == 200, page.status_code
        async with websockets.connect(f"ws://127.0.0.1:8080/ws?token={player_token}") as ws:
            await call("party_push", narrative="A cold wind blows through the smoke test.")
            for _ in range(20):
                frame = await asyncio.wait_for(ws.recv(), timeout=10)
                if "cold wind" in frame:
                    break
            else:
                raise AssertionError("pushed narrative never arrived over the WebSocket")
            await call("party_push", narrative="", image_base64=TINY_PNG, image_caption="smoke test picture")
            for _ in range(20):
                frame = await asyncio.wait_for(ws.recv(), timeout=10)
                found = re.search(r'"file":\s*"(img_[A-Za-z0-9._-]+)"', frame)
                if found:
                    name = found.group(1)
                    break
            else:
                raise AssertionError("pushed image never arrived over the WebSocket")
        async with httpx.AsyncClient() as http:
            img = await http.get(f"{local}/img/{name}", params={"token": player_token})
            assert img.status_code == 200 and img.headers["content-type"] == "image/png", img.status_code
            assert (await http.get(f"{local}/img/{name}")).status_code in (401, 403)  # token-gated
        print("ok  Party Mode image push delivered and served (token-gated)")

        # A second device set to Chinese gets the Chinese text, and sees dice as they are rolled.
        async with websockets.connect(f"ws://127.0.0.1:8080/ws?token={player_token}", max_size=None) as ws:
            await ws.send(json.dumps({"type": "set_language", "language": "zh"}))
            await call("party_push", narrative="The bell tolls.", translations=json.dumps({"zh": "钟声响起。"}))
            rid = None
            for _ in range(30):
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if frame.get("type") == "narrative" and frame.get("narrative") == "钟声响起。":
                    rid = frame["id"]
                    break
            assert rid, "the Chinese device never received the Chinese narrative"
            print("ok  Party Mode sent the Chinese text to the device set to Chinese")
            # Dice appear on the device the moment they are rolled.
            await call("roll_dice", dice_notation="1d20+3", label="Smoke test check")
            for _ in range(30):
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if frame.get("type") == "rolls":
                    assert frame["rolls"][0]["label"] == "Smoke test check", frame
                    print("ok  Party Mode showed the roll on the device")
                    break
            else:
                raise AssertionError("the device never received the roll")
            await ws.send(json.dumps({"type": "set_language", "language": "en"}))
        print(f"ok  Party Mode WebSocket delivered the push (player links use {play_base})")
        await call("stop_party_mode")

        await call("backup_campaigns", message="Smoke test")
    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
