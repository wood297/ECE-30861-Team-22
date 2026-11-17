"""The connector contract (Phase 5 plan, §11).

A connector fetches one file for one category from one source (GitHub, Confluence, Jenkins...).
It does nothing else. The code around it resolves products and releases, decides statuses,
validates formats, runs Bedrock, caches, and handles AWS, logins and organizations.

A connector gets everything it may use through a `FetchContext`:
- `ctx.http`: a `safe_http.SafeHttpClient` limited to the connection's allowed hosts;
- `ctx.secret()`: the connection's token, as a `Secret`, read only when called;
- `ctx.log`: a logger that masks every secret the connector has seen;
- `ctx.deadline` / `ctx.time_left()`: when the bundle's time runs out.

and answers with a `FetchResult`. `fetch()` never raises for a problem with the source: an
answer from the source, or a `SafeHttpError`, becomes a result. `run_fetch()` is the safety net
the pipeline calls, so a bug in a connector costs one category, not the bundle.

See CONNECTORS.md for how to write, register and test one.
"""
import base64
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from safe_http import SafeHttpClient, SafeHttpError
from secret_store import Secret, SecretNotFound
from tenant_store import Connection

# FetchResult.outcome values. The pipeline maps every outcome except RETRIEVED to INACCESSIBLE.
RETRIEVED = "RETRIEVED"          # data and filename hold the file
NOT_FOUND = "NOT_FOUND"          # the pinned file, build or page version doesn't exist
AUTH_FAILED = "AUTH_FAILED"      # credentials missing, rejected, or lacking permission
UNAVAILABLE = "UNAVAILABLE"      # timeout, rate limit, server error: may work later, never cached
SOURCE_ERROR = "SOURCE_ERROR"    # the source answered, but not in a usable way: ambiguous match,
                                 # malformed listing, unexpected redirect, file too large
OUTCOMES = (RETRIEVED, NOT_FOUND, AUTH_FAILED, UNAVAILABLE, SOURCE_ERROR)

MAX_DETAIL_CHARS = 300
MAX_FILENAME_CHARS = 200
MASK = "****"

# Field kinds
TEXT, SECRET, URL, INTEGER, LIST = "text", "secret", "url", "integer", "list"
_KINDS = (TEXT, SECRET, URL, INTEGER, LIST)
_PLACEHOLDER = re.compile(r"\{([a-z_]*)\}")
PLACEHOLDERS = ("product", "release")


class SettingsError(ValueError):
    """A connection's settings or a release's reference were rejected. Shown next to the field."""

    def __init__(self, field_name: str, message: str):
        super().__init__(f"{field_name}: {message}")
        self.field = field_name
        self.message = message


@dataclass(frozen=True)
class Field:
    """One value an admin enters for a connection, or a release pins.

    kind: "text", "url" (https:// only), "integer", "list" (a list of strings, tried in order;
    a form takes one per line), or "secret". A connection has at most one secret field; its
    value goes to SecretStore and never into the settings, and the connector reads it back
    with ctx.secret().
    """
    name: str
    label: str
    kind: str = TEXT
    required: bool = True
    help: str = ""
    pattern: str | None = None     # a full-match regex for text values, or each list item
    max_length: int = 200
    max_items: int = 5

    def __post_init__(self):
        if self.kind not in _KINDS:
            raise ValueError(f"unknown field kind {self.kind!r}")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", self.name):
            raise ValueError(f"field names are snake_case: {self.name!r}")

    @property
    def secret(self) -> bool:
        return self.kind == SECRET


def check_values(fields: Sequence[Field], values: Mapping[str, Any], *, allow_placeholders: bool = False
                 ) -> dict[str, Any]:
    """Check and normalize values against fields. Secret fields are skipped (they never appear in
    settings). Unknown keys are refused. With allow_placeholders, text and list values may hold
    {product} and {release}, which fill_ref() replaces later."""
    if not isinstance(values, Mapping):
        raise SettingsError("settings", "must be an object")
    known = {f.name: f for f in fields if not f.secret}
    for name in values:
        if name not in known:
            raise SettingsError(str(name)[:40], "is not a field of this connection type")
    clean: dict[str, Any] = {}
    for f in known.values():
        value = values.get(f.name)
        if value is None or value == "" or value == []:
            if f.required:
                raise SettingsError(f.name, "is required")
            continue
        clean[f.name] = _check_value(f, value, allow_placeholders)
    return clean


