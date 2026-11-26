"""Tests for cloud hosting: HTTP mode config, owner OAuth sign-in, token store,
idle tracking, campaign backup, and the arc/art tools."""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastmcp import FastMCP

from dm20_protocol import campaign_files
from dm20_protocol.cloud import config as cloud_config
from dm20_protocol.cloud import oauth as cloud_oauth
from dm20_protocol.cloud.backup import backup
from dm20_protocol.cloud.http import build_app
from dm20_protocol.cloud.store import TokenStore

PUBLIC_URL = "https://dnd-mcp.example.com"
PASSWORD = "correct horse battery staple"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def make_cfg(tmp_path: Path, **over) -> cloud_config.CloudConfig:
    return cloud_config.CloudConfig(
        public_url=PUBLIC_URL,
        owner_password=PASSWORD,
        token_file=tmp_path / "auth" / "tokens.json",
        activity_file=tmp_path / "run" / "last-activity",
        **over,
    )


def make_server() -> FastMCP:
    mcp = FastMCP(name="test")

    @mcp.tool
    def ping() -> str:
        """Ping."""
        return "pong"

    return mcp


def run_with_app(cfg, coro_fn):
    """Run `coro_fn(client)` against the ASGI app with its lifespan active."""

    async def runner():
        app = build_app(make_server(), cfg)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=PUBLIC_URL) as client:
                return await coro_fn(client)

    return asyncio.run(runner())


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def sign_in(client, password=PASSWORD, redirect=REDIRECT, client_id="dm20-claude"):
    """Drive authorize -> login -> token. Returns (token_response_json, verifier)."""
    verifier, challenge = pkce()
    r = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
    )
    if r.status_code != 302 or "/login" not in r.headers.get("location", ""):
        return r, verifier
    req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
    page = await client.get("/login", params={"req": req})
    assert page.status_code == 200 and "Owner password" in page.text
    r = await client.post("/login", data={"req": req, "password": password})
    if r.status_code != 302:
        return r, verifier
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == redirect
    q = parse_qs(loc.query)
    assert q["state"] == ["xyz"]
    r = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "redirect_uri": redirect,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    return r, verifier


def call_tool_body(name="ping"):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}


# --- config -------------------------------------------------------------------


def test_http_mode_is_opt_in():
    assert not cloud_config.http_mode_requested([], {})
    assert not cloud_config.http_mode_requested([], {"DM20_TRANSPORT": "stdio"})
    assert cloud_config.http_mode_requested(["--http"], {})
    assert cloud_config.http_mode_requested([], {"DM20_TRANSPORT": "http"})


def test_config_requires_url_and_long_password(tmp_path):
    with pytest.raises(cloud_config.ConfigError):
        cloud_config.load({"DM20_OWNER_PASSWORD": PASSWORD})
    with pytest.raises(cloud_config.ConfigError):
        cloud_config.load({"DM20_PUBLIC_URL": PUBLIC_URL, "DM20_OWNER_PASSWORD": "short"})
    cfg = cloud_config.load(
        {"DM20_PUBLIC_URL": PUBLIC_URL + "/", "DM20_OWNER_PASSWORD": PASSWORD, "DM20_STORAGE_DIR": str(tmp_path)}
    )
    assert cfg.public_url == PUBLIC_URL
    assert cfg.host == "127.0.0.1"
    assert cfg.token_file == tmp_path.resolve() / ".auth" / "tokens.json"
    assert PASSWORD not in repr(cfg)


