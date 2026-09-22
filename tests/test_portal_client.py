"""Offline tests for the Portal da Transparencia client.

No network and no Apify: the HTTP transport is a fake, and the clock and the
sleep function are injected. Runs on its own:

    .venv/bin/python negocios/actor-idoneidade-cnpj/tests/test_portal_client.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.portal_client import (  # noqa: E402
    CEIS_URL,
    CEPIM_URL,
    CNEP_URL,
    LENIENCIA_URL,
    TOKEN_HEADER,
    PortalAuthError,
    PortalClient,
    PortalRequestError,
    brasilia_hour,
    min_interval_for,
    unique_cnpjs,
)

CNPJ = "12345678000199"


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeTransport:
    """Records every call and answers from a scripted list or a callable."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[dict] = []

    def get(self, url, params, headers, timeout):
        self.calls.append(
            {"url": url, "params": dict(params), "headers": dict(headers), "timeout": timeout}
        )
        if callable(self.answers):
            return self.answers(url, params)
        index = min(len(self.calls) - 1, len(self.answers) - 1)
        return self.answers[index]


class FakeClock:
    """Monotonic clock that only moves when the code sleeps."""

    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client(transport, **kwargs) -> PortalClient:
    clock = kwargs.pop("clock", None) or FakeClock()
    return PortalClient(
        token=kwargs.pop("token", "test-token"),
        transport=transport,
        sleep=clock.sleep,
        clock=clock.time,
        **kwargs,
    )


def test_repr_of_the_client_never_shows_the_token():
    secret = "repr-secret-9f3a7c1e"
    client = _client(FakeTransport([FakeResponse(200, [])]), token=secret)
    assert secret not in repr(client)
    assert secret not in str(client)
    assert "token=" not in repr(client)
    # the token is still there for the header
    assert client.token == secret


def test_token_goes_in_the_header_and_never_in_the_query():
    transport = FakeTransport([FakeResponse(200, [])])
    client = _client(transport, token="secret-token")
    client.fetch_ceis(CNPJ)
    call = transport.calls[0]
    assert call["headers"][TOKEN_HEADER] == "secret-token"
    assert "secret-token" not in str(call["params"])
    assert call["params"]["codigoSancionado"] == CNPJ
    assert call["url"] == CEIS_URL


def test_empty_token_is_refused_before_any_call():
    try:
        PortalClient(token="   ", transport=FakeTransport([]))
    except PortalAuthError as exc:
        assert "portalToken" in str(exc)
    else:
        raise AssertionError("an empty token must raise PortalAuthError")


# (d) pagination over two pages of the fake transport
def test_pagination_reads_every_page_until_an_empty_one():
    pages = {
        1: [{"id": 1}, {"id": 2}],
        2: [{"id": 3}],
        3: [],
    }
    transport = FakeTransport(lambda url, params: FakeResponse(200, pages[params["pagina"]]))
    client = _client(transport)
    records = client.fetch_ceis(CNPJ)
    assert [record["id"] for record in records] == [1, 2, 3]
    assert [call["params"]["pagina"] for call in transport.calls] == [1, 2, 3]


def test_pagination_stops_when_the_api_repeats_a_page():
    transport = FakeTransport(lambda url, params: FakeResponse(200, [{"id": 1}]))
    client = _client(transport)
    records = client.fetch_ceis(CNPJ)
    assert len(records) == 1
    assert len(transport.calls) == 2  # page 1, then the repeat that stops it


def test_page_ceiling_is_respected():
    counter = {"n": 0}

    def answer(url, params):
        counter["n"] += 1
        return FakeResponse(200, [{"id": counter["n"]}])

    transport = FakeTransport(answer)
    client = _client(transport, max_pages_per_query=3)
    records = client.fetch_ceis(CNPJ)
    assert len(transport.calls) == 3
    assert len(records) == 3


# (e) 403 gives a clear message, and no retry
def test_403_raises_a_clear_auth_error_without_retrying():
    transport = FakeTransport([FakeResponse(403, {"message": "denied"})])
    client = _client(transport)
    try:
        client.fetch_ceis(CNPJ)
    except PortalAuthError as exc:
        message = str(exc)
        assert "403" in message
        assert "portalToken" in message
        assert "cadastrar-email" in message
        assert len(transport.calls) == 1, "a rejected token must not be retried"
    else:
        raise AssertionError("HTTP 403 must raise PortalAuthError")


