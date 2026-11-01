# Contributing

This is a small, deliberately narrow project maintained in spare time. It will stay small. Most
proposals will be declined, and a declined proposal is not a judgement of you. Read this whole file
before opening anything. A pull request that ignores it is closed without comment.

## What this project is, and will not become

- A **read-only** MCP server for the Reddit Ads API. It requests the `adsread` scope and nothing else.
- Minimal dependencies: `mcp` and `httpx`. Nothing else.
- Small tool surface, so a language model can pick the right tool.

**Declined on sight:**

- Anything that writes to a Reddit account: creating, editing, pausing, deleting, bidding, budgets,
  audiences, conversion uploads. No write scope, no "opt-in" flag, no exceptions.
- New runtime dependencies, including "just a small helper".
- New tools that duplicate what `get_report` already does with different arguments.
- Telemetry, analytics, update checks, or any network call other than to Reddit's Ads API and
  token endpoint.
- Storing credentials anywhere other than the environment or the `0600` token file.
- Support for other ad platforms. Fork it.
- Cosmetic refactors, renames, reformatting, or style changes with no behavioural reason.
- Drive-by "improvements" to the README or docstrings. Fix a factual error or leave it.

## Before you write any code

1. **Open an issue first** and wait for the maintainer to answer. A pull request without a prior
   accepted issue is closed. There is no response-time guarantee; weeks are normal.
2. **Bugs** need: the exact command or tool call, the full error text with secrets removed, your
   OS, Python version, `mcp` version (`pip show mcp`) and `reddit-ads-mcp --version`, and the output
   of `reddit-ads-mcp doctor`. Reports without these are closed.
3. **Features** need: the concrete question you cannot answer today, why existing tools cannot answer
   it, and evidence from the Reddit Ads API docs that the endpoint exists. "It would be nice" is not
   a reason.
4. **Security issues:** do not open a public issue. Use GitHub's private vulnerability reporting.

## Requirements for a pull request

A pull request is reviewed only when **all** of these hold. Partial compliance is a rejection.

- It links the accepted issue and implements exactly what was agreed, nothing more.
- It is **one logical change**, small enough to review in one sitting. Large diffs are closed, not
  reviewed.
- `ruff check .` and `ruff format --check .` pass with the repository's configuration. Do not edit
  the lint configuration to make your change pass.
- `pytest` passes locally, **and** the CI matrix passes (Python 3.10-3.13, `mcp` 1.x and 2.x, plus
  macOS and Windows). A red CI is not reviewed.
- **Every behaviour change has a test that fails without it.** Bug fixes need a regression test.
  A change that lowers coverage of the code it touches is rejected.
- Tests use mocked HTTP (`httpx.MockTransport`). Tests must not need network access, credentials or
  a Reddit account.
- Type hints on all new code. No `# type: ignore`, `noqa` or `# fmt: skip` added without a comment
  that justifies it.
- Public behaviour you change is reflected in `README.md`, including limits and gotchas.
- **Claims about Reddit's API are verified**, not assumed. State how you verified each one (docs link,
  or a request you ran and its sanitised response). Past mistakes here were found only by running the
  code against a real account: pagination that re-fetched page one, and breakdowns whose rows summed
  to twice the real spend. Unverified assumptions about the API are the most common reason for
  rejection.
- New or changed money, unit or total calculations must be checked against a real account and the
  result described in the pull request.
- Commits are signed off (`git commit -s`), certifying the
  [Developer Certificate of Origin](https://developercertificate.org/). Squash noise before review.
  Use the imperative mood in commit subjects.

## Secrets and data

Never include, in code, tests, fixtures, logs, issue text, screenshots or commit history: client
secrets, tokens, authorization codes, real ad account or business ids, real campaign or ad names,
real URLs from an account, or personal data. Use obviously fake values (`a2_x`, `csecret`). A pull
request that leaks anything is closed, and you should rotate whatever leaked, because the history
may already be public. Do not ask the maintainer to scrub it.

## Style and design rules

- Match the surrounding code: naming, comment density, error handling, structure.
- Expected failures raise `RedditAdsError` with a message safe to show a user. Never put request
  bodies, headers or tokens in an error.
- Do not log. The server owns stdout for the MCP protocol; stray output breaks clients.
- Tool descriptions are part of the interface. Change them only with a reason, and keep them short.
- Do not add configuration options where a sensible default exists.

## Review

- The maintainer decides. Reasons are given briefly or not at all. Do not argue; fork if you disagree.
- Expect several rounds of requested changes. Unanswered requests for more than 14 days close the
  pull request.
- Merging is at the maintainer's pace and may never happen. There is no obligation to merge, to
  release, or to keep any behaviour stable for your use case.
- Contributions are licensed under the project's [MIT license](LICENSE). No contributor agreement
  gives you any say over the project's direction.

## Conduct

Be factual and brief. Pressure, repeated pings, demands for timelines, entitlement, or anything
abusive ends the conversation and may get you blocked. Questions about using the tool belong in
discussions, not in issues.