def test_main_defaults_to_stdio(monkeypatch):
    import dm20_protocol.main as m

    calls = []
    monkeypatch.setattr(m.mcp, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr("sys.argv", ["dm20-protocol"])
    monkeypatch.delenv("DM20_TRANSPORT", raising=False)
    m.main()
    assert calls == [((), {})]


# --- token store ----------------------------------------------------------------


def test_store_persists_tokens_hashed(tmp_path):
    path = tmp_path / "a" / "tokens.json"
    store = TokenStore(path)
    store.put("access", "secret-token-value", {"client_id": "c"}, 60)
    store.put("pending", "req", {"x": 1}, 60)
    raw = path.read_text()
    assert "secret-token-value" not in raw and "pending" not in raw
    assert (path.stat().st_mode & 0o777) == 0o600

    again = TokenStore(path)
    assert again.get("access", "secret-token-value") == {"client_id": "c"}
    assert again.get("pending", "req") is None  # short-lived state is memory only
    again.delete("access", "secret-token-value")
    assert TokenStore(path).get("access", "secret-token-value") is None


def test_store_expiry_and_counter(tmp_path):
    store = TokenStore(None)
    store.put("code", "k", {"v": 1}, -1)
    assert store.get("code", "k") is None
    assert [store.incr("lockout", "login", 60) for _ in range(3)] == [1, 2, 3]
    TokenStore(tmp_path / "broken.json")  # missing file is fine
    (tmp_path / "broken.json").write_text("{not json")
    assert TokenStore(tmp_path / "broken.json").get("access", "x") is None


# --- OAuth + MCP over HTTP ---------------------------------------------------------


def test_mcp_requires_token_and_advertises_metadata(tmp_path):
    async def go(client):
        r = await client.post("/mcp", json=call_tool_body(), headers=HEADERS)
        assert r.status_code == 401
        assert "resource_metadata" in r.headers["www-authenticate"]

        meta = (await client.get("/.well-known/oauth-authorization-server")).json()
        assert meta["issuer"].rstrip("/") == PUBLIC_URL
        assert meta["authorization_endpoint"] == f"{PUBLIC_URL}/authorize"
        assert meta["token_endpoint"] == f"{PUBLIC_URL}/token"
        assert "none" in meta["token_endpoint_auth_methods_supported"]
        assert "S256" in meta["code_challenge_methods_supported"]
        assert "registration_endpoint" not in meta

        pr = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert pr["resource"] == f"{PUBLIC_URL}/mcp"
        assert [a.rstrip("/") for a in pr["authorization_servers"]] == [PUBLIC_URL]

        assert (await client.get("/health")).json() == {"ok": True}
        assert (await client.post("/register", json={"redirect_uris": [REDIRECT]})).status_code in (404, 405)

    run_with_app(make_cfg(tmp_path), go)


def test_full_sign_in_then_tool_call_and_refresh(tmp_path):
    cfg = make_cfg(tmp_path)

    async def go(client):
        r, _ = await sign_in(client)
        assert r.status_code == 200, r.text
        tok = r.json()
        assert tok["token_type"].lower() == "bearer" and tok["refresh_token"]

        before = cfg.activity_file.stat().st_mtime
        os.utime(cfg.activity_file, (before - 500, before - 500))
        auth = {**HEADERS, "Authorization": f"Bearer {tok['access_token']}"}
        r = await client.post("/mcp", json=call_tool_body(), headers=auth)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["content"][0]["text"] == "pong"
        assert cfg.activity_file.stat().st_mtime > before - 100  # tool call counted as activity

        r = await client.post("/mcp", json=call_tool_body(), headers={**HEADERS, "Authorization": "Bearer nope"})
        assert r.status_code == 401

        # refresh rotates: the old refresh token stops working
        data = {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": "dm20-claude"}
        r2 = await client.post("/token", data=data)
        assert r2.status_code == 200, r2.text
        assert r2.json()["access_token"] != tok["access_token"]
        assert (await client.post("/token", data=data)).status_code == 400
        return r2.json()["access_token"]

    access = run_with_app(cfg, go)

    # A restarted server (new process, same token file) still accepts the token.
    async def after_restart(client):
        auth = {**HEADERS, "Authorization": f"Bearer {access}"}
        r = await client.post("/mcp", json=call_tool_body(), headers=auth)
        assert r.status_code == 200

    run_with_app(cfg, after_restart)
    assert access not in cfg.token_file.read_text()


def test_wrong_password_and_lockout(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_oauth, "MAX_FAILURES", 3)

    async def go(client):
        for _ in range(3):
            r, _ = await sign_in(client, password="wrong password guess")
            assert r.status_code == 401 and "Wrong password" in r.text
        r, _ = await sign_in(client)  # correct password, but locked out
        assert r.status_code == 429

    run_with_app(make_cfg(tmp_path), go)


def test_rejects_unknown_client_and_redirect(tmp_path):
    async def go(client):
        r, _ = await sign_in(client, client_id="someone-else")
        assert r.status_code == 400
        r, _ = await sign_in(client, redirect="https://evil.example.com/cb")
        assert r.status_code == 400
        assert "evil.example.com" not in r.headers.get("location", "")
        r = await client.post("/login", data={"req": "made-up", "password": PASSWORD})
        assert r.status_code == 400

    run_with_app(make_cfg(tmp_path), go)


def test_code_is_single_use_and_needs_verifier(tmp_path):
    async def go(client):
        verifier, challenge = pkce()
        r = await client.get(
            "/authorize",
            params={
                "response_type": "code", "client_id": "dm20-claude", "redirect_uri": REDIRECT,
                "code_challenge": challenge, "code_challenge_method": "S256", "state": "s",
            },
        )
        req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
        r = await client.post("/login", data={"req": req, "password": PASSWORD})
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": "dm20-claude"}
        assert (await client.post("/token", data={**data, "code_verifier": "wrong" * 12})).status_code == 400
        assert (await client.post("/token", data={**data, "code_verifier": verifier})).status_code == 200
        assert (await client.post("/token", data={**data, "code_verifier": verifier})).status_code == 400

    run_with_app(make_cfg(tmp_path), go)


# --- backup -------------------------------------------------------------------------


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def campaign_repo(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "campaigns"
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True, capture_output=True)
    git(repo, "checkout", "-q", "-b", "main")
    return repo, remote


def test_backup_commits_and_pushes(campaign_repo):
    repo, remote = campaign_repo
    camp = repo / "The Lantern of Wickmoor"
    (camp / "party" / "images").mkdir(parents=True)
    (camp / "art").mkdir()
    (camp / "campaign.json").write_text("{}")
    (camp / "CAMPAIGN_ARC.md").write_text("secret")
    (camp / "art" / "the-lantern-of-wickmoor.px.js").write_text("// art")
    (camp / "party" / "images" / "scene.png").write_bytes(b"png")
    (camp / "party" / "QR Zack-Hero.png").write_bytes(b"png")

    result = backup(repo, "Session 7 end")
    assert result.ok and result.committed and result.pushed, result.message
    files = git(remote, "ls-tree", "-r", "--name-only", "main")
    assert "The Lantern of Wickmoor/CAMPAIGN_ARC.md" in files
    assert "the-lantern-of-wickmoor.px.js" in files
    assert "scene.png" not in files and "QR Zack" not in files
    assert "Session 7 end" in git(remote, "log", "-1", "--format=%s", "main")

    again = backup(repo)
    assert again.ok and not again.committed and "No changes" in again.message

    (camp / "campaign.json").write_text('{"v": 2}')
    third = backup(repo)
    assert third.ok and third.committed
    assert git(remote, "rev-list", "--count", "main").strip() == "2"


def test_backup_stops_tracking_party_secrets(campaign_repo):
    """Secrets committed before they were ignored leave the repo but stay on disk."""
    repo, remote = campaign_repo
    party = repo / "Wickmoor" / "party"
    party.mkdir(parents=True)
    (repo / "Wickmoor" / "campaign.json").write_text("{}")
    (party / ".token_secret.json").write_text('{"secret": "abc"}')
    (party / ".vapid_private.pem").write_text("KEY")
    (party / "languages.json").write_text('{"mei": "zh"}')
    git(repo, "add", "-A", "-f")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "import with secrets")
    git(repo, "push", "-q", "origin", "HEAD")
    assert ".token_secret.json" in git(remote, "ls-tree", "-r", "--name-only", "main")

    result = backup(repo, "tidy")
    assert result.ok and result.committed, result.message
    files = git(remote, "ls-tree", "-r", "--name-only", "main")
    assert ".token_secret.json" not in files and ".vapid_private.pem" not in files
    assert "Wickmoor/party/languages.json" in files and "Wickmoor/campaign.json" in files
    assert (party / ".token_secret.json").exists() and (party / ".vapid_private.pem").exists()
    assert not backup(repo).committed  # and they are not picked up again


def test_backup_of_empty_clone_commits_nothing(campaign_repo):
    """An empty campaign repo must stay empty, so the first real import can be pulled."""
    repo, remote = campaign_repo
    result = backup(repo)
    assert result.ok and not result.committed and "Nothing to back up" in result.message
    assert not (repo / ".gitignore").exists()
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True).returncode != 0


