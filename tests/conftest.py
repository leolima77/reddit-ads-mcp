from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from reddit_ads_mcp.auth import TokenProvider
from reddit_ads_mcp.client import RedditAdsClient
from reddit_ads_mcp.config import Settings

API = "https://ads-api.reddit.com/api/v3"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        client_id="cid",
        client_secret="csecret",
        redirect_uri="https://example.test/callback",
        refresh_token="RT-secret",
        token_file=tmp_path / "token.json",
    )


def json_response(payload, status: int = 200, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode(), headers=headers)


class FakeReddit:
    """Routes requests to handlers keyed by "METHOD path" and records every call."""

    def __init__(self) -> None:
        self.routes: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.calls: list[httpx.Request] = []
        self.token_calls = 0
        self.sleeps: list[float] = []

    def on(self, key: str, handler) -> None:
        self.routes[key] = handler if callable(handler) else (lambda _r, h=handler: h)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "www.reddit.com":
            self.token_calls += 1
            return json_response({"access_token": f"AT{self.token_calls}", "expires_in": 3600})
        key = f"{request.method} {request.url.path.removeprefix('/api/v3')}"
        if key not in self.routes:
            return json_response({"message": f"no route {key}"}, 404)
        result = self.routes[key](request)
        return result if isinstance(result, httpx.Response) else json_response(result)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.fixture
def fake() -> FakeReddit:
    return FakeReddit()


@pytest.fixture
async def client(settings, fake):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        yield RedditAdsClient(settings, TokenProvider(settings, http), http, sleep=fake.sleep)
