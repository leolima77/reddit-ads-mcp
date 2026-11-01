"""Settings, read once from environment variables. Nothing here is ever logged or returned."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from reddit_ads_mcp import __version__
from reddit_ads_mcp.errors import ConfigError

DEFAULT_API_BASE = "https://ads-api.reddit.com/api/v3"
AUTHORIZE_URL = "https://www.reddit.com/api/v1/authorize"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
READ_SCOPE = "adsread"  # this server never asks for write scopes


def default_token_file(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if override := env.get("REDDIT_ADS_TOKEN_FILE"):
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "reddit-ads-mcp" / "token.json"


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = (env.get(name) or "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    redirect_uri: str | None = None
    refresh_token: str | None = field(default=None, repr=False)
    account_id: str | None = None
    business_id: str | None = None
    user_agent: str = f"reddit-ads-mcp/{__version__} (read-only MCP server)"
    api_base: str = DEFAULT_API_BASE
    token_url: str = TOKEN_URL
    authorize_url: str = AUTHORIZE_URL
    token_file: Path = field(default_factory=default_token_file)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        defaults = cls()
        return cls(
            client_id=_get(env, "REDDIT_ADS_CLIENT_ID"),
            client_secret=_get(env, "REDDIT_ADS_CLIENT_SECRET"),
            redirect_uri=_get(env, "REDDIT_ADS_REDIRECT_URI"),
            refresh_token=_get(env, "REDDIT_ADS_REFRESH_TOKEN"),
            account_id=_get(env, "REDDIT_ADS_ACCOUNT_ID"),
            business_id=_get(env, "REDDIT_ADS_BUSINESS_ID"),
            user_agent=_get(env, "REDDIT_ADS_USER_AGENT") or defaults.user_agent,
            api_base=(_get(env, "REDDIT_ADS_API_BASE") or DEFAULT_API_BASE).rstrip("/"),
            token_file=default_token_file(env),
        )

    def require_app(self) -> tuple[str, str]:
        if not self.client_id or not self.client_secret:
            raise ConfigError(
                "Missing REDDIT_ADS_CLIENT_ID / REDDIT_ADS_CLIENT_SECRET. Create a developer "
                "app in Reddit Ads Manager (Business > Developer Portal) and set both variables "
                "in your MCP client configuration."
            )
        return self.client_id, self.client_secret