def test_401_is_also_an_auth_error():
    transport = FakeTransport([FakeResponse(401, {})])
    client = _client(transport)
    try:
        client.fetch_cnep(CNPJ)
    except PortalAuthError:
        assert transport.calls[0]["url"] == CNEP_URL
    else:
        raise AssertionError("HTTP 401 must raise PortalAuthError")


# (f) retrying stops at the ceiling
def test_retry_stops_at_the_ceiling():
    transport = FakeTransport([FakeResponse(500, None)])
    clock = FakeClock()
    client = _client(transport, clock=clock, max_retries=3, backoff_seconds=1.0)
    try:
        client.fetch_ceis(CNPJ)
    except PortalRequestError as exc:
        assert "4 attempts" in str(exc)
    else:
        raise AssertionError("a permanent HTTP 500 must raise PortalRequestError")
    assert len(transport.calls) == 4, "1 first try + 3 retries"
    assert client.stats.retries == 3
    # backoff doubles and is capped
    backoffs = [value for value in clock.slept if value >= 1.0]
    assert backoffs == [1.0, 2.0, 4.0]


def test_backoff_is_capped():
    transport = FakeTransport([FakeResponse(503, None)])
    clock = FakeClock()
    client = _client(
        transport,
        clock=clock,
        max_retries=5,
        backoff_seconds=10.0,
        max_backoff_seconds=20.0,
    )
    try:
        client.fetch_ceis(CNPJ)
    except PortalRequestError:
        pass
    assert max(clock.slept) == 20.0


def test_transport_exception_is_retried_then_reported():
    def boom(url, params):
        raise TimeoutError("read timed out")

    transport = FakeTransport(boom)
    client = _client(transport, max_retries=2)
    try:
        client.fetch_ceis(CNPJ)
    except PortalRequestError as exc:
        assert "TimeoutError" in str(exc)
        assert "3 attempts" in str(exc)
    else:
        raise AssertionError("a transport failure must raise PortalRequestError")
    assert len(transport.calls) == 3


def test_rate_limit_keeps_the_gap_between_calls():
    pages = {1: [{"id": 1}], 2: []}
    transport = FakeTransport(lambda url, params: FakeResponse(200, pages[params["pagina"]]))
    clock = FakeClock()
    client = _client(transport, clock=clock, min_interval_seconds=0.15)
    client.fetch_ceis(CNPJ)
    # two calls, so one wait of 0.15s between them (400 requests per minute)
    assert clock.slept == [0.15]
    assert round(client.stats.seconds_slept, 4) == 0.15


def test_daytime_window_paces_at_four_hundred_per_minute():
    """06:00-23:59 Brasilia: "o Portal aceita 400 requisicoes por minuto"."""
    pages = {1: [{"id": 1}], 2: []}
    transport = FakeTransport(lambda url, params: FakeResponse(200, pages[params["pagina"]]))
    clock = FakeClock()
    # 12:00 UTC is 09:00 in Brasilia, inside the daytime window.
    noon_utc = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    client = _client(transport, clock=clock, wall_clock=lambda: noon_utc)
    assert client.current_min_interval() == 60.0 / 400.0
    client.fetch_ceis(CNPJ)
    assert clock.slept == [60.0 / 400.0]


def test_night_window_paces_at_seven_hundred_per_minute():
    """00:00-05:59 Brasilia: "sao aceitas 700 requisicoes por minuto"."""
    pages = {1: [{"id": 1}], 2: []}
    transport = FakeTransport(lambda url, params: FakeResponse(200, pages[params["pagina"]]))
    clock = FakeClock()
    # 05:00 UTC is 02:00 in Brasilia, inside the night window.
    night_utc = datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)
    client = _client(transport, clock=clock, wall_clock=lambda: night_utc)
    assert client.current_min_interval() == 60.0 / 700.0
    client.fetch_ceis(CNPJ)
    assert clock.slept == [60.0 / 700.0]


