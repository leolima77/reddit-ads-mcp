"""OAuth2: refresh-token access for the server, plus the one-time interactive authorization."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import webbrowser
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from reddit_ads_mcp.config import READ_SCOPE, Settings
from reddit_ads_mcp.errors import ConfigError, RedditAdsError

NO_TOKEN_MESSAGE = (
    "No Reddit refresh token found. Run `reddit-ads-mcp auth` once (or set "
    "REDDIT_ADS_REFRESH_TOKEN)."
)
_EXPIRY_MARGIN = 60.0


# --- refresh-token storage ------------------------------------------------------------------


def load_refresh_token(settings: Settings) -> str | None:
    """Environment wins over the token file, so CI and containers need no file."""
    if settings.refresh_token:
        return settings.refresh_token
    try:
        data = json.loads(settings.token_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = data.get("refresh_token") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None


def save_refresh_token(settings: Settings, refresh_token: str, scope: str | None) -> Path:
    path = settings.token_file
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "refresh_token": refresh_token,
        "scope": scope,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    if os.name != "nt":
        path.chmod(0o600)  # O_CREAT mode is ignored when the file already existed
    return path


# --- token endpoint -------------------------------------------------------------------------


def _parse_token_response(resp: httpx.Response) -> dict:
    """Reddit answers 200 with {"error": ...} for some failures; never echo the body."""
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if resp.status_code >= 400 or "error" in data or "access_token" not in data:
        reason = data.get("error") or f"HTTP {resp.status_code}"
        hint = ""
        if reason == "invalid_grant":
            hint = (
                " The code or refresh token expired or was already used; run `reddit-ads-mcp auth`."
            )
        elif resp.status_code == 401:
            hint = " Check REDDIT_ADS_CLIENT_ID and REDDIT_ADS_CLIENT_SECRET."
        raise RedditAdsError(f"Reddit token endpoint refused the request ({reason}).{hint}")
    return data


class TokenProvider:
    """Caches the short-lived access token and refreshes it on demand."""

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            if self._token and self._clock() < self._expires_at - _EXPIRY_MARGIN:
                return self._token
            await self._refresh()
            assert self._token is not None
            return self._token

    def invalidate(self) -> None:
        self._token = None

    async def _refresh(self) -> None:
        settings = self._settings
        refresh_token = load_refresh_token(settings)
        if not refresh_token:
            raise ConfigError(NO_TOKEN_MESSAGE)
        auth = settings.require_app()
        resp = await self._http.post(
            settings.token_url,
            auth=auth,
            headers={"User-Agent": settings.user_agent},
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        data = _parse_token_response(resp)
        self._token = data["access_token"]
        self._expires_at = self._clock() + float(data.get("expires_in") or 3600)
        rotated = data.get("refresh_token")
        if rotated and rotated != refresh_token and not settings.refresh_token:
            save_refresh_token(settings, rotated, data.get("scope"))


# --- one-time interactive authorization -----------------------------------------------------


def build_authorize_url(settings: Settings, state: str) -> str:
    client_id, _ = settings.require_app()
    if not settings.redirect_uri:
        raise ConfigError(
            "Missing REDDIT_ADS_REDIRECT_URI. Use exactly the Redirect URL registered on your "
            "Reddit developer app (http://localhost:8080/callback works well for this tool)."
        )
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "state": state,
            "redirect_uri": settings.redirect_uri,
            "duration": "permanent",
            "scope": READ_SCOPE,
        }
    )
    return f"{settings.authorize_url}?{query}"


def extract_code(user_input: str, expected_state: str) -> str:
    """Accept a bare code or the whole redirected URL; reject a mismatching state."""
    text = user_input.strip()
    if "code=" in text:
        params = parse_qs(urlparse(text).query or text.split("?", 1)[-1])
        state = (params.get("state") or [""])[0]
        if state and not secrets.compare_digest(state, expected_state):
            raise RedditAdsError("The `state` in the redirect does not match; start `auth` again.")
        text = (params.get("code") or [""])[0]
    text = text.removesuffix("#_").strip()
    if not text:
        raise RedditAdsError("No authorization code found in the input.")
    return text


def exchange_code(settings: Settings, code: str, http: httpx.Client | None = None) -> dict:
    client_id, client_secret = settings.require_app()
    owns_client = http is None
    http = http or httpx.Client(timeout=30)
    try:
        resp = http.post(
            settings.token_url,
            auth=(client_id, client_secret),
            headers={"User-Agent": settings.user_agent},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.redirect_uri,
            },
        )
    finally:
        if owns_client:
            http.close()
    data = _parse_token_response(resp)
    if not data.get("refresh_token"):
        raise RedditAdsError(
            "Reddit returned no refresh token; the authorization must be permanent."
        )
    return data


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        params = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if "code" in params or "error" in params:
            self.server.params = params  # type: ignore[attr-defined]
        body = b"<html><body><h3>reddit-ads-mcp</h3>You can close this tab.</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def _is_loopback(redirect_uri: str) -> bool:
    parsed = urlparse(redirect_uri)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and bool(parsed.port)
    )


def _wait_for_callback(redirect_uri: str, timeout: float) -> dict | None:
    parsed = urlparse(redirect_uri)
    server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or 80), _CallbackHandler)
    server.timeout = 1.0
    server.params = None  # type: ignore[attr-defined]
    deadline = time.monotonic() + timeout
    try:
        while server.params is None and time.monotonic() < deadline:  # type: ignore[attr-defined]
            server.handle_request()
        return server.params  # type: ignore[attr-defined]
    finally:
        server.server_close()


def run_interactive_auth(
    settings: Settings,
    *,
    open_browser: bool = True,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    timeout: float = 300.0,
) -> Path:
    """Authorize once with the `adsread` scope and store the refresh token (mode 0600)."""
    state = secrets.token_urlsafe(16)
    url = build_authorize_url(settings, state)
    assert settings.redirect_uri
    out("Open this URL while logged in to the Reddit account that owns the ads account:\n")
    out(url + "\n")
    if _is_loopback(settings.redirect_uri):
        if open_browser:
            webbrowser.open(url)
        out(f"Waiting for the redirect on {settings.redirect_uri} ...")
        params = _wait_for_callback(settings.redirect_uri, timeout)
        if not params:
            raise RedditAdsError("Timed out waiting for the authorization redirect.")
        if "error" in params:
            raise RedditAdsError(f"Reddit denied the authorization ({params['error']}).")
        code = extract_code(urlencode(params), state)
    else:
        out("After clicking Allow, copy the code or the full redirected URL.")
        code = extract_code(input_fn("Code or URL: "), state)
    data = exchange_code(settings, code)
    path = save_refresh_token(settings, data["refresh_token"], data.get("scope"))
    out(f"Saved refresh token to {path} (scope: {data.get('scope')}).")
    return path
