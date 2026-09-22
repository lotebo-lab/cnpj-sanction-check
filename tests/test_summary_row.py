"""The dataset never comes back empty, and silence never reads as "clear".

This is the regression test for the worst defect this Actor could have. It used
to write one row per CNPJ and nothing else, so two opposite runs produced the
same dataset:

1. the token worked and no company is sanctioned  -> empty dataset;
2. the Portal da Transparencia refused the token, so nothing was ever looked
   up -> empty dataset (an empty `portalToken` never even builds the client).

A buyer reading case 2 concludes the supplier is clean when in fact nobody
consulted anything.

The test drives `src/main.py` itself, with the Apify SDK and the Portal client
replaced by fakes, so what it proves is that `Actor.push_data` really receives
the summary row, not that a helper can build one. No network: no HTTP call is
made by any of the scenarios below.

    .venv/bin/python negocios/actor-idoneidade-cnpj/tests/test_summary_row.py
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import main as actor_main  # noqa: E402
from src.portal_client import PortalAuthError  # noqa: E402
from src.summary_row import SUMMARY_ROW_TYPE  # noqa: E402

CNPJ_A = "00000000000191"
CNPJ_B = "12345678000199"

# One CEIS record shaped like `CeisDTO` in docs/portal-openapi-2026-09-20.json.
CEIS_RECORD = {
    "id": 555,
    "dataInicioSancao": "01/03/2025",
    "tipoSancao": {"descricaoResumida": "Inidoneidade"},
    "orgaoSancionador": {"nome": "Ministerio da Saude"},
    "pessoa": {
        "cnpjFormatado": "12.345.678/0001-99",
        "razaoSocialReceita": "EMPRESA EXEMPLO LTDA",
        "tipo": "Pessoa Juridica",
    },
}

NOTHING = {"ceis": [], "cnep": [], "cepim": [], "leniencia": []}


# --------------------------------------------------------------------------
# Fakes: the slice of the Apify SDK and of PortalClient that main.py touches.
# --------------------------------------------------------------------------
@dataclass
class FakeChargeResult:
    event_charge_limit_reached: bool = False
    charged_count: int = 1
    chargeable_within_limit: dict = field(default_factory=dict)


class FakeLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message) -> None:  # noqa: ANN001
        self.lines.append(str(message))

    def warning(self, message) -> None:  # noqa: ANN001
        self.lines.append(f"[warn] {message}")

    def error(self, message) -> None:  # noqa: ANN001
        self.lines.append(f"[error] {message}")


class FakePricing:
    is_pay_per_event = True
    pricing_model = "PAY_PER_EVENT"


class FakeChargingManager:
    def get_pricing_info(self) -> FakePricing:
        return FakePricing()


class FakeConfiguration:
    default_dataset_id = "FAKE_DATASET"


class FakeActor:
    def __init__(self, actor_input: dict) -> None:
        self.input = actor_input
        self.log = FakeLog()
        self.configuration = FakeConfiguration()
        self.pushed: list[dict] = []
        self.values: dict = {}
        self.charges: list[tuple[str, int]] = []

    async def __aenter__(self) -> "FakeActor":
        return self

    async def __aexit__(self, *exc) -> bool:  # noqa: ANN001
        return False

    async def get_input(self) -> dict:
        return self.input

    def get_charging_manager(self) -> FakeChargingManager:
        return FakeChargingManager()

    async def charge(self, event_name: str, count: int = 1) -> FakeChargeResult:
        self.charges.append((event_name, count))
        return FakeChargeResult(charged_count=count)

    async def push_data(self, rows) -> None:  # noqa: ANN001
        self.pushed.extend(rows if isinstance(rows, list) else [rows])

    async def set_value(self, key: str, value) -> None:  # noqa: ANN001
        self.values[key] = value


class FakeStats:
    requests_made = 8
    retries = 0


class FakePortalClient:
    """Answers `fetch_sanctions` from a script. Never opens a socket.

    `answers` maps a CNPJ to either a payload dict or an exception to raise.
    `build_error` is the exception raised by the constructor, which is how an
    empty `portalToken` fails in the real client.
    """

    build_error: Exception | None = None
    answers: dict = {}

    def __init__(self, token: str = "", log=None, **kwargs) -> None:  # noqa: ANN001
        if type(self).build_error is not None:
            raise type(self).build_error
        self.token = token
        self.stats = FakeStats()

    def fetch_sanctions(self, cnpj: str) -> dict:
        answer = type(self).answers.get(cnpj, NOTHING)
        if isinstance(answer, Exception):
            raise answer
        return answer


def run_actor(
    actor_input: dict,
    answers: dict | None = None,
    build_error: Exception | None = None,
) -> FakeActor:
    """Run `src.main.main()` with the fakes in place, and always restore."""
    actor = FakeActor(actor_input)
    real_actor = actor_main.Actor
    real_client = actor_main.PortalClient
    FakePortalClient.answers = answers or {}
    FakePortalClient.build_error = build_error
    actor_main.Actor = actor
    actor_main.PortalClient = FakePortalClient
    try:
        asyncio.run(actor_main.main())
    finally:
        actor_main.Actor = real_actor
        actor_main.PortalClient = real_client
        FakePortalClient.answers = {}
        FakePortalClient.build_error = None
    return actor


def _rows(actor: FakeActor) -> tuple[list[dict], list[dict]]:
    summary = [r for r in actor.pushed if r.get("rowType") == SUMMARY_ROW_TYPE]
    company = [r for r in actor.pushed if r.get("rowType") == "company"]
    return company, summary


# --------------------------------------------------------------------------
# 1. The run that finds nothing. The common case, and it used to be empty.
# --------------------------------------------------------------------------
def test_a_run_with_no_finding_still_writes_the_summary_row() -> None:
    actor = run_actor(
        {"cnpjs": [CNPJ_A, CNPJ_B], "portalToken": "a-real-looking-token"}
    )
    company, summary_rows = _rows(actor)

    assert len(actor.pushed) == 3, actor.pushed
    assert len(company) == 2, company
    assert len(summary_rows) == 1, summary_rows
    row = summary_rows[0]

    assert row["rowType"] == SUMMARY_ROW_TYPE
    assert actor.pushed[-1] is row, "the summary row must be the last one"
    assert all(r["verdict"] == "clear" for r in company)

    assert row["cnpjsRequested"] == 2, row
    assert row["cnpjsChecked"] == 2, row
    assert row["cnpjsClear"] == 2, row
    assert row["cnpjsFlagged"] == 0, row
    assert row["cnpjsWithError"] == 0, row
    assert row["cnpjsNotChecked"] == 0, row
    assert row["authFailed"] is False, row
    assert row["registriesChecked"] == [
        "CEIS",
        "CNEP",
        "CEPIM",
        "Acordos de Leniencia",
    ], row
    # Looked and found nothing: the four registries report zero, explicitly.
    assert row["findingsByRegistry"] == {
        "CEIS": 0,
        "CNEP": 0,
        "CEPIM": 0,
        "Acordos de Leniencia": 0,
    }, row
    assert row["companiesFlaggedByRegistry"] == {
        "CEIS": 0,
        "CNEP": 0,
        "CEPIM": 0,
        "Acordos de Leniencia": 0,
    }, row

    message = row["message"]
    assert "2 company number(s) checked" in message, message
    assert "none of them has a record" in message, message
    assert "not a certificate of good standing" in message, message
    assert "NOT CHECKED" not in message, message

    # The field the owner asked to keep, still there.
    assert "unmappedApiKeys" in row, row
    # The row says what the run was charged, and the SUMMARY record agrees.
    assert row["chargedEvents"] == {"company-checked": 2, "batch-report": 1}, row
    assert actor.values["SUMMARY"]["chargedEvents"] == row["chargedEvents"]
    assert any("Wrote 3 row(s)" in line for line in actor.log.lines), actor.log.lines


def test_a_flagged_run_names_the_registry_in_the_summary_row() -> None:
    actor = run_actor(
        {"cnpjs": [CNPJ_A, CNPJ_B], "portalToken": "a-real-looking-token"},
        answers={CNPJ_B: {**NOTHING, "ceis": [CEIS_RECORD]}},
    )
    company, summary_rows = _rows(actor)
    row = summary_rows[0]

    assert len(company) == 2
    assert row["cnpjsFlagged"] == 1, row
    assert row["cnpjsClear"] == 1, row
    assert row["findingsByRegistry"]["CEIS"] == 1, row
    assert row["companiesFlaggedByRegistry"] == {
        "CEIS": 1,
        "CNEP": 0,
        "CEPIM": 0,
        "Acordos de Leniencia": 0,
    }, row
    assert "1 flagged" in row["message"], row["message"]
    assert "CEIS: 1" in row["message"], row["message"]


# --------------------------------------------------------------------------
# 2. The run that never authenticated. It must not look like case 1.
# --------------------------------------------------------------------------
def _assert_says_not_checked(row: dict, expected_status) -> None:  # noqa: ANN001
    message = row["message"]
    assert row["rowType"] == SUMMARY_ROW_TYPE, row
    assert row["authFailed"] is True, row
    assert row["authErrorHttpStatus"] == expected_status, row
    assert row["cnpjsChecked"] == 0, row
    assert row["cnpjsClear"] == 0, row
    assert row["cnpjsNotChecked"] >= 1, row
    assert message.startswith("NOT CHECKED"), message
    assert "were NOT checked" in message, message
    assert "authentication" in message, message
    assert "portalToken" in message, message
    # Nothing in this row may read as a clean bill of health.
    lowered = message.lower()
    for forbidden in ("no record", "none of them", "clear result", "no sanction"):
        assert forbidden not in lowered.replace("not clear", ""), (forbidden, message)
    # And no per-registry table of zeros next to it either: zeros there mean
    # "we looked", and nobody looked.
    assert row["findingsByRegistry"] == {}, row
    assert row["companiesFlaggedByRegistry"] == {}, row


def test_an_empty_token_writes_a_row_saying_the_check_did_not_happen() -> None:
    """The empty-token path: the client is never built, so no CNPJ is read."""
    actor = run_actor(
        {"cnpjs": [CNPJ_A, CNPJ_B], "portalToken": "   "},
        build_error=PortalAuthError(
            "Input field 'portalToken' is empty.", status_code=None
        ),
    )
    company, summary_rows = _rows(actor)

    assert company == [], company
    assert len(summary_rows) == 1, actor.pushed
    assert len(actor.pushed) == 1, "the dataset must not be empty"
    _assert_says_not_checked(summary_rows[0], None)
    assert summary_rows[0]["cnpjsNotChecked"] == 2, summary_rows[0]
    assert actor.charges == [], f"an unauthenticated run charged: {actor.charges}"


def test_a_refused_token_writes_a_row_saying_the_check_did_not_happen() -> None:
    """The HTTP 401 path: the Portal answers, and refuses."""
    refusal = PortalAuthError(
        "The Portal da Transparencia refused the token (HTTP 401).", status_code=401
    )
    actor = run_actor(
        {"cnpjs": [CNPJ_A, CNPJ_B], "portalToken": "wrong-token"},
        answers={CNPJ_A: refusal, CNPJ_B: refusal},
    )
    company, summary_rows = _rows(actor)

    # One company row explaining the failure, never a verdict we did not earn.
    assert len(company) == 1, company
    assert company[0]["verdict"] == "error", company[0]
    assert len(summary_rows) == 1, actor.pushed
    _assert_says_not_checked(summary_rows[0], 401)
    assert summary_rows[0]["cnpjsWithError"] == 1, summary_rows[0]
    assert summary_rows[0]["cnpjsNotChecked"] == 1, summary_rows[0]
    assert actor.charges == [], f"an unauthenticated run charged: {actor.charges}"
    assert actor.values["SUMMARY"]["authError"]["httpStatus"] == 401


# --------------------------------------------------------------------------
# 3. The summary row is a report, never a charge.
# --------------------------------------------------------------------------
def test_the_summary_row_is_written_after_the_last_charge_and_adds_none() -> None:
    """Read the source: no charge call may sit below the summary push."""
    source = (ROOT / "src" / "main.py").read_text()
    push = "await Actor.push_data(summary_row)"
    assert push in source, "src/main.py no longer pushes the summary row"
    assert source.index(f"await _charge(EVENT_BATCH_REPORT") < source.index(push), (
        "the summary row must be written after the charging decisions"
    )
    tail = source.split(push, 1)[1]
    assert "_charge" not in tail and "Actor.charge" not in tail, (
        "no charge call may sit between the summary row and the end of the run"
    )


def test_a_clean_run_charges_one_event_per_company_and_one_report() -> None:
    """The count the buyer is billed does not change because of the new row."""
    actor = run_actor(
        {"cnpjs": [CNPJ_A, CNPJ_B], "portalToken": "a-real-looking-token"}
    )
    assert actor.charges == [
        ("company-checked", 1),
        ("company-checked", 1),
        ("batch-report", 1),
    ], actor.charges
    assert len(actor.pushed) == 3, "3 rows, still 3 charged events"


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
