"""Party Mode: DM images in the feed."""

from __future__ import annotations

import base64
import json
import struct
import zlib
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

from dm20_protocol import main
from dm20_protocol.party import bridge
from dm20_protocol.party.images import resolve_image, store_image
from dm20_protocol.party.queue import ActionQueue, ResponseQueue


def _png(w: int = 4, h: int = 3) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# ----- image store -----

def test_store_image_from_base64_records_size(tmp_path):
    info = store_image(tmp_path, image_base64=base64.b64encode(_png(192, 108)).decode(), caption=" Dusk ")
    assert info["caption"] == "Dusk"
    assert (info["width"], info["height"]) == (192, 108)
    assert resolve_image(tmp_path, info["file"]) is not None


def test_store_image_from_relative_path(tmp_path):
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "scene.png").write_bytes(_png())
    info = store_image(tmp_path, image_path="art/scene.png")
    assert info["file"].endswith(".png")


def test_store_image_rejects_non_images(tmp_path):
    (tmp_path / "notes.txt").write_text("secret")
    for kwargs in ({"image_path": "notes.txt"}, {"image_base64": "bm90IGFuIGltYWdl"}):
        try:
            store_image(tmp_path, **kwargs)
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_resolve_image_only_serves_generated_names(tmp_path):
    (tmp_path / "party" / "images").mkdir(parents=True)
    (tmp_path / "party" / "images" / "x.png").write_bytes(_png())
    assert resolve_image(tmp_path, "x.png") is None
    assert resolve_image(tmp_path, "../CAMPAIGN_ARC.md") is None


# ----- image through the MCP tools -----

def _server(tmp_path):
    pushed = []
    return SimpleNamespace(
        campaign_dir=tmp_path,
        response_queue=ResponseQueue(tmp_path, on_push=pushed.append),
        action_queue=ActionQueue(tmp_path),
    ), pushed


def test_party_push_image_only(tmp_path):
    server, pushed = _server(tmp_path)
    fn = getattr(main.party_push, "fn", main.party_push)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak"):
        out = fn(image_base64=base64.b64encode(_png()).decode(), image_caption="The square at dusk")
    assert "Pushed" in out
    rec = server.response_queue.get_for_player("p1")[0]
    assert rec["image"]["caption"] == "The square at dusk"
    assert pushed[0]["image"]["file"] == rec["image"]["file"]


def test_party_resolve_bad_image_is_rejected(tmp_path):
    server, _ = _server(tmp_path)
    aid = server.action_queue.push("bardchar", "look")
    fn = getattr(main.party_resolve_action, "fn", main.party_resolve_action)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak"):
        out = fn(action_id=aid, narrative="You see it.", image_path="nope.png")
    assert out.startswith("Not resolved")
    assert server.response_queue.get_for_player("bardchar") == []


def test_bridge_passes_image_to_players(e2e_permission_resolver):
    raw = {"id": "res_1", "narrative": "", "image": {"file": "img_0.png", "caption": "c"}}
    assert bridge.format_response(raw, "thorin", e2e_permission_resolver)["image"]["caption"] == "c"


# ----- HTTP: image route -----

def test_image_route_is_token_gated(e2e_server, e2e_tokens):
    info = store_image(e2e_server.campaign_dir, image_base64=base64.b64encode(_png()).decode())
    client = TestClient(e2e_server.app)
    assert client.get(f"/img/{info['file']}").status_code == 401
    ok = client.get(f"/img/{info['file']}?token={e2e_tokens['thorin']}")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    assert client.get(f"/img/img_000000000000.png?token={e2e_tokens['thorin']}").status_code == 404


def _post(client, token, body):
    return client.post("/action", json=body, headers={"Authorization": f"Bearer {token}"})


def test_actions_are_shown_in_feed_but_whispers_stay_private(e2e_server, e2e_tokens):
    client = TestClient(e2e_server.app)
    with client.websocket_connect(f"/ws?token={e2e_tokens['elara']}") as ws:
        ws.receive_json()  # connected
        _post(client, e2e_tokens["thorin"], {"action": "I light the lantern"})
        _post(client, e2e_tokens["thorin"], {"action": "secret plan", "private": True})
        ws.send_json({"type": "history_request", "since": ""})
        frames = []
        for _ in range(6):
            msg = ws.receive_json()
            if msg["type"] == "player_action":
                frames.append(msg)
            if len(frames) >= 2:
                break
    texts = [f["text"] for f in frames]
    assert "I light the lantern" in texts
    assert "secret plan" not in texts
    assert frames[0]["name"] == "Thorin"
