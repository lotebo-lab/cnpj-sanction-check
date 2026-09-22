"""HTTP client for four Brazilian federal sanction registries.

All four routes belong to the Portal da Transparencia open data API, published
by the Controladoria-Geral da Uniao (CGU):

    https://api.portaldatransparencia.gov.br/api-de-dados/ceis
    https://api.portaldatransparencia.gov.br/api-de-dados/cnep
    https://api.portaldatransparencia.gov.br/api-de-dados/cepim
    https://api.portaldatransparencia.gov.br/api-de-dados/acordos-leniencia

THE CNPJ PARAMETER IS NOT THE SAME ON ALL FOUR, and this is the kind of
difference that breaks a client in silence: a wrong parameter name is simply
ignored by the API, which then answers with the unfiltered first page of the
whole registry. Read from the official OpenAPI document (saved at
`docs/portal-openapi-2026-09-20.json`, downloaded from
https://api.portaldatransparencia.gov.br/v3/api-docs):

    /api-de-dados/ceis               -> codigoSancionado
    /api-de-dados/cnep               -> codigoSancionado
    /api-de-dados/cepim              -> cnpjSancionado
    /api-de-dados/acordos-leniencia  -> cnpjSancionado

`tests/test_schema_contract.py` asserts each of the four names against that
document, so a rename upstream fails a test instead of returning junk.

Two things about credentials, and they are not negotiable:

1. The API token belongs to the **user of the Actor**. It arrives in the Actor
   input field `portalToken` and is passed to `PortalClient` by the caller.
   This module never reads an environment variable, never reads a file and
   never carries a default token. There is no lab credential here.
2. The token is only ever written to the `chave-api-dados` request header. It
   is never logged, never pushed to the dataset and never echoed in an error
   message. Two guards enforce that second sentence, and they are there because
   the library breaks it on its own: `requests.utils.check_header_validity`
   raises `InvalidHeader: ... in header value: '<the value>'`, so a token with a
   line break inside it (an e-mail client wrapping the line is enough) used to
   travel from the transport exception into the error row of the dataset. The
   guards are `_sanitize_token`, which refuses such a token before any call and
   without repeating it, and `_redact`, which blanks the token out of every
   message this module produces, whoever wrote it.

The token is free: anyone registers an e-mail at
https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email and receives
one. Access rules read on 2026-09-20 from
https://portaldatransparencia.gov.br/api-de-dados : "No periodo de 6:00 as
23:59, o Portal aceita 400 requisicoes por minuto." and "Ja no periodo das
00:00 as 5:59, sao aceitas 700 requisicoes por minuto." The pacing below
follows both windows, on Brasilia time, and errs on the slow side.

The HTTP transport is injectable so the whole client can be tested offline:
pass any object with a `get(url, params=..., headers=..., timeout=...)` method,
or a plain callable with the same signature. The default is `requests`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence

API_BASE = "https://api.portaldatransparencia.gov.br"
CEIS_PATH = "/api-de-dados/ceis"
CNEP_PATH = "/api-de-dados/cnep"
CEPIM_PATH = "/api-de-dados/cepim"
LENIENCIA_PATH = "/api-de-dados/acordos-leniencia"

CEIS_URL = API_BASE + CEIS_PATH
CNEP_URL = API_BASE + CNEP_PATH
CEPIM_URL = API_BASE + CEPIM_PATH
LENIENCIA_URL = API_BASE + LENIENCIA_PATH

# Name of the CNPJ query parameter per route. Not a detail: CEIS and CNEP call
# it `codigoSancionado` (it also accepts a CPF), while CEPIM and
# acordos-leniencia call it `cnpjSancionado`. Sending the wrong one returns the
# unfiltered registry, which would read as "this company is sanctioned".
CNPJ_PARAM_BY_PATH = {
    CEIS_PATH: "codigoSancionado",
    CNEP_PATH: "codigoSancionado",
    CEPIM_PATH: "cnpjSancionado",
    LENIENCIA_PATH: "cnpjSancionado",
}

# Header name documented by the Portal for the free token.
TOKEN_HEADER = "chave-api-dados"

# Published ceilings (https://portaldatransparencia.gov.br/api-de-dados):
#   06:00 - 23:59 -> 400 requests per minute -> 0.15 s between two calls
#   00:00 - 05:59 -> 700 requests per minute -> ~0.0857 s between two calls
# The windows are Brazilian federal government hours, so they are read on
# Brasilia time. Brazil has had no daylight saving time since Decreto
# 9.772/2019, so the offset is a constant UTC-3 and no tzdata file is needed
# inside the Actor image.
DAY_MIN_INTERVAL_SECONDS = 60.0 / 400.0
NIGHT_MIN_INTERVAL_SECONDS = 60.0 / 700.0
NIGHT_WINDOW_HOURS = frozenset({0, 1, 2, 3, 4, 5})
BRASILIA_OFFSET = timezone(timedelta(hours=-3))

# Kept for callers that want one fixed, always-safe gap.
DEFAULT_MIN_INTERVAL_SECONDS = DAY_MIN_INTERVAL_SECONDS


def brasilia_hour(now: datetime | None = None) -> int:
    """Hour of the day in Brasilia (UTC-3) for an aware or naive UTC instant."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(BRASILIA_OFFSET).hour


