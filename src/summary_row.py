"""The one dataset row every successful run writes, findings or none.

Why this file exists
--------------------
This Actor used to write one row per CNPJ and nothing else. Two very different
runs then produced the same dataset:

1. the token worked and none of the companies is sanctioned;
2. the token was refused by the Portal da Transparencia, so **no company was
   ever looked up**.

In case 2 the run ended clean, with exit code 0 and an empty dataset (an empty
`portalToken` never even builds the client, so not a single row was pushed).
For a compliance check that is the worst possible answer: the buyer files an
empty result as "supplier is clear" when in truth nobody consulted anything.

The summary row says, inside the dataset itself, how many company numbers were
asked for, how many were really checked, how many came back clear, how many
were flagged and in which of the four federal registries. When the
authentication failed it says, in plain English, that the check did NOT happen
and that the buyer has to fix their own `portalToken`, and it never reports a
clear count in that case.

Nothing here charges anything. The module has no Apify import on purpose: it is
pure data, it is called after the work and after the charging decisions are
done, and the events this Actor charges (`company-checked` per company really
screened, `batch-report` once per run and only when at least one company was
screened) are decided in `src/main.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

try:  # running inside the Actor image (python src/main.py)
    from rules import SOURCE_ORDER, SOURCES
except ImportError:  # running as a package (python -m src.main)
    from .rules import SOURCE_ORDER, SOURCES  # type: ignore[no-redef]

# Every row carries this field, so a reader can tell a company verdict from the
# run summary with one comparison and drop the summary if it only wants
# verdicts.
ROW_TYPE_FIELD = "rowType"
COMPANY_ROW_TYPE = "company"
SUMMARY_ROW_TYPE = "summary"

# "CEIS, CNEP, CEPIM and Acordos de Leniencia", built from the single list of
# registries in `rules.SOURCES` instead of typed again.
REGISTRY_LABELS = [SOURCES[name]["label"] for name in SOURCE_ORDER]
REGISTRY_SENTENCE = (
    ", ".join(REGISTRY_LABELS[:-1]) + " and " + REGISTRY_LABELS[-1]
    if len(REGISTRY_LABELS) > 1
    else REGISTRY_LABELS[0]
)

# Where the buyer gets their own token. Same URL the input schema shows.
TOKEN_HELP_URL = "https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email"

# Fields that exist only on the summary row. Declared here so a schema test can
# check the dataset schema against the code instead of against a list typed by
# hand twice.
SUMMARY_ONLY_FIELDS = (
    "finishedAt",
    "cnpjsRequested",
    "cnpjsChecked",
    "cnpjsClear",
    "cnpjsFlagged",
    "cnpjsWithError",
    "cnpjsNotChecked",
    "invalidInputCount",
    "skippedOverLimitCount",
    "registriesChecked",
    "findingsByRegistry",
    "companiesFlaggedByRegistry",
    "authFailed",
    "authErrorHttpStatus",
    "unmappedApiKeys",
    "unexpectedApiKeys",
    "chargedEvents",
    "chargeFailures",
    "chargeLimitReached",
    "dataAttribution",
    "message",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tag_company_row(row: dict) -> dict:
    """Stamp `rowType` on a company row, without touching its content.

    A copy is returned. `rules.build_result` builds the row through the LGPD
    allow-list, so the row type is added here, at the door of the dataset, and
    never inside the allow-list.
    """
    return {ROW_TYPE_FIELD: COMPANY_ROW_TYPE, **row}


def _by_label(counts: dict) -> dict:
    """{'ceis': 2} -> {'CEIS': 2}, in the fixed order of the four registries."""
    return {
        SOURCES[name]["label"]: int(counts.get(name, 0) or 0)
        for name in SOURCE_ORDER
    }


def auth_failure_message(report: dict) -> str:
    """The sentence a run that never authenticated must show. No verdict in it.

    It has to be impossible to read as "the companies are clean": it names what
    did not happen, says the run proves nothing about the companies, and points
    at the buyer's own token.
    """
    requested = int(report.get("cnpjsRequested", 0) or 0)
    checked = int(report.get("cnpjsChecked", 0) or 0)
    not_checked = max(0, requested - checked)
    status = report.get("authErrorHttpStatus")
    status_text = f"HTTP {status}" if status else "no token was given"
    opening = (
        f"NOT CHECKED. The Portal da Transparencia API refused this run's "
        f"authentication ({status_text}), so {not_checked} of {requested} "
        f"company number(s) were NOT checked against {REGISTRY_SENTENCE}."
    )
    if checked:
        # The token was accepted and then refused partway through. Saying
        # "nothing was checked" would be as wrong as the silence this row
        # replaces, so the two groups are named separately.
        opening += (
            f" The {checked} company number(s) checked before the refusal have "
            "their own row with a verdict; the rest have none."
        )
    return (
        opening
        + " This run says nothing about whether the companies it did not check "
        "are sanctioned: an unchecked company is unknown, NOT clear. Fix the "
        "'portalToken' input field, which must hold your own free Portal da "
        f"Transparencia token ({TOKEN_HELP_URL}), and run again."
    )


def summary_message(report: dict) -> str:
    """One plain sentence with the counts. No promise, no advice, no guess."""
    if report.get("authFailed"):
        return auth_failure_message(report)

    requested = int(report.get("cnpjsRequested", 0) or 0)
    checked = int(report.get("cnpjsChecked", 0) or 0)
    clear = int(report.get("cnpjsClear", 0) or 0)
    flagged = int(report.get("cnpjsFlagged", 0) or 0)
    errors = int(report.get("cnpjsWithError", 0) or 0)
    not_checked = int(report.get("cnpjsNotChecked", 0) or 0)
    per_registry = _by_label(report.get("companiesFlaggedBySource") or {})

    if checked == 0:
        text = (
            f"No company was checked: {requested} company number(s) were given "
            f"and none could be looked up in {REGISTRY_SENTENCE}, so this run "
            "says nothing about whether they are sanctioned."
        )
    elif flagged:
        where = ", ".join(
            f"{label}: {count}" for label, count in per_registry.items() if count
        )
        text = (
            f"{checked} company number(s) checked against {REGISTRY_SENTENCE}: "
            f"{flagged} flagged and {clear} with no record. Companies flagged "
            f"per registry - {where}. Read every record before contracting."
        )
    else:
        text = (
            f"{checked} company number(s) checked against {REGISTRY_SENTENCE}: "
            "none of them has a record in any of the four registries. This is "
            "not a certificate of good standing and it covers only these "
            "federal registries; state, municipal, TCU and court sanctions are "
            "not included."
        )

    if errors:
        text += (
            f" {errors} company number(s) could not be checked; each one has its "
            "own row with verdict 'error' and the reason."
        )
    if not_checked:
        text += (
            f" {not_checked} company number(s) in the input were never looked up "
            "and have no row."
        )
    if report.get("invalidInputCount"):
        text += (
            f" {report['invalidInputCount']} input value(s) are not a 14 digit "
            "CNPJ and were skipped."
        )
    if report.get("skippedOverLimitCount"):
        text += (
            f" {report['skippedOverLimitCount']} company number(s) were skipped "
            "by the 'maxCnpjs' ceiling."
        )
    if report.get("chargeLimitReached"):
        text += (
            " The run stopped early because it reached its pay-per-event charge "
            "limit, so part of the list was not checked."
        )
    return text


def build_summary_row(report: dict) -> dict:
    """The summary row, built from the same numbers the SUMMARY record holds."""
    checked = int(report.get("cnpjsChecked", 0) or 0)
    # A table of zeros per registry means "we looked and found nothing". When
    # no company was screened at all there is nothing to report, and the two
    # must not look alike: the counters are then published empty, not as zeros.
    findings = _by_label(report.get("findingsBySource") or {}) if checked else {}
    flagged_per_registry = (
        _by_label(report.get("companiesFlaggedBySource") or {}) if checked else {}
    )
    return {
        ROW_TYPE_FIELD: SUMMARY_ROW_TYPE,
        "finishedAt": report.get("finishedAt") or _now(),
        "cnpjsRequested": int(report.get("cnpjsRequested", 0) or 0),
        "cnpjsChecked": int(report.get("cnpjsChecked", 0) or 0),
        "cnpjsClear": int(report.get("cnpjsClear", 0) or 0),
        "cnpjsFlagged": int(report.get("cnpjsFlagged", 0) or 0),
        "cnpjsWithError": int(report.get("cnpjsWithError", 0) or 0),
        "cnpjsNotChecked": int(report.get("cnpjsNotChecked", 0) or 0),
        "invalidInputCount": int(report.get("invalidInputCount", 0) or 0),
        "skippedOverLimitCount": int(report.get("skippedOverLimitCount", 0) or 0),
        "registriesChecked": list(REGISTRY_LABELS),
        # Records found per registry, and how many distinct companies each
        # registry flagged. Zero everywhere is a real answer, and it is only
        # published when at least one company was really screened.
        "findingsByRegistry": findings,
        "companiesFlaggedByRegistry": flagged_per_registry,
        "authFailed": bool(report.get("authFailed")),
        "authErrorHttpStatus": report.get("authErrorHttpStatus"),
        # Key names the API returned that the allow-list does not map. Names
        # only, never values. Kept in the summary by explicit decision of the
        # owner, even after the schema was confirmed: the live API is free to
        # drift from the snapshot between two of our releases.
        "unmappedApiKeys": list(report.get("unmappedApiKeys") or []),
        "chargedEvents": dict(report.get("chargedEvents") or {}),
        "chargeFailures": int(report.get("chargeFailures", 0) or 0),
        "chargeLimitReached": bool(report.get("chargeLimitReached", False)),
        "dataAttribution": report.get("dataAttribution"),
        "message": summary_message(report),
    }


__all__ = (
    "COMPANY_ROW_TYPE",
    "REGISTRY_LABELS",
    "REGISTRY_SENTENCE",
    "ROW_TYPE_FIELD",
    "SUMMARY_ONLY_FIELDS",
    "SUMMARY_ROW_TYPE",
    "TOKEN_HELP_URL",
    "auth_failure_message",
    "build_summary_row",
    "summary_message",
    "tag_company_row",
)
