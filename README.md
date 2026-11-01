# reddit-ads-mcp

A **read-only** [MCP](https://modelcontextprotocol.io) server for the [Reddit Ads API](https://ads-api.reddit.com/docs/v3/).
Ask your AI assistant (any MCP client) how your Reddit campaigns are doing, which ads and communities
earn their spend, and whether your UTMs and conversion tracking are sane.

> Unofficial. Not affiliated with or endorsed by Reddit. It only requests the `adsread` OAuth
> scope, so it cannot create, edit, pause or delete anything in your account.

## What you can ask

- "Give me an overview of the last 60 days."
- "Which communities spent the most and what was their CPC?"
- "Compare my carousel ads against the single-image ads."
- "List every ad's destination URL and tell me which ones lack `utm_campaign`."
- "How much did each campaign spend per month, and what did page-visit conversions cost?"

## Quick start

You need Python 3.10+ and [`uv`](https://docs.astral.sh/uv/) (or `pipx`/`pip`, see below).

**1. Create a Reddit developer app.** In Ads Manager go to *Business → Developer Portal → Add app*.
Set the redirect URL to `http://localhost:8080/callback` and copy the **app ID** and **secret**.
(Only business admins can do this. Any redirect URL works; with a non-localhost one the tool asks
you to paste the code instead of capturing it.)

**2. Authorize once** (opens your browser, stores a read-only refresh token with mode `0600`):

```bash
uvx reddit-ads-mcp auth
```

It asks for the app ID, the secret (hidden) and the redirect URL, then prints the entry for step 3.

**3. Add it to your MCP client.** Most clients take the same `mcpServers` entry in their
configuration file (check your client's documentation for where that file lives):

```json
{
  "mcpServers": {
    "reddit-ads": {
      "command": "uvx",
      "args": ["reddit-ads-mcp"],
      "env": {
        "REDDIT_ADS_CLIENT_ID": "<app id>",
        "REDDIT_ADS_CLIENT_SECRET": "<app secret>",
        "REDDIT_ADS_BUSINESS_ID": "<business id>"
      }
    }
  }
}
```

The refresh token stored in step 2 is found automatically, so it does not go in this file.

**4. Check it:** `uvx reddit-ads-mcp doctor` verifies credentials, the token and API access.

`REDDIT_ADS_BUSINESS_ID` is optional but lets the server discover your ad account on its own
(your Reddit business id, a UUID). If your business has several ad accounts,
also set `REDDIT_ADS_ACCOUNT_ID`.

### Other ways to install

```bash
pipx install reddit-ads-mcp          # then use `reddit-ads-mcp` as the command
pip install reddit-ads-mcp
uvx --from git+https://github.com/leolima77/reddit-ads-mcp reddit-ads-mcp   # straight from GitHub
```

## Tools

All tools are annotated read-only.

| Tool | What it does |
| --- | --- |
| `performance_overview` | Totals, monthly trend, campaigns, top ads, placements and top communities in one call. |
| `get_report` | Any report: 1-3 breakdowns (`CAMPAIGN_ID`, `AD_GROUP_ID`, `AD_ID`, `COMMUNITY`, `PLACEMENT`, `COUNTRY`, `INTEREST`, `DATE`, ...), any metrics, a date range, sorting and limits. Adds campaign/ad group/ad names. |
| `list_campaigns`, `list_ad_groups`, `list_ads` | Structure of the account. `list_ads` includes each ad's `click_url`. |
| `list_ad_accounts` | Ad accounts of a business (id, currency, time zone). |
| `list_report_fields` | Common metrics and breakdowns, with a link to Reddit's full reference. |

### Things worth knowing

- **Money is converted for you.** Reddit returns spend, CPC, eCPM and eCPA in micros; this server
  divides by 1,000,000. `ctr` is a ratio (`0.01` = 1%). Currency is the ad account's.
- **`COMMUNITY`, `INTEREST` and `KEYWORD` rows overlap.** A community report summed to roughly twice
  the account's real spend in testing, so those reports return no totals. Compare rows against each
  other, never against the account total.
- **Reddit returns rows in key order, not by spend.** A report with thousands of rows (communities)
  is fetched in full (up to 20,000 rows) and sorted here. If it hits the cap you get a warning,
  because a top-N from a partial fetch can be wrong.
- Dates are inclusive and use the ad account's time zone. Metrics can take up to 6 hours to settle.
- The default metrics are delivery plus Reddit's "key conversion". For sign-ups, leads or purchases
  ask for fields like `CONVERSION_SIGN_UP_CLICKS`; they stay empty unless your pixel/CAPI sends them.

## Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `REDDIT_ADS_CLIENT_ID` | yes | App ID from the developer portal. |
| `REDDIT_ADS_CLIENT_SECRET` | yes | App secret. |
| `REDDIT_ADS_BUSINESS_ID` | no | Lets the server find your ad account and use `list_ad_accounts`. |
| `REDDIT_ADS_ACCOUNT_ID` | no | Default ad account (`a2_...`) so you never have to pass it. |
| `REDDIT_ADS_REFRESH_TOKEN` | no | Use instead of the token file (containers, CI). |
| `REDDIT_ADS_REDIRECT_URI` | for `auth` | Must match the app's redirect URL exactly. |
| `REDDIT_ADS_TOKEN_FILE` | no | Token location. Default `~/.config/reddit-ads-mcp/token.json` (`%APPDATA%` on Windows). |
| `REDDIT_ADS_USER_AGENT` | no | Reddit asks API clients to send a descriptive one. |
| `REDDIT_ADS_API_BASE` | no | Defaults to `https://ads-api.reddit.com/api/v3`. |

## Security

- Read-only: the only scope ever requested is `adsread`.
- Secrets are never logged or returned in tool output, and error messages are built from Reddit's
  status and message only, never from request bodies or headers.
- The refresh token file is created with mode `0600`. Prefer a dedicated Reddit app for this tool.
- The server speaks stdio only; it opens no network port. During `auth` it briefly listens on the
  loopback redirect address you configured.
- Pagination links are followed only on the Ads API host, so the bearer token cannot be sent elsewhere.

## Troubleshooting

Run `reddit-ads-mcp doctor` first.

| Symptom | Fix |
| --- | --- |
| `invalid_grant` | The code was used or expired (10 minutes, single use). Run `auth` again. |
| `redirect_uri` error in the browser | The redirect URL must match the app's setting character for character. |
| HTTP 403 | The authorizing Reddit user lacks access to that ad account, or the app is not allowed to use the Ads API. |
| "No Reddit refresh token found" | Run `auth`, or set `REDDIT_ADS_REFRESH_TOKEN`. |
| Numbers differ from Ads Manager | Check the time zone and dates, and remember recent data settles for up to 6 hours. |

## Development

Contributions are accepted only under the strict rules in [CONTRIBUTING.md](CONTRIBUTING.md); read it before opening an issue or pull request.

```bash
git clone https://github.com/leolima77/reddit-ads-mcp && cd reddit-ads-mcp
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest          # no network, no credentials needed
ruff check . && ruff format --check .
```

Supports Python 3.10+ and the `mcp` SDK 1.22+ or 2.x; CI runs the suite against both SDK majors. The
tests use mocked HTTP and also drive the server end to end over stdio.

## License

[MIT](LICENSE)