def test_backup_reports_failures(tmp_path, campaign_repo):
    assert not backup(tmp_path / "nowhere").ok
    repo, remote = campaign_repo
    (repo / "x.json").write_text("{}")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    result = backup(repo)
    assert not result.ok and result.committed and "push failed" in result.message


# --- arc / art / backup tools -----------------------------------------------------------


@pytest.fixture
def tools(tmp_path):
    name = "The Lantern of Wickmoor"
    campaign_dir = tmp_path / "campaigns" / name
    campaign_dir.mkdir(parents=True)
    state = {"campaign": SimpleNamespace(name=name)}
    storage = SimpleNamespace(
        data_dir=tmp_path,
        get_current_campaign=lambda: state["campaign"],
        _split_backend=SimpleNamespace(_get_campaign_dir=lambda n: tmp_path / "campaigns" / n),
    )
    registered = campaign_files.register(FastMCP(name="t"), storage)
    return SimpleNamespace(dir=campaign_dir, state=state, **{k: v.fn for k, v in registered.items()})


def test_campaign_slug():
    assert campaign_files.campaign_slug("The Lantern of Wickmoor") == "the-lantern-of-wickmoor"
    assert campaign_files.campaign_slug("  Curse of Strahd!  ") == "curse-of-strahd"


def test_read_arc(tools):
    assert "No CAMPAIGN_ARC.md" in tools.read_campaign_arc()
    (tools.dir / "CAMPAIGN_ARC.md").write_text("The mayor is the lich.", encoding="utf-8")
    out = tools.read_campaign_arc()
    assert "The mayor is the lich." in out and "DM ONLY" in out
    tools.state["campaign"] = None
    assert tools.read_campaign_arc().startswith("Error: No campaign loaded")


