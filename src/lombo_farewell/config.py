"""Settings, read once from environment variables (see .env.example).

Every setting has a LOMBO_FAREWELL_ prefix so it never collides with another container's variables.
A bad value stops the app at start-up with a message that names the variable.
"""

import ipaddress
import shlex
from dataclasses import dataclass
from urllib.parse import urlparse

PREFIX = "LOMBO_FAREWELL_"

# Every setting, so a test can keep .env.example in sync.
SETTINGS = (
    "JELLYFIN_URL",
    "JELLYFIN_API_KEY",
    "WEBHOOK_URL",
    "DRY_RUN",
    "INTERVAL_SECONDS",
    "SOURCE",
    "MAINTAINERR_URL",
    "SOURCE_FILE",
    "COLLECTION_NAME",
    "KEEP_DAYS",
    "MAX_REMOVALS_PER_ROUND",
    "STATE_FILE",
    "ALLOW_SHRINK",
    "ADOPT",
    "API_TOKEN",
    "API_BIND",
    "POST_KEEP_COMMAND",
    "MAX_KEEPS_PER_PERSON",
    "MAX_KEEPS_PER_TITLE",
    "ALERT_ON_KEEP",
    "ALERT_DELETING_SOON",
)

TOKEN_MIN = 24
DEFAULT_BIND = "127.0.0.1:8098"

SOURCES = ("maintainerr", "file")


class ConfigError(Exception):
    """A setting is missing or invalid. The message says which one and what to do."""


@dataclass(frozen=True)
class Config:
    jellyfin_url: str
    jellyfin_api_key: str
    webhook_url: str | None
    dry_run: bool
    interval_seconds: int
    source: str
    maintainerr_url: str
    source_file: str
    collection_name: str
    keep_days: int
    max_removals_per_round: int
    state_file: str
    allow_shrink: bool = False  # let a round remove titles even when the list shrank to under half
    adopt: bool = False  # let this tool manage a collection it did not make
    api_token: str = ""  # `serve` needs it (see token_problem); empty means the API is off
    api_bind: str = DEFAULT_BIND  # host:port `serve` listens on
    post_keep_command: tuple[str, ...] = ()  # run once after a keep that postponed a title; empty = off
    max_keeps_per_person: int = 0  # keeps one person may make per KEEP_DAYS days (needs `by`); 0 = no limit
    max_keeps_per_title: int = 0  # times one title may be kept while it stays on the list; 0 = no limit
    alert_on_keep: bool = False  # webhook message after a keep that postponed a title
    alert_deleting_soon: bool = False  # webhook message once when a title is within a day of deletion


def _truthy(value: str, name: str) -> bool:
    """Strict on/off: a typo such as DRY_RUN=treu is an error, never a silent 'off'."""
    word = value.strip().lower()
    if word in {"1", "true", "yes", "on"}:
        return True
    if word in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{PREFIX}{name} must be true or false (got {value!r})")