def min_interval_for(now: datetime | None = None) -> float:
    """Seconds to wait between two calls, for the window we are in right now."""
    if brasilia_hour(now) in NIGHT_WINDOW_HOURS:
        return NIGHT_MIN_INTERVAL_SECONDS
    return DAY_MIN_INTERVAL_SECONDS

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3  # 1 first try + 3 retries = at most 4 calls
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 30.0

# Safety net for the page loop: the registries return a handful of pages per
# company at most, so a query that keeps producing pages means something is
# wrong with the request, not that the company has thousands of sanctions.
DEFAULT_MAX_PAGES_PER_QUERY = 40

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

USER_AGENT = "cnpj-sanction-check (Apify Actor; contact via Apify Store page)"

# What replaces the token in any text that leaves this module.
TOKEN_PLACEHOLDER = "[portalToken redacted]"

# Characters an HTTP header value may not carry. `requests` refuses them, and
# the refusal quotes the value, so we refuse first and quote nothing.
def _bad_header_chars(value: str) -> list[str]:
    """Names of the characters in `value` that cannot go in a header value."""
    bad: list[str] = []
    for char in value:
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            bad.append({0x09: "tab", 0x0A: "line break", 0x0D: "carriage return"}.get(
                code, f"control character U+{code:04X}"
            ))
        elif code > 0x7E:
            bad.append(f"non-ASCII character U+{code:04X}")
    # Stable, deduplicated, and never the character itself when it is printable
    # enough to be part of a token.
    seen: list[str] = []
    for name in bad:
        if name not in seen:
            seen.append(name)
    return seen


class PortalError(Exception):
    """Any failure talking to the Portal da Transparencia API."""