def test_arc_tool_warns_never_to_push_to_players():
    registered = campaign_files.register(FastMCP(name="t"), SimpleNamespace())
    doc = registered["read_campaign_arc"].description
    assert "NEVER" in doc and "party_push" in doc


def test_art_round_trip(tools):
    assert "Available art files: none" in tools.read_campaign_art()
    out = tools.write_campaign_art(content="scene('dock', () => {})\n")
    assert out.startswith("Created art/the-lantern-of-wickmoor.px.js")
    assert tools.read_campaign_art() == "scene('dock', () => {})\n"
    assert tools.write_campaign_art(content="v2").startswith("Updated")
    assert tools.read_campaign_art(name="the-lantern-of-wickmoor.px.js") == "v2"
    assert not list((tools.dir / "art").glob(".*"))  # no temp files left behind


def test_art_uses_single_existing_file(tools):
    (tools.dir / "art").mkdir()
    (tools.dir / "art" / "wickmoor.px.js").write_text("legacy name")
    assert tools.read_campaign_art() == "legacy name"
    tools.write_campaign_art(content="new")
    assert (tools.dir / "art" / "wickmoor.px.js").read_text() == "new"


@pytest.mark.parametrize("bad", ["../campaign.json", "a/b.px.js", "/etc/passwd", "notes.md", ".hidden.px.js", "x\\y.px.js"])
def test_art_rejects_unsafe_names(tools, bad):
    assert tools.read_campaign_art(name=bad).startswith("Error: Invalid art file name")
    assert tools.write_campaign_art(content="x", name=bad).startswith("Error: Invalid art file name")
    assert not (tools.dir / "art").exists()