def _check_value(f: Field, value: Any, allow_placeholders: bool) -> Any:
    if f.kind == INTEGER:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise SettingsError(f.name, "must be a whole number")
        if isinstance(value, str) and not (allow_placeholders and _PLACEHOLDER.search(value)):
            if not re.fullmatch(r"[0-9]{1,12}", value.strip()):
                raise SettingsError(f.name, "must be a whole number")
            return int(value.strip())
        return value
    if f.kind == LIST:
        if isinstance(value, str):
            value = [line.strip() for line in value.splitlines() if line.strip()]
        if not isinstance(value, list) or not value or len(value) > f.max_items:
            raise SettingsError(f.name, f"must be a list of 1 to {f.max_items} values")
        return [_check_text(f, item, allow_placeholders) for item in value]
    text = _check_text(f, value, allow_placeholders)
    if f.kind == URL:
        if not text.startswith("https://"):
            raise SettingsError(f.name, "must start with https://")
        text = text.rstrip("/")
    return text


def _check_text(f: Field, value: Any, allow_placeholders: bool) -> str:
    if not isinstance(value, str):
        raise SettingsError(f.name, "must be text")
    value = value.strip()
    if not value or len(value) > f.max_length or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SettingsError(f.name, f"must be 1 to {f.max_length} printable characters")
    for name in _PLACEHOLDER.findall(value):
        if not allow_placeholders or name not in PLACEHOLDERS:
            raise SettingsError(f.name, "may only use the placeholders {product} and {release}")
    if f.pattern:
        # Placeholders are checked as if they were a typical value
        sample = _PLACEHOLDER.sub("v1", value) if allow_placeholders else value
        if not re.fullmatch(f.pattern, sample):
            raise SettingsError(f.name, "is not in the expected format")
    return value


def fill_ref(ref: Mapping[str, Any], product: str, release: str) -> dict[str, Any]:
    """Replace {product} and {release} in a reference, so a connector only ever gets concrete
    values. Called by the pipeline before fetch()."""
    values = {"product": product, "release": release}

    def fill(item: Any) -> Any:
        if isinstance(item, str):
            return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), item)
        if isinstance(item, list):
            return [fill(i) for i in item]
        return item
    return {key: fill(value) for key, value in ref.items()}


@dataclass(frozen=True)
class FetchResult:
    """What fetch() returns. Use the constructors below rather than building one directly.

    detail is shown to the company as source_failure_detail: one short line, written by the
    connector. Never put a token, a header or an upstream response body in it.
    version is what the source says it returned (a commit, page version, build number), if known.
    """
    outcome: str
    data: bytes | None = field(default=None, repr=False)
    filename: str | None = None
    detail: str | None = None
    version: str | None = None

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {self.outcome!r}")
        if self.outcome == RETRIEVED:
            if not isinstance(self.data, bytes) or not _filename_ok(self.filename):
                raise ValueError("a RETRIEVED result needs bytes and a plain file name")
        elif self.data is not None:
            raise ValueError("only a RETRIEVED result carries data")

    @classmethod
    def retrieved(cls, data: bytes, filename: str, version: str | None = None) -> "FetchResult":
        return cls(RETRIEVED, data=data, filename=filename, version=version)

    @classmethod
    def not_found(cls, detail: str) -> "FetchResult":
        return cls(NOT_FOUND, detail=detail)

    @classmethod
    def auth_failed(cls, detail: str) -> "FetchResult":
        return cls(AUTH_FAILED, detail=detail)

    @classmethod
    def unavailable(cls, detail: str) -> "FetchResult":
        return cls(UNAVAILABLE, detail=detail)

    @classmethod
    def source_error(cls, detail: str) -> "FetchResult":
        return cls(SOURCE_ERROR, detail=detail)


def _filename_ok(name: Any) -> bool:
    return (isinstance(name, str) and 0 < len(name) <= MAX_FILENAME_CHARS and name not in (".", "..")
            and not any(c in name for c in "/\\") and all(32 <= ord(c) != 127 for c in name))


