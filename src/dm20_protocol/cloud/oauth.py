"""Single-owner OAuth 2.1 authorization server for the claude.ai custom connector.

- One pre-registered public client (PKCE, no secret); Dynamic Client Registration is off.
- Authorization codes are only ever sent to Claude's callback URLs.
- /authorize sends the browser to a password page; the owner password is the only way
  to get a code. Repeated wrong passwords lock sign-in for 15 minutes.
- Codes and tokens are random, stored hashed, and expire. Refresh tokens rotate on use.
"""

from __future__ import annotations

import hmac
import html
import secrets
import time

from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .config import CloudConfig
from .store import TokenStore

PENDING_TTL = 600
CODE_TTL = 300
LOCKOUT_WINDOW = 900
MAX_FAILURES = 10
AS_METADATA_PATH = "/.well-known/oauth-authorization-server"


class OwnerOAuthProvider(OAuthProvider):
    def __init__(self, cfg: CloudConfig, store: TokenStore) -> None:
        super().__init__(
            base_url=cfg.public_url,
            client_registration_options=ClientRegistrationOptions(enabled=False),
            revocation_options=RevocationOptions(enabled=True),
        )
        self.cfg = cfg
        self.store = store
        self.client = OAuthClientInformationFull(
            client_id=cfg.client_id,
            client_name="Claude",
            redirect_uris=list(cfg.redirect_uris),
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )

    # --- routes ----------------------------------------------------------------
    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = []
        for route in super().get_routes(mcp_path):
            if isinstance(route, Route) and route.path == AS_METADATA_PATH:
                routes.append(Route(AS_METADATA_PATH, self._metadata, methods=["GET", "OPTIONS"]))
            else:
                routes.append(route)
        routes.append(Route("/login", self.login_page, methods=["GET"]))
        routes.append(Route("/login", self.login_submit, methods=["POST"]))
        return routes

    async def _metadata(self, _: Request) -> Response:
        # The one client is public (PKCE, no secret). The SDK's metadata only advertises
        # secret-based token auth, so serve metadata that also lists "none".
        from mcp.server.auth.routes import build_metadata

        metadata = build_metadata(
            self.base_url, None, self.client_registration_options, self.revocation_options
        ).model_dump(mode="json", exclude_none=True)
        metadata["token_endpoint_auth_methods_supported"] = ["none", "client_secret_post", "client_secret_basic"]
        return JSONResponse(
            metadata,
            headers={"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"},
        )

    # --- clients ---------------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.client if hmac.compare_digest(client_id.encode(), self.cfg.client_id.encode()) else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:  # DCR disabled
        raise RegistrationError(error="invalid_client_metadata", error_description="registration disabled")

    # --- authorize: park the request, send the browser to the login page ---------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        req_id = secrets.token_urlsafe(24)
        self.store.put(
            "pending",
            req_id,
            {
                "client_id": client.client_id,
                "state": params.state,
                "scopes": params.scopes or [],
                "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "resource": params.resource,
            },
            PENDING_TTL,
        )
        return f"{self.cfg.public_url}/login?req={req_id}"

    async def login_page(self, request: Request) -> Response:
        req_id = request.query_params.get("req", "")
        if not self.store.get("pending", req_id):
            return _html(_page("This sign-in link expired. Start again from Claude."), 400)
        return _html(_page(form_req=req_id))

    async def login_submit(self, request: Request) -> Response:
        form = await request.form()
        req_id = str(form.get("req", ""))
        password = str(form.get("password", ""))
        pending = self.store.get("pending", req_id)
        if not pending:
            return _html(_page("This sign-in link expired. Start again from Claude."), 400)

        failures = (self.store.get("lockout", "login") or {}).get("n", 0)
        if failures >= MAX_FAILURES:
            return _html(_page("Too many attempts. Try again in 15 minutes."), 429)

        if not hmac.compare_digest(password.encode(), self.cfg.owner_password.encode()):
            self.store.incr("lockout", "login", LOCKOUT_WINDOW)
            return _html(_page("Wrong password.", form_req=req_id), 401)

        self.store.delete("pending", req_id)
        code = secrets.token_urlsafe(32)
        self.store.put("code", code, {**pending, "expires_at": time.time() + CODE_TTL}, CODE_TTL)
        return RedirectResponse(
            construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"]),
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    # --- codes -----------------------------------------------------------------
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        d = self.store.get("code", authorization_code)
        if not d or d["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=d["scopes"],
            expires_at=d["expires_at"],
            client_id=d["client_id"],
            code_challenge=d["code_challenge"],
            redirect_uri=d["redirect_uri"],
            redirect_uri_provided_explicitly=d["redirect_uri_provided_explicitly"],
            resource=d["resource"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self.store.delete("code", authorization_code.code)  # single use
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    # --- refresh tokens ----------------------------------------------------------
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        d = self.store.get("refresh", refresh_token)
        if not d or d["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, **d)

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        self.store.delete("refresh", refresh_token.token)  # rotate
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    # --- access tokens -----------------------------------------------------------
    async def load_access_token(self, token: str) -> AccessToken | None:
        d = self.store.get("access", token)
        if not d or (d.get("expires_at") and d["expires_at"] < time.time()):
            return None
        return AccessToken(token=token, **d)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.store.delete("access", token.token)
        self.store.delete("refresh", token.token)

    # --- helpers -----------------------------------------------------------------
    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        a_ttl, r_ttl = self.cfg.access_token_ttl_s, self.cfg.refresh_token_ttl_s
        self.store.put(
            "access", access,
            {"client_id": client_id, "scopes": scopes, "expires_at": now + a_ttl, "resource": resource},
            a_ttl,
        )
        self.store.put(
            "refresh", refresh,
            {"client_id": client_id, "scopes": scopes, "expires_at": now + r_ttl, "resource": resource},
            r_ttl,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=a_ttl,
            scope=" ".join(scopes) or None,
            refresh_token=refresh,
        )


def _html(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        body,
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
        },
    )


def _page(message: str = "", form_req: str | None = None) -> str:
    msg = f'<p class="msg">{html.escape(message)}</p>' if message else ""
    form = ""
    if form_req is not None:
        form = f"""
      <form method="post" action="login">
        <input type="hidden" name="req" value="{html.escape(form_req)}">
        <label for="pw">Owner password</label>
        <input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
        <button type="submit">Allow Claude</button>
      </form>"""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>dm20 sign-in</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:360px;margin:15vh auto;padding:0 20px;color:#222}}
 h1{{font-size:20px;font-weight:600}} label{{display:block;margin:16px 0 6px;font-size:14px}}
 input[type=password]{{width:100%;padding:10px;font-size:16px;box-sizing:border-box}}
 button{{margin-top:16px;width:100%;padding:10px;font-size:16px;cursor:pointer}}
 .msg{{color:#a12;font-size:14px}} .sub{{color:#666;font-size:14px}}
</style></head><body>
<h1>dm20</h1><p class="sub">Claude is asking for access to your D&amp;D game server.</p>{msg}{form}
</body></html>"""