def test_the_two_published_windows_have_the_right_edges():
    # Brasilia is UTC-3 all year (no daylight saving since 2019).
    edges = {
        # UTC instant -> expected Brasilia hour
        datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc): 0,   # 00:00, night
        datetime(2026, 9, 20, 8, 59, tzinfo=timezone.utc): 5,  # 05:59, night
        datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc): 6,   # 06:00, day
        datetime(2026, 9, 21, 2, 59, tzinfo=timezone.utc): 23,  # 23:59, day
    }
    for moment, expected_hour in edges.items():
        assert brasilia_hour(moment) == expected_hour
    assert min_interval_for(datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)) == 60.0 / 700.0
    assert min_interval_for(datetime(2026, 9, 20, 8, 59, tzinfo=timezone.utc)) == 60.0 / 700.0
    assert min_interval_for(datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)) == 60.0 / 400.0
    assert min_interval_for(datetime(2026, 9, 21, 2, 59, tzinfo=timezone.utc)) == 60.0 / 400.0


def test_pacing_follows_the_clock_when_the_window_changes():
    """A long run that crosses 06:00 slows down instead of overshooting."""
    pages = {1: [{"id": 1}], 2: [{"id": 2}], 3: []}
    transport = FakeTransport(lambda url, params: FakeResponse(200, pages[params["pagina"]]))
    clock = FakeClock()
    moments = iter(
        [
            datetime(2026, 9, 20, 8, 59, tzinfo=timezone.utc),  # 05:59 Brasilia
            datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc),   # 06:00 Brasilia
            datetime(2026, 9, 20, 9, 1, tzinfo=timezone.utc),   # 06:01 Brasilia
        ]
    )
    client = _client(transport, clock=clock, wall_clock=lambda: next(moments))
    client.fetch_ceis(CNPJ)
    # First call never waits; the two that follow use the window they landed in.
    assert clock.slept == [60.0 / 400.0, 60.0 / 400.0]


def test_404_means_nothing_found_not_a_failure():
    transport = FakeTransport([FakeResponse(404, None)])
    client = _client(transport)
    assert client.fetch_ceis(CNPJ) == []


def test_unexpected_status_is_not_retried():
    transport = FakeTransport([FakeResponse(418, None)])
    client = _client(transport)
    try:
        client.fetch_ceis(CNPJ)
    except PortalRequestError as exc:
        assert "418" in str(exc)
    else:
        raise AssertionError("an unexpected status must raise")
    assert len(transport.calls) == 1


REGISTRY_BY_URL = {
    CEIS_URL: "ceis",
    CNEP_URL: "cnep",
    CEPIM_URL: "cepim",
    LENIENCIA_URL: "leniencia",
}


def test_fetch_sanctions_reads_the_four_registries():
    def answer(url, params):
        if params["pagina"] > 1:
            return FakeResponse(200, [])
        return FakeResponse(200, [{"id": 1, "cadastro": REGISTRY_BY_URL[url]}])

    transport = FakeTransport(answer)
    client = _client(transport)
    raw = client.fetch_sanctions(CNPJ)
    assert sorted(raw) == ["ceis", "cepim", "cnep", "leniencia"]
    for name in raw:
        assert raw[name][0]["cadastro"] == name, name
    urls = {call["url"] for call in transport.calls}
    assert urls == set(REGISTRY_BY_URL)


def test_each_route_gets_the_cnpj_parameter_name_it_documents():
    """CEIS/CNEP take codigoSancionado; CEPIM/leniencia take cnpjSancionado.

    Sending the other name is not an error for the API: it answers 200 with the
    unfiltered first page, which would put another company's sanctions in the
    report of the CNPJ that was asked about.
    """
    transport = FakeTransport(lambda url, params: FakeResponse(200, []))
    client = _client(transport)
    client.fetch_sanctions(CNPJ)

    expected = {
        CEIS_URL: "codigoSancionado",
        CNEP_URL: "codigoSancionado",
        CEPIM_URL: "cnpjSancionado",
        LENIENCIA_URL: "cnpjSancionado",
    }
    seen = {call["url"]: dict(call["params"]) for call in transport.calls}
    assert set(seen) == set(expected), seen
    for url, param in expected.items():
        assert param in seen[url], (url, seen[url])
        assert seen[url][param] == CNPJ, (url, seen[url])
        other = "cnpjSancionado" if param == "codigoSancionado" else "codigoSancionado"
        assert other not in seen[url], (url, seen[url])