def _http_url(value: str, name: str, example: str) -> str:
    parts = urlparse(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConfigError(f"{PREFIX}{name} must be a full URL such as {example} (got {value!r})")
    return value.rstrip("/")


def _whole(value: str, name: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{PREFIX}{name} must be a whole number") from None
    if not low <= number <= high:
        raise ConfigError(f"{PREFIX}{name} must be between {low} and {high}")
    return number


def parse_bind(value: str) -> tuple[str, int]:
    """'host:port' (or '[::1]:port') as (host, port). Raises ConfigError naming API_BIND."""
    host, sep, port_text = value.rpartition(":")
    host = host.strip("[]")
    if not sep or not host or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ConfigError(f"{PREFIX}API_BIND must look like 127.0.0.1:8098 (host:port, port 1 to 65535)")
    return host, int(port_text)


def is_loopback(host: str) -> bool:
    """True for localhost and loopback addresses. A host name other than localhost counts as not loopback."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def token_problem(token: str) -> str | None:
    """Why `serve` will not start with this token, or None when it is fine."""
    if not token:
        return f"{PREFIX}API_TOKEN is not set. Make a long random one (for example `openssl rand -hex 32`)."
    if len(token) < TOKEN_MIN:
        return f"{PREFIX}API_TOKEN is too short: use at least {TOKEN_MIN} characters (`openssl rand -hex 32`)."
    if not all(33 <= ord(c) <= 126 for c in token):
        return f"{PREFIX}API_TOKEN must be printable ASCII with no spaces."
    return None


def from_env(env: dict[str, str]) -> Config:
    def get(name: str, default: str = "") -> str:
        return (env.get(PREFIX + name) or default).strip()

    url = _http_url(get("JELLYFIN_URL"), "JELLYFIN_URL", "http://jellyfin:8096")

    key = get("JELLYFIN_API_KEY")
    if not key or key == "changeme":
        raise ConfigError(f"{PREFIX}JELLYFIN_API_KEY is not set. Create one in Jellyfin: Dashboard > API Keys.")

    if any(not c.isprintable() for c in key):
        raise ConfigError(f"{PREFIX}JELLYFIN_API_KEY must not contain control characters (a stray newline?)")

    webhook = get("WEBHOOK_URL") or None
    if webhook and urlparse(webhook).scheme not in {"http", "https"}:
        raise ConfigError(f"{PREFIX}WEBHOOK_URL must start with http:// or https:// (or be empty)")

    interval = _whole(get("INTERVAL_SECONDS", "3600"), "INTERVAL_SECONDS", 60, 7 * 86400)

    source = get("SOURCE", "maintainerr").lower()
    if source not in SOURCES:
        raise ConfigError(f"{PREFIX}SOURCE must be one of {', '.join(SOURCES)} (got {source!r})")
    maintainerr = get("MAINTAINERR_URL")
    source_file = get("SOURCE_FILE")
    if source == "maintainerr":
        maintainerr = _http_url(maintainerr, "MAINTAINERR_URL", "http://maintainerr:6246")
    elif not source_file:
        raise ConfigError(f"{PREFIX}SOURCE_FILE must name the list of titles when SOURCE=file")

    name = get("COLLECTION_NAME", "Leaving soon")
    if len(name) > 100:
        raise ConfigError(f"{PREFIX}COLLECTION_NAME must be at most 100 characters")

    keep_days = _whole(get("KEEP_DAYS", "30"), "KEEP_DAYS", 1, 365)
    max_removals = _whole(get("MAX_REMOVALS_PER_ROUND", "25"), "MAX_REMOVALS_PER_ROUND", 1, 10000)

    bind = get("API_BIND", DEFAULT_BIND)
    parse_bind(bind)  # a bad address stops the app at start-up like any other setting

    try:
        post_keep = tuple(shlex.split(get("POST_KEEP_COMMAND")))
    except ValueError as e:
        raise ConfigError(f"{PREFIX}POST_KEEP_COMMAND is not a valid command: {e}") from None

    max_per_person = _whole(get("MAX_KEEPS_PER_PERSON", "0"), "MAX_KEEPS_PER_PERSON", 0, 1000)
    max_per_title = _whole(get("MAX_KEEPS_PER_TITLE", "0"), "MAX_KEEPS_PER_TITLE", 0, 1000)

    # Safe by default: nothing changes anything until the person sets DRY_RUN=false.
    return Config(
        url,
        key,
        webhook,
        _truthy(get("DRY_RUN", "true"), "DRY_RUN"),
        interval,
        source,
        maintainerr,
        source_file,
        name,
        keep_days,
        max_removals,
        get("STATE_FILE", "/data/state.json"),
        _truthy(get("ALLOW_SHRINK", "false"), "ALLOW_SHRINK"),
        _truthy(get("ADOPT", "false"), "ADOPT"),
        get("API_TOKEN"),
        bind,
        post_keep,
        max_per_person,
        max_per_title,
        _truthy(get("ALERT_ON_KEEP", "false"), "ALERT_ON_KEEP"),
        _truthy(get("ALERT_DELETING_SOON", "false"), "ALERT_DELETING_SOON"),
    )