def test_art_rejects_empty_and_huge(tools):
    assert "empty" in tools.write_campaign_art(content="  ")
    assert "limit" in tools.write_campaign_art(content="x" * (campaign_files.MAX_ART_BYTES + 1))


def test_backup_tool_without_repo(tools):
    assert "not a git repository" in tools.backup_campaigns()


def test_tools_registered_on_real_server():
    import dm20_protocol.main as m

    names = set(asyncio.run(m.mcp.get_tools()))
    assert {"read_campaign_arc", "read_campaign_art", "write_campaign_art", "backup_campaigns"} <= names


# --- Party Mode public URL ---------------------------------------------------------------


def test_qr_uses_public_base_url(tmp_path, monkeypatch):
    from dm20_protocol.party.auth import QRCodeGenerator

    seen = []
    monkeypatch.setattr(QRCodeGenerator, "generate_qr_code", staticmethod(lambda url, path: seen.append(url)))
    QRCodeGenerator.generate_player_qr("p1", "tok", "10.0.0.5", 8080, tmp_path)
    QRCodeGenerator.generate_player_qr("p1", "tok", "10.0.0.5", 8080, tmp_path, base_url="https://dnd-play.example.com/")
    assert seen == ["http://10.0.0.5:8080/play?token=tok", "https://dnd-play.example.com/play?token=tok"]


def test_party_base_url(monkeypatch):
    import dm20_protocol.main as m

    server = SimpleNamespace(host_ip="10.0.0.5", port=8080)
    monkeypatch.delenv("DM20_PARTY_PUBLIC_URL", raising=False)
    assert m._party_base_url(server) == "http://10.0.0.5:8080"
    monkeypatch.setenv("DM20_PARTY_PUBLIC_URL", "https://dnd-play.example.com/")
    assert m._party_base_url(server) == "https://dnd-play.example.com"


def test_public_party_pages_hide_player_ids(monkeypatch):
    """With a public URL, the token-free pages must not name the connected players."""
    from starlette.testclient import TestClient

    from dm20_protocol.party.server import PartyServer

    server = SimpleNamespace(
        connection_manager=SimpleNamespace(get_connected_players=lambda: ["pc_secret_id"], connection_count=lambda: 1),
        start_time=__import__("datetime").datetime.now(),
        pc_registry=SimpleNamespace(get_all_active=lambda: []),
    )
    from starlette.applications import Starlette
    from starlette.routing import Route

    async def root(request):
        return await PartyServer.get_root(server, request)

    async def status(request):
        return await PartyServer.get_status(server, request)

    app = Starlette(routes=[Route("/", root), Route("/status", status)])
    client = TestClient(app)

    monkeypatch.delenv("DM20_PARTY_PUBLIC_URL", raising=False)
    assert "pc_secret_id" in client.get("/").text
    assert client.get("/status").json()["connected_players"] == ["pc_secret_id"]

    monkeypatch.setenv("DM20_PARTY_PUBLIC_URL", "https://quest.example.com")
    assert "pc_secret_id" not in client.get("/").text and "1</strong> player" in client.get("/").text
    assert client.get("/status").json() == {"status": "running", "connected_players": 1}
