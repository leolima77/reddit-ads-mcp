"""Command line: serve (default), auth, doctor."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
from collections.abc import Callable
from dataclasses import replace

import httpx

from reddit_ads_mcp import __version__
from reddit_ads_mcp.auth import (
    NO_TOKEN_MESSAGE,
    TokenProvider,
    load_refresh_token,
    run_interactive_auth,
)
from reddit_ads_mcp.client import RedditAdsClient
from reddit_ads_mcp.config import Settings
from reddit_ads_mcp.errors import RedditAdsError


def _serve() -> int:
    from reddit_ads_mcp.server import create_server

    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines carry request URLs

    create_server().run("stdio")
    return 0


DEFAULT_REDIRECT = "http://localhost:8080/callback"


def prompt_missing(
    settings: Settings,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
) -> Settings:
    """First-run wizard: ask only for what the environment did not provide."""
    changes: dict[str, str] = {}
    if not settings.client_id:
        changes["client_id"] = input_fn("Reddit app ID (client id): ").strip()
    if not settings.client_secret:
        changes["client_secret"] = secret_fn("Reddit app secret (hidden): ").strip()
    if not settings.redirect_uri:
        typed = input_fn(f"Redirect URL registered on the app [{DEFAULT_REDIRECT}]: ").strip()
        changes["redirect_uri"] = typed or DEFAULT_REDIRECT
    return replace(settings, **changes) if changes else settings


def _auth(args: argparse.Namespace) -> int:
    try:
        settings = prompt_missing(Settings.from_env())
        run_interactive_auth(settings, open_browser=not args.no_browser)
    except RedditAdsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\ncancelled", file=sys.stderr)
        return 1
    print(
        "\nNext, add this entry to your MCP client's configuration (the refresh token is already\n"
        "stored, so it is not needed there):\n\n"
        '  "reddit-ads": {\n'
        '    "command": "uvx",\n'
        '    "args": ["reddit-ads-mcp"],\n'
        '    "env": {\n'
        f'      "REDDIT_ADS_CLIENT_ID": "{settings.client_id}",\n'
        '      "REDDIT_ADS_CLIENT_SECRET": "<your app secret>",\n'
        '      "REDDIT_ADS_BUSINESS_ID": "<optional business id>"\n'
        "    }\n"
        "  }\n\n"
        "Then run `reddit-ads-mcp doctor` to verify."
    )
    return 0


async def _doctor() -> int:
    settings = Settings.from_env()
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"[{'ok' if passed else 'FAIL'}] {label}{': ' + detail if detail else ''}")

    check("REDDIT_ADS_CLIENT_ID", bool(settings.client_id))
    check("REDDIT_ADS_CLIENT_SECRET", bool(settings.client_secret))
    has_token = bool(load_refresh_token(settings))
    check("refresh token", has_token, "" if has_token else NO_TOKEN_MESSAGE)
    print(f"[info] REDDIT_ADS_ACCOUNT_ID: {'set' if settings.account_id else 'not set'}")
    print(f"[info] REDDIT_ADS_BUSINESS_ID: {'set' if settings.business_id else 'not set'}")
    if not (settings.client_id and settings.client_secret and has_token):
        return 1
    async with httpx.AsyncClient(timeout=30) as http:
        tokens = TokenProvider(settings, http)
        client = RedditAdsClient(settings, tokens, http)
        try:
            await tokens.get()
            check("token refresh", True)
            if settings.business_id:
                accounts = await client.paged(f"businesses/{settings.business_id}/ad_accounts")
                check("list ad accounts", True, f"{len(accounts)} found")
        except RedditAdsError as exc:
            check("API access", False, str(exc))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reddit-ads-mcp", description="Read-only MCP server for the Reddit Ads API."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Run the MCP server over stdio (default).")
    auth = sub.add_parser("auth", help="Authorize once and store a read-only refresh token.")
    auth.add_argument("--no-browser", action="store_true", help="Print the URL, do not open it.")
    sub.add_parser("doctor", help="Check configuration and API access.")
    args = parser.parse_args(argv)

    if args.command == "auth":
        return _auth(args)
    if args.command == "doctor":
        return asyncio.run(_doctor())
    return _serve()