def test_transport_can_be_a_plain_callable():
    calls: list[str] = []

    def transport(url, params, headers, timeout):
        calls.append(url)
        return FakeResponse(200, [])

    client = _client(transport)
    assert client.fetch_ceis(CNPJ) == []
    assert calls == [CEIS_URL]


def test_wrapped_payload_is_understood():
    transport = FakeTransport(
        lambda url, params: FakeResponse(
            200, {"content": [{"id": 1}]} if params["pagina"] == 1 else {"content": []}
        )
    )
    client = _client(transport)
    assert client.fetch_ceis(CNPJ) == [{"id": 1}]


def test_unique_cnpjs_cleans_and_deduplicates():
    valid, rejected = unique_cnpjs(
        ["12.345.678/0001-99", "12345678000199", "123", "", None, "00000000000191"]
    )
    assert valid == ["12345678000199", "00000000000191"]
    assert rejected == ["123", "", "None"]


# --------------------------------------------------------------------------
# The token never leaves this module in any text.
#
# Regression: `requests.utils.check_header_validity` raises
# `InvalidHeader: ... in header value: '<the value>'`. That message used to be
# copied into `last_problem`, then into the `PortalRequestError` text, and
# `src/main.py` writes that text into the log AND into the `error` and
# `justification` fields of the dataset row. A token wrapped over two lines by
# an e-mail client was enough to publish it, and an Apify dataset comes back
# through a signed link.
# --------------------------------------------------------------------------

LEAKY_HEADER_VALUE = "1f2e3d4c5b6a7988776655\n44332211"
TOKEN_HALVES = ("1f2e3d4c5b6a7988776655", "44332211")


def test_token_with_a_line_break_is_refused_without_being_quoted():
    try:
        PortalClient(token=LEAKY_HEADER_VALUE, transport=FakeTransport(lambda url, params: None))
    except PortalAuthError as exc:
        message = str(exc)
        for half in TOKEN_HALVES:
            assert half not in message, f"the refusal message quotes the token: {message}"
        assert "line break" in message
    else:
        raise AssertionError("a token with a line break must be refused")


def test_transport_exception_that_quotes_the_token_is_redacted():
    """Even if a transport invents a message with the token in it, nothing leaks."""

    secret = "abc123def456"

    def explode(url, params):
        raise RuntimeError(f"connection failed with header chave-api-dados: {secret}")

    client = PortalClient(
        token=secret,
        transport=FakeTransport(explode),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
        max_retries=0,
        min_interval_seconds=0,
    )
    try:
        client.fetch_ceis(CNPJ)
    except PortalRequestError as exc:
        message = str(exc)
        assert secret not in message, f"the error message carries the token: {message}"
        assert "[portalToken redacted]" in message
    else:
        raise AssertionError("the call had to fail")


def test_retry_log_line_never_carries_the_token():
    secret = "abc123def456"
    lines: list[str] = []

    def explode(url, params):
        raise RuntimeError(f"boom {secret}")

    client = PortalClient(
        token=secret,
        transport=FakeTransport(explode),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
        max_retries=1,
        min_interval_seconds=0,
        log=lines.append,
    )
    try:
        client.fetch_ceis(CNPJ)
    except PortalRequestError:
        pass
    assert lines, "the retry should have been logged"
    for line in lines:
        assert secret not in line, f"a log line carries the token: {line}"


def test_real_requests_invalid_header_message_would_carry_the_token():
    """Pins the library behaviour this guard exists for.

    If `requests` ever stops quoting the header value, this test fails and the
    guard can be revisited on purpose instead of by accident.
    """
    try:
        import requests
    except ImportError:  # the guard is not about our environment
        return
    try:
        requests.utils.check_header_validity(("chave-api-dados", LEAKY_HEADER_VALUE))
    except Exception as exc:  # noqa: BLE001 - we want whatever it raises
        assert TOKEN_HALVES[0] in f"{exc}", (
            "requests no longer quotes the header value; the redaction guard in "
            "portal_client can be reviewed"
        )
    else:
        raise AssertionError("requests accepted a header value with a line break")


def _run_all() -> int:
    failures: list[str] = []
    ran = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        ran += 1
        try:
            func()
        except AssertionError as exc:
            failures.append(name)
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            failures.append(name)
            print(f"  ERROR {name}: {exc.__class__.__name__}: {exc}")
        else:
            print(f"  PASS  {name}")
    print(f"\n{ran - len(failures)}/{ran} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
