class RedditAdsError(Exception):
    """Any failure talking to Reddit or configuring this server. The message is safe to show."""


class ConfigError(RedditAdsError):
    """Missing or invalid local configuration (credentials, ids)."""