# Helpers that turn the usual HTTP problems into results, so connectors map them the same way

def result_for_http_error(error: SafeHttpError, source: str) -> FetchResult:
    """A SafeHttpError from ctx.http, as a result. source names the system, e.g. "Confluence"."""
    if error.code in ("timeout", "connection", "dns", "tls"):
        return FetchResult.unavailable(f"{source} could not be reached ({error.code}).")
    if error.code == "too_large":
        return FetchResult.source_error(f"The file from {source} is larger than the size limit.")
    # bad_url, host_not_allowed, address_not_allowed: the connection's settings point somewhere
    # we won't go, or the source redirected there
    return FetchResult.source_error(f"{source} refused: {error}")


def result_for_status(status: int, source: str, what: str = "the file") -> FetchResult | None:
    """The result for an unsuccessful HTTP status, or None for 2xx. Connectors check their own
    special cases first (e.g. GitHub's 404 for a private repository the token can't see)."""
    if 200 <= status < 300:
        return None
    if status in (401, 403):
        return FetchResult.auth_failed(f"{source} rejected the connection's credentials ({status}).")
    if status in (404, 410):
        return FetchResult.not_found(f"{source} has no {what} at the pinned version.")
    if status == 429 or status >= 500:
        return FetchResult.unavailable(f"{source} is unavailable or rate limited ({status}).")
    if 300 <= status < 400:
        return FetchResult.source_error(f"{source} answered with an unexpected redirect ({status}).")
    return FetchResult.source_error(f"{source} answered with HTTP {status}.")


class SecretMasker:
    """Remembers secret values and the forms they're sent in, and removes them from text."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def add(self, value: str) -> None:
        """Mask value too. FetchContext adds the token itself; a connector adds anything derived
        from it that it sends, e.g. a Basic header. Base64 and the parts of "user:token" are
        added automatically."""
        if not value:
            return
        self._values.add(value)
        self._values.add(base64.b64encode(value.encode()).decode())

    def mask(self, text: str) -> str:
        # Longest first, so a value containing another is masked whole
        for value in sorted(self._values, key=len, reverse=True):
            text = text.replace(value, MASK)
        return text


class SafeLog:
    """ctx.log: logs like a logging.Logger, but formats the message first and masks secrets."""

    def __init__(self, logger: logging.Logger, masker: SecretMasker, prefix: str = ""):
        self._logger = logger
        self._masker = masker
        self._prefix = prefix

    def _log(self, level: int, msg: str, *args: Any) -> None:
        if not self._logger.isEnabledFor(level):
            return
        try:
            text = str(msg) % args if args else str(msg)
        except (TypeError, ValueError):
            text = f"{msg} {args}"
        self._logger.log(level, "%s", self._masker.mask(self._prefix + text))

    def debug(self, msg: str, *args: Any) -> None:
        self._log(logging.DEBUG, msg, *args)

    def info(self, msg: str, *args: Any) -> None:
        self._log(logging.INFO, msg, *args)

    def warning(self, msg: str, *args: Any) -> None:
        self._log(logging.WARNING, msg, *args)

    def error(self, msg: str, *args: Any) -> None:
        self._log(logging.ERROR, msg, *args)


class FetchContext:
    """Everything a connector may use during fetch(). Built by the pipeline (or by a test)."""

    def __init__(self, http: SafeHttpClient, secret_loader: Callable[[], Secret] | None = None,
                 log: logging.Logger | None = None, deadline: float | None = None,
                 clock: Callable[[], float] = time.monotonic, log_prefix: str = ""):
        self.http = http
        self.deadline = deadline
        self.masker = SecretMasker()
        self.log = SafeLog(log or logging.getLogger("connectors"), self.masker, log_prefix)
        self._secret_loader = secret_loader
        self._secret: Secret | None = None
        self._clock = clock

    def secret(self) -> Secret:
        """The connection's token. Raises MissingSecret if the connection has none; fetch() can
        let that propagate (run_fetch makes it AUTH_FAILED) or handle it."""
        if self._secret is None:
            if self._secret_loader is None:
                raise MissingSecret("this connection has no stored secret")
            try:
                self._secret = self._secret_loader()
            except SecretNotFound:
                raise MissingSecret("this connection's secret is not stored") from None
            value = self._secret.reveal()
            self.masker.add(value)
            for part in value.split(":"):
                if len(part) >= 8:
                    self.masker.add(part)
        return self._secret

    def time_left(self) -> float | None:
        """Seconds until the deadline, or None if there isn't one."""
        return None if self.deadline is None else self.deadline - self._clock()


