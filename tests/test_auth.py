import json
import os
import stat
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from reddit_ads_mcp.auth import (
    TokenProvider,
    build_authorize_url,
    exchange_code,
    extract_code,
    load_refresh_token,
    save_refresh_token,
)
from reddit_ads_mcp.config import Settings
from reddit_ads_mcp.errors import ConfigError, RedditAdsError

from .conftest import FakeReddit, json_response


def test_authorize_url_is_read_only_and_carries_no_secret(settings):
    url = build_authorize_url(settings, "STATE1")
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["adsread"]
    assert query["duration"] == ["permanent"]
    assert query["state"] == ["STATE1"]
    assert "csecret" not in url


def test_authorize_url_needs_a_redirect_uri(settings):
    no_redirect = Settings(client_id="cid", client_secret="x", token_file=settings.token_file)
    with pytest.raises(ConfigError, match="REDIRECT_URI"):
        build_authorize_url(no_redirect, "s")


@pytest.mark.parametrize(
    "text",
    ["abc123", "abc123#_", "https://x.test/cb?state=S&code=abc123#_", "code=abc123&state=S"],
)
def test_extract_code_accepts_code_or_redirect_url(text):
    assert extract_code(text, "S") == "abc123"


def test_extract_code_rejects_state_mismatch_and_empty():
    with pytest.raises(RedditAdsError, match="state"):
        extract_code("https://x.test/cb?state=OTHER&code=abc", "S")
    with pytest.raises(RedditAdsError):
        extract_code("   ", "S")


def test_token_file_roundtrip_with_private_permissions(settings):
    assert load_refresh_token(Settings(token_file=settings.token_file)) is None
    path = save_refresh_token(Settings(token_file=settings.token_file), "RT-file", "adsread")
    assert json.loads(path.read_text())["refresh_token"] == "RT-file"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_refresh_token(Settings(token_file=settings.token_file)) == "RT-file"


def test_environment_refresh_token_wins_over_file(settings):
    save_refresh_token(settings, "RT-file", None)
    assert load_refresh_token(settings) == "RT-secret"


def test_exchange_code_requires_a_refresh_token(settings):
    def handler(_request):
        return json_response({"access_token": "A", "expires_in": 3600})

    with pytest.raises(RedditAdsError, match="no refresh token"):
        exchange_code(settings, "code", httpx.Client(transport=httpx.MockTransport(handler)))


def test_exchange_code_reports_reddit_errors_without_echoing_the_body(settings):
    def handler(_request):
        return json_response({"error": "invalid_grant", "debug": "RT-secret"})

    with pytest.raises(RedditAdsError) as excinfo:
        exchange_code(settings, "code", httpx.Client(transport=httpx.MockTransport(handler)))
    assert "invalid_grant" in str(excinfo.value)
    assert "RT-secret" not in str(excinfo.value)


@pytest.mark.anyio
async def test_refresh_without_any_token_explains_what_to_do(tmp_path):
    bare = Settings(client_id="cid", client_secret="s", token_file=tmp_path / "none.json")
    async with httpx.AsyncClient(transport=httpx.MockTransport(FakeReddit())) as http:
        with pytest.raises(ConfigError, match="reddit-ads-mcp auth"):
            await TokenProvider(bare, http).get()


@pytest.mark.anyio
async def test_rotated_refresh_token_is_persisted_only_when_stored_in_file(tmp_path):
    file_settings = Settings(client_id="cid", client_secret="s", token_file=tmp_path / "t.json")
    save_refresh_token(file_settings, "OLD", None)

    def handler(_request):
        return json_response({"access_token": "A", "expires_in": 3600, "refresh_token": "NEW"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await TokenProvider(file_settings, http).get()
    assert load_refresh_token(file_settings) == "NEW"


def test_wizard_only_asks_for_missing_values(settings):
    from reddit_ads_mcp.cli import DEFAULT_REDIRECT, prompt_missing

    asked: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        return ""

    assert prompt_missing(settings, fake_input, fake_input) is settings
    assert asked == []

    bare = Settings(token_file=settings.token_file)
    answers = iter(["my-id", "my-secret", ""])
    filled = prompt_missing(bare, lambda _p: next(answers), lambda _p: next(answers))
    assert (filled.client_id, filled.client_secret) == ("my-id", "my-secret")
    assert filled.redirect_uri == DEFAULT_REDIRECT
