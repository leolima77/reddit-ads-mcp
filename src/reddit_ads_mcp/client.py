"""Thin async client for the Reddit Ads API: auth header, retries, rate limits, pagination."""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from reddit_ads_mcp.auth import TokenProvider
from reddit_ads_mcp.config import Settings
from reddit_ads_mcp.errors import RedditAdsError

MAX_ATTEMPTS = 5
MAX_BACKOFF = 30.0
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    """Honour Retry-After, then the RateLimit `t=` reset hint, then exponential backoff."""
    if resp is not None:
        retry_after = resp.headers.get("Retry-After", "")
        if retry_after.replace(".", "", 1).isdigit():
            return min(float(retry_after), MAX_BACKOFF)
        match = re.search(r"\bt=(\d+)", resp.headers.get("RateLimit", ""))
        if match and resp.status_code == 429:
            return min(float(match.group(1)) + 0.5, MAX_BACKOFF)
    return min(2.0**attempt + random.random(), MAX_BACKOFF)


def _error_message(resp: httpx.Response) -> str:
    detail = ""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        detail = str(
            body.get("message")
            or body.get("detail")
            or (error.get("message") if isinstance(error, dict) else error)
            or ""
        )
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        # Reddit's validation errors say exactly what is wrong, e.g. which breakdown is unknown.
        issues = [
            f"{f.get('field')}: {f.get('message')}"
            for f in body["error"].get("fields") or []
            if isinstance(f, dict)
        ]
        detail = "; ".join(filter(None, [detail, *issues]))
    detail = detail or resp.text[:300]
    hint = ""
    if resp.status_code == 403:
        hint = " (the token may lack access to this ad account, or the Ads API is not enabled)"
    elif resp.status_code == 404:
        hint = " (check the id and that it belongs to this ad account)"
    return f"Reddit Ads API returned HTTP {resp.status_code}{hint}: {detail[:700]}"


class RedditAdsClient:
    def __init__(
        self,
        settings: Settings,
        tokens: TokenProvider,
        http: httpx.AsyncClient,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._tokens = tokens
        self._http = http
        self._sleep = sleep
        self.cache: dict[Any, Any] = {}  # per-process memo for account info and name lookups

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http"):
            if urlparse(path_or_url).netloc != urlparse(self.settings.api_base).netloc:
                # never send the bearer token to a host Reddit's pagination did not name
                raise RedditAdsError("Refusing to follow a pagination link to a different host.")
            return path_or_url
        return f"{self.settings.api_base}/{path_or_url.lstrip('/')}"

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        url = self._url(path_or_url)
        refreshed = False
        for attempt in range(MAX_ATTEMPTS):
            headers = {
                "Authorization": f"Bearer {await self._tokens.get()}",
                "User-Agent": self.settings.user_agent,
                "Accept": "application/json",
            }
            try:
                resp = await self._http.request(
                    method, url, params=params, json=json, headers=headers
                )
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise RedditAdsError(
                        f"Network error talking to Reddit: {type(exc).__name__}"
                    ) from exc
                await self._sleep(_retry_delay(None, attempt))
                continue
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                self._tokens.invalidate()
                continue
            if resp.status_code in _RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
                await self._sleep(_retry_delay(resp, attempt))
                continue
            if resp.status_code >= 400:
                raise RedditAdsError(_error_message(resp))
            try:
                return resp.json()
            except ValueError as exc:
                raise RedditAdsError("Reddit returned a non-JSON response.") from exc
        raise RedditAdsError("Reddit Ads API kept failing after several attempts.")

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def paged(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_items: int = 1000,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Collect `data` across pages by following `pagination.next_url`."""
        items: list[dict[str, Any]] = []
        first = {"page.size": page_size, **(params or {})}
        payload = await self.get(path, first)
        while True:
            items.extend(payload.get("data") or [])
            next_url = (payload.get("pagination") or {}).get("next_url")
            if not next_url or len(items) >= max_items:
                return items[:max_items]
            payload = await self.request("GET", next_url)