class MissingSecret(Exception):
    pass


class Connector(Protocol):
    # Plain class attributes in an implementation (read-only here so a tuple satisfies Sequence)
    @property
    def type(self) -> str:
        """"github", "confluence", "jenkins": the registry key."""

    @property
    def display_name(self) -> str:
        """Shown on pages and as external_source, e.g. "Confluence"."""

    @property
    def settings_fields(self) -> Sequence[Field]:
        """What an admin enters once for a connection."""

    @property
    def ref_fields(self) -> Sequence[Field]:
        """What a product or release pins."""

    def validate_settings(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        """Normalize a new connection's settings (without the secret), or raise SettingsError."""

    def validate_ref(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        """Normalize what a product or release pins (placeholders allowed), or raise SettingsError."""

    def allowed_hosts(self, settings: Mapping[str, Any]) -> list[str]:
        """The host names ctx.http may reach for these settings, e.g. ["acme.atlassian.net",
        "api.atlassian.com"]. Include hosts reached after a redirect the connector follows."""

    def fetch(self, conn: Connection, ref: Mapping[str, Any], ctx: FetchContext) -> FetchResult:
        """Fetch one file. ref is concrete (no placeholders). Return a FetchResult; don't raise."""


class BaseConnector:
    """Sensible defaults: validation straight from the field lists. Subclass and set the class
    attributes, implement allowed_hosts() and fetch(), and override the validators to add checks."""
    type: str = ""
    display_name: str = ""
    settings_fields: Sequence[Field] = ()
    ref_fields: Sequence[Field] = ()

    def validate_settings(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        return check_values(self.settings_fields, settings)

    def validate_ref(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        return check_values(self.ref_fields, ref, allow_placeholders=True)

    def allowed_hosts(self, settings: Mapping[str, Any]) -> list[str]:
        raise NotImplementedError

    def fetch(self, conn: Connection, ref: Mapping[str, Any], ctx: FetchContext) -> FetchResult:
        raise NotImplementedError


def clean_detail(detail: str | None, masker: SecretMasker) -> str | None:
    """One line, no control characters, at most MAX_DETAIL_CHARS, secrets masked."""
    if detail is None:
        return None
    text = masker.mask(" ".join(str(detail).split()))
    text = "".join(c for c in text if c.isprintable())
    if len(text) > MAX_DETAIL_CHARS:
        text = text[:MAX_DETAIL_CHARS - 1] + "…"
    return text


def run_fetch(connector: Connector, conn: Connection, ref: Mapping[str, Any], ctx: FetchContext) -> FetchResult:
    """Call connector.fetch() the way the pipeline does: whatever happens, return a FetchResult
    whose detail is safe to show."""
    try:
        result = connector.fetch(conn, ref, ctx)
    except MissingSecret:
        result = FetchResult.auth_failed(f"The {connector.display_name} connection has no stored credentials.")
    except SafeHttpError as error:
        result = result_for_http_error(error, connector.display_name)
    except Exception as error:
        # Only the exception type is logged: its message could hold anything the connector had
        ctx.log.error("connector %s failed with %s", connector.type, type(error).__name__)
        result = FetchResult.source_error(f"The {connector.display_name} connector failed unexpectedly.")
    if not isinstance(result, FetchResult):
        ctx.log.error("connector %s returned %s instead of a FetchResult", connector.type, type(result).__name__)
        result = FetchResult.source_error(f"The {connector.display_name} connector failed unexpectedly.")
    if result.outcome == RETRIEVED:
        return result
    return FetchResult(result.outcome, detail=clean_detail(result.detail, ctx.masker) or "No detail given.",
                       version=result.version)