class PortalAuthError(PortalError):
    """The token was rejected (HTTP 401/403). The user has to fix it.

    `status_code` carries the HTTP status the Portal answered, so the run
    summary can state it instead of hiding it inside a sentence. It is None
    when no call was made at all (an empty `portalToken` input).
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PortalRequestError(PortalError):
    """The request failed and retrying did not help."""


@dataclass
class FetchStats:
    """Counters for the run summary. No personal data, no token."""

    requests_made: int = 0
    retries: int = 0
    pages_read: int = 0
    seconds_slept: float = 0.0


@dataclass
class _Response:
    status_code: int
    payload: Any


class RequestsTransport:
    """Default transport: one pooled `requests` session."""

    def __init__(self) -> None:
        import requests  # imported lazily so tests never need the library

        self._session = requests.Session()

    def get(self, url: str, params: dict, headers: dict, timeout: float):
        return self._session.get(url, params=params, headers=headers, timeout=timeout)


def _call_transport(transport: Any, url: str, params: dict, headers: dict, timeout: float):
    """Accept both an object with `.get(...)` and a bare callable."""
    getter = getattr(transport, "get", None)
    if getter is None:
        if not callable(transport):
            raise TypeError("transport must have a get() method or be callable")
        getter = transport
    return getter(url, params=params, headers=headers, timeout=timeout)


def only_digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def format_cnpj(cnpj: str) -> str:
    """12345678000199 -> 12.345.678/0001-99. Anything else is returned as is."""
    digits = only_digits(cnpj)
    if len(digits) != 14:
        return str(cnpj)
    return f"{digits[0:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:14]}"


def _extract_records(payload: Any) -> list[dict]:
    """Return the list of records in one API page.

    The documented shape is a JSON array. The dictionary shapes below are
    defensive: if the API ever wraps the array, the client keeps working
    instead of silently reporting zero sanctions, which would be the dangerous
    failure for a compliance check.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("content", "items", "registros", "data", "results"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [item for item in inner if isinstance(item, dict)]
        return [payload]
    return []


@dataclass
class PortalClient:
    """Reads CEIS and CNEP for one CNPJ at a time.

    Every knob that touches the clock is injectable, so the tests run instantly
    and offline.
    """

    # repr=False: a dataclass repr would otherwise print the buyer's token in
    # any log line or traceback that shows the client.
    token: str = field(repr=False)
    transport: Any = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS
    # None means "follow the published window": 400/min by day, 700/min from
    # midnight to 05:59 Brasilia time. A number pins one fixed gap instead.
    min_interval_seconds: float | None = None
    # Wall clock used only to decide which window we are in. Injectable so the
    # tests can stand at any hour without waiting for it.
    wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    max_pages_per_query: int = DEFAULT_MAX_PAGES_PER_QUERY
    log: Callable[[str], None] = lambda message: None
    stats: FetchStats = field(default_factory=FetchStats)
    _last_call_at: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        token = (self.token or "").strip()
        if not token:
            raise PortalAuthError(
                "No API token given. Fill the 'portalToken' input field with your "
                "own free Portal da Transparencia token. Request one at "
                "https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email "
                "and it arrives by e-mail."
            )
        bad = _bad_header_chars(token)
        if bad:
            # The value is NOT repeated here, and that is the whole point: the
            # library's own message for this case quotes the header value, and
            # that message ends up in the log and in the error row.
            raise PortalAuthError(
                "The token in the 'portalToken' input field carries "
                f"{len(bad)} kind(s) of character that cannot be sent in an HTTP "
                f"header ({', '.join(bad)}). This usually means the token was "
                "copied with a line break or a stray space inside it. Copy it "
                "again as a single line, with nothing before or after it."
            )
        self.token = token
        if self.transport is None:
            self.transport = RequestsTransport()

    # -- credential hygiene ----------------------------------------------

    def _redact(self, text: Any) -> str:
        """Blank the token out of any message before it can be logged or pushed.

        Last line of defence. `_sanitize_token` already refuses the token shapes
        that make `requests` quote the value, but this module must not depend on
        knowing every message every transport can produce: whatever the text
        says, it leaves here without the credential in it.
        """
        message = str(text)
        token = getattr(self, "token", "") or ""
        if not token:
            return message
        message = message.replace(token, TOKEN_PLACEHOLDER)
        # `repr()` of a string with escapes (what InvalidHeader prints) does not
        # contain the raw value, so the escaped form is replaced too.
        escaped = repr(token)[1:-1]
        if escaped and escaped != token:
            message = message.replace(escaped, TOKEN_PLACEHOLDER)
        return message

    # -- pacing ----------------------------------------------------------

    def current_min_interval(self) -> float:
        """The gap this client must keep right now, in seconds."""
        if self.min_interval_seconds is not None:
            return self.min_interval_seconds
        return min_interval_for(self.wall_clock())

    def _wait_turn(self) -> None:
        """Keep the published minimum gap between two API calls."""
        interval = self.current_min_interval()
        if interval <= 0:
            return
        now = self.clock()
        if self._last_call_at is not None:
            elapsed = now - self._last_call_at
            remaining = interval - elapsed
            if remaining > 0:
                self.sleep(remaining)
                self.stats.seconds_slept += remaining
                now = self.clock()
        self._last_call_at = now

    # -- one HTTP call ---------------------------------------------------

    def _get(self, url: str, params: dict) -> list[dict]:
        headers = {
            TOKEN_HEADER: self.token,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        attempt = 0
        last_problem = "unknown error"
        while True:
            self._wait_turn()
            self.stats.requests_made += 1
            try:
                response = _call_transport(
                    self.transport, url, params, headers, self.timeout_seconds
                )
            except Exception as exc:  # noqa: BLE001 - transport level failure
                # Redacted at the source: this string goes to the log, to the
                # 'error' field of the dataset row and to the justification
                # sentence, and some transport exceptions quote the request
                # headers.
                last_problem = self._redact(f"{exc.__class__.__name__}: {exc}")
                response = None
            else:
                status = int(getattr(response, "status_code", 0) or 0)
                if status in (401, 403):
                    # No retry: a rejected token is not a transient problem.
                    raise PortalAuthError(
                        f"The Portal da Transparencia rejected the token (HTTP {status}). "
                        "Check the 'portalToken' input field: the token must be the one "
                        "the Portal e-mailed to you, copied without extra spaces. "
                        "Get or re-issue a free token at "
                        "https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email",
                        status_code=status,
                    )
                if status == 404:
                    # The Portal answers 404 for "nothing found" on some routes.
                    return []
                if status in RETRYABLE_STATUS:
                    last_problem = f"HTTP {status}"
                elif 200 <= status < 300:
                    try:
                        payload = _json_of(response)
                    except Exception as exc:  # noqa: BLE001 - bad body
                        last_problem = self._redact(f"invalid JSON body: {exc}")
                    else:
                        return _extract_records(payload)
                else:
                    raise PortalRequestError(
                        self._redact(
                            "Unexpected answer from the Portal da Transparencia: "
                            f"HTTP {status} for {url}."
                        )
                    )

            if attempt >= self.max_retries:
                raise PortalRequestError(
                    self._redact(
                        f"The Portal da Transparencia did not answer {url} after "
                        f"{attempt + 1} attempts ({last_problem}). Try again later; "
                        "the Portal is sometimes unavailable at night for "
                        "maintenance."
                    )
                )
            delay = min(
                self.backoff_seconds * (2**attempt), self.max_backoff_seconds
            )
            self.log(self._redact(f"Retrying {url} in {delay:.1f}s after {last_problem}"))
            self.sleep(delay)
            self.stats.seconds_slept += delay
            self.stats.retries += 1
            attempt += 1

    # -- pagination ------------------------------------------------------

    def _fetch_all_pages(self, url: str, params: dict) -> list[dict]:
        """Read page 1, 2, 3... until a page comes back empty.

        The `pagina` query parameter is documented for both routes
        ("Pagina consultada (default = 1)"). The page size is decided by the
        Portal, so the loop stops on an empty page, on a repeated page (an API
        that ignores `pagina` would loop forever otherwise) and on the page
        ceiling.
        """
        records: list[dict] = []
        seen_pages: set[str] = set()
        page = 1
        while page <= self.max_pages_per_query:
            page_records = self._get(url, {**params, "pagina": page})
            self.stats.pages_read += 1
            if not page_records:
                break
            fingerprint = _fingerprint(page_records)
            if fingerprint in seen_pages:
                self.log(
                    f"Stopping pagination at page {page} for {url}: the Portal returned "
                    "the same page twice."
                )
                break
            seen_pages.add(fingerprint)
            records.extend(page_records)
            page += 1
        else:
            self.log(
                f"Stopped at the page ceiling ({self.max_pages_per_query}) for {url}. "
                "The result may be incomplete."
            )
        return records

    # -- public API ------------------------------------------------------

    def _fetch_registry(self, path: str, cnpj: str) -> list[dict]:
        """One registry, one CNPJ, with the parameter name that route expects."""
        param = CNPJ_PARAM_BY_PATH[path]
        return self._fetch_all_pages(API_BASE + path, {param: only_digits(cnpj)})

    def fetch_ceis(self, cnpj: str) -> list[dict]:
        """Raw CEIS records for one CNPJ, exactly as the API returned them."""
        return self._fetch_registry(CEIS_PATH, cnpj)

    def fetch_cnep(self, cnpj: str) -> list[dict]:
        """Raw CNEP records for one CNPJ, exactly as the API returned them."""
        return self._fetch_registry(CNEP_PATH, cnpj)

    def fetch_cepim(self, cnpj: str) -> list[dict]:
        """Raw CEPIM records: non-profit entities barred from federal transfers."""
        return self._fetch_registry(CEPIM_PATH, cnpj)

    def fetch_leniencia(self, cnpj: str) -> list[dict]:
        """Raw leniency agreement records (Lei 12.846/2013, CGU/AGU)."""
        return self._fetch_registry(LENIENCIA_PATH, cnpj)

    def fetch_sanctions(self, cnpj: str) -> dict[str, list[dict]]:
        """All four registries for one CNPJ.

        Returns {"ceis": [...], "cnep": [...], "cepim": [...],
        "leniencia": [...]}. The records are raw. Nothing here decides what is
        safe to publish; that is the allow-list in `rules.py`.
        """
        return {
            "ceis": self.fetch_ceis(cnpj),
            "cnep": self.fetch_cnep(cnpj),
            "cepim": self.fetch_cepim(cnpj),
            "leniencia": self.fetch_leniencia(cnpj),
        }


def _json_of(response: Any) -> Any:
    """Read the JSON body of a response object or of a plain dict/list."""
    if isinstance(response, (list, dict)):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        return json_method()
    text = getattr(response, "text", None)
    if text is None:
        raise ValueError("response has neither .json() nor .text")
    return json.loads(text)


def _fingerprint(records: Sequence[dict]) -> str:
    try:
        return json.dumps(records, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - fingerprinting must never break a run
        return repr(records)


def unique_cnpjs(values: Iterable[Any]) -> tuple[list[str], list[str]]:
    """Split the input list into (valid 14 digit CNPJs, rejected entries).

    Order is preserved and duplicates are removed, because the user pays per
    company checked and must not pay twice for the same company.
    """
    valid: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        digits = only_digits(value)
        if len(digits) != 14:
            rejected.append(str(value))
            continue
        if digits in seen:
            continue
        seen.add(digits)
        valid.append(digits)
    return valid, rejected
