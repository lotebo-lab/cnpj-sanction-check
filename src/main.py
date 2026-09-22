"""Apify Actor entry point: CNPJ Sanction Check (CEIS, CNEP, CEPIM, leniency).

Input is a list of Brazilian company numbers (CNPJ) and the user's own free
Portal da Transparencia API token. For each CNPJ the Actor reads the four
federal sanction registries, drops every field that is not on the allow-list in
`rules.py`, and pushes one dataset row with the verdict, the findings and the
audit trail.

The list of registries lives in one place, `rules.SOURCES`, and both the log
line and the `sources` block of the run summary are built from it, so adding a
registry can never leave a stale name in the output.

The pieces that do the work (`portal_client.py`, `rules.py`) have no Apify
dependency, so they run and are tested locally.
"""

from __future__ import annotations

import asyncio

from apify import Actor

try:  # running inside the Actor image (python src/main.py)
    from portal_client import (
        PortalAuthError,
        PortalClient,
        PortalError,
        unique_cnpjs,
    )
    from rules import (
        DATA_ATTRIBUTION,
        SOURCE_ORDER,
        SOURCES,
        build_error_result,
        build_result,
        unexpected_keys,
    )
    from summary_row import (
        REGISTRY_SENTENCE,
        SUMMARY_ROW_TYPE,
        TOKEN_HELP_URL,
        build_summary_row,
        tag_company_row,
    )
except ImportError:  # running as a package (python -m src.main)
    from .portal_client import (  # type: ignore[no-redef]
        PortalAuthError,
        PortalClient,
        PortalError,
        unique_cnpjs,
    )
    from .rules import (  # type: ignore[no-redef]
        DATA_ATTRIBUTION,
        SOURCE_ORDER,
        SOURCES,
        build_error_result,
        build_result,
        unexpected_keys,
    )
    from .summary_row import (  # type: ignore[no-redef]
        REGISTRY_SENTENCE,
        SUMMARY_ROW_TYPE,
        TOKEN_HELP_URL,
        build_summary_row,
        tag_company_row,
    )

# "CEIS, CNEP, CEPIM and Acordos de Leniencia" and the four official URLs, both
# built from the single list of registries instead of typed again.
REGISTRY_URLS = [SOURCES[name]["url"] for name in SOURCE_ORDER]

# Pay per event. The Actor never charges `actor-start`; Apify does that itself.
EVENT_COMPANY_CHECKED = "company-checked"
EVENT_BATCH_REPORT = "batch-report"

# Hard ceiling for one charge round trip. On the platform a charge is an HTTP
# call to the Apify API. Without a ceiling one hung call blocks the run until
# the platform timeout kills it and the user loses the results that were
# already collected. This is the defect that took down our first Actor in the
# cloud: a charge must never be able to stop the work.
CHARGE_TIMEOUT_SECONDS = 5.0

DEFAULT_MAX_CNPJS = 200
HARD_MAX_CNPJS = 1000


def should_charge_batch_report(successful_checks: int) -> bool:
    """The batch report is charged only when a CNPJ was really checked.

    A run that ends on a rejected token, or on the Portal being unreachable,
    produced no answer about any company. The error rows in the dataset are
    there to explain the failure, not to be sold, so nothing is charged.
    """
    return successful_checks > 0


def auth_error_summary(exc: PortalAuthError, cnpj: str | None = None) -> dict:
    """The `authError` block of the run summary. Never invents a status.

    `httpStatus` is the status the Portal answered (401 or 403). It stays None
    when the token was empty and no call was ever made, so a network failure
    can never be dressed up as a bad token: those raise `PortalRequestError`
    and never reach this function.
    """
    return {
        "httpStatus": getattr(exc, "status_code", None),
        "message": str(exc),
        "firstCnpjNotChecked": cnpj,
        "whatToDo": (
            "The 'portalToken' input field must hold your own free Portal da "
            f"Transparencia token, requested at {TOKEN_HELP_URL} (free, but the "
            "form asks you to sign in with a gov.br account). Every company "
            "this run did not check is unknown, NOT clear, and only companies "
            "that were really screened are charged."
        ),
    }


async def _charge(event_name: str, count: int, state: dict) -> bool:
    """Charge one event. Returns False only when the run's budget is spent.

    Any other problem (charging off, API hiccup, timeout) is a warning in the
    log: the user already started this run, so the work gets finished.
    """
    if state["limit_reached"]:
        return False
    if not state["pay_per_event"]:
        return True
    try:
        result = await asyncio.wait_for(
            Actor.charge(event_name=event_name, count=count),
            timeout=CHARGE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        state["failures"] += 1
        Actor.log.warning(
            f"Charging {event_name} timed out after {CHARGE_TIMEOUT_SECONDS:.0f}s. "
            "Continuing the run."
        )
        return True
    except Exception as exc:  # noqa: BLE001 - never kill a paid run
        state["failures"] += 1
        Actor.log.warning(f"Could not charge {event_name}: {exc}")
        return True

    if getattr(result, "event_charge_limit_reached", False):
        state["limit_reached"] = True
        Actor.log.info(
            "Charge limit reached for this run. Stopping and keeping everything "
            "checked so far."
        )
        return False
    charged = int(getattr(result, "charged_count", 0) or 0)
    if charged < 1:
        state["failures"] += 1
        return True
    state["charged"][event_name] = state["charged"].get(event_name, 0) + charged
    return True


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}

        token = (actor_input.get("portalToken") or "").strip()

        max_cnpjs = int(actor_input.get("maxCnpjs") or DEFAULT_MAX_CNPJS)
        max_cnpjs = max(1, min(max_cnpjs, HARD_MAX_CNPJS))

        cnpjs, rejected = unique_cnpjs(actor_input.get("cnpjs") or [])
        if not cnpjs and not rejected:
            raise ValueError("Input field 'cnpjs' is required: give at least one CNPJ.")
        if rejected:
            Actor.log.warning(
                f"{len(rejected)} input value(s) are not a 14 digit CNPJ and were "
                "skipped. They are listed in the run summary."
            )
        skipped_over_limit = []
        if len(cnpjs) > max_cnpjs:
            skipped_over_limit = cnpjs[max_cnpjs:]
            cnpjs = cnpjs[:max_cnpjs]
            Actor.log.warning(
                f"Input has more CNPJs than the 'maxCnpjs' limit ({max_cnpjs}). "
                f"{len(skipped_over_limit)} were skipped."
            )

        pricing = Actor.get_charging_manager().get_pricing_info()
        charge_state = {
            "pay_per_event": pricing.is_pay_per_event,
            "limit_reached": False,
            "charged": {},
            "failures": 0,
        }
        if not pricing.is_pay_per_event:
            Actor.log.info(
                f"This run is not billed per event (pricing model: {pricing.pricing_model}). "
                "Checking without charging."
            )

        # An empty token never reaches the network: PortalClient refuses to be
        # built. That is an authentication failure like any other, so it is
        # reported the same way and the run still closes cleanly.
        auth_error: dict | None = None
        client: PortalClient | None = None
        try:
            client = PortalClient(token=token, log=Actor.log.info)
        except PortalAuthError as exc:
            auth_error = auth_error_summary(exc)
            Actor.log.error(str(exc))

        if client is not None:
            Actor.log.info(
                f"Checking {len(cnpjs)} CNPJ(s) against {REGISTRY_SENTENCE}."
            )

        rows = 0
        flagged = 0
        clear = 0
        errors = 0
        unmapped_keys: set[str] = set()
        person_records_discarded = 0
        # Records found per registry, and how many companies each registry
        # flagged. Both are zero for every registry in a normal run where
        # nobody is sanctioned, and that zero is a result the buyer paid for.
        findings_by_source = {name: 0 for name in SOURCE_ORDER}
        companies_flagged_by_source = {name: 0 for name in SOURCE_ORDER}

        for cnpj in cnpjs if client is not None else []:
            # The client is synchronous (requests + pacing), so it runs in a
            # worker thread to keep the Actor event loop free.
            try:
                raw = await asyncio.to_thread(client.fetch_sanctions, cnpj)
            except PortalAuthError as exc:
                # A bad token fails every single CNPJ: stop and say why. This
                # is not a crash: the row explains it, the summary states the
                # HTTP status, nothing is charged and the run ends clean.
                auth_error = auth_error_summary(exc, cnpj)
                Actor.log.error(str(exc))
                await Actor.push_data(tag_company_row(build_error_result(cnpj, str(exc))))
                rows += 1
                errors += 1
                break
            except PortalError as exc:
                Actor.log.warning(f"CNPJ {cnpj}: {exc}")
                await Actor.push_data(tag_company_row(build_error_result(cnpj, str(exc))))
                rows += 1
                errors += 1
                continue

            result = build_result(cnpj, raw)
            await Actor.push_data(tag_company_row(result))
            rows += 1
            unmapped_keys.update(result["unmappedKeys"])
            person_records_discarded += result["personRecordsDiscarded"]
            for source, count in (result["findingsBySource"] or {}).items():
                if source not in findings_by_source:
                    continue
                findings_by_source[source] += int(count or 0)
                if count:
                    companies_flagged_by_source[source] += 1
            if result["verdict"] == "flagged":
                flagged += 1
                Actor.log.info(
                    f"CNPJ {result['cnpjFormatted']}: flagged, "
                    f"{result['findingCount']} record(s)."
                )
            else:
                clear += 1
                Actor.log.info(f"CNPJ {result['cnpjFormatted']}: clear.")

            # Charged after the row is safely in the dataset.
            if not await _charge(EVENT_COMPANY_CHECKED, 1, charge_state):
                Actor.log.info("Stopping early: the run reached its charge limit.")
                break

        successful_checks = flagged + clear
        if should_charge_batch_report(successful_checks):
            # One report per run, charged once, only when something was checked.
            await _charge(EVENT_BATCH_REPORT, 1, charge_state)
        else:
            Actor.log.info(
                "No CNPJ was actually checked, so no event was charged for this run."
            )

        # ------------------------------------------------------------------
        # Everything from here on is reporting. No charge call may appear
        # below this line: the summary row is the Actor telling the buyer what
        # happened, and a run that never authenticated must cost nothing.
        # ------------------------------------------------------------------
        summary = {
            "cnpjsRequested": len(cnpjs) + len(skipped_over_limit),
            "cnpjsChecked": rows,
            "flagged": flagged,
            "clear": clear,
            "errors": errors,
            # Companies inside the 'maxCnpjs' ceiling that never got a row at
            # all: the run stopped before reaching them (refused token, charge
            # limit). They are not clear and they are not errors, they are
            # unknown.
            "cnpjsNotChecked": max(0, len(cnpjs) - rows),
            "findingsBySource": dict(findings_by_source),
            "companiesFlaggedBySource": dict(companies_flagged_by_source),
            "invalidInputs": rejected,
            "skippedOverLimit": skipped_over_limit,
            # Built from rules.SOURCES, never written by hand: the summary must
            # declare exactly the registries the run actually queried. It once
            # listed two while the Actor read four, which understates what the
            # buyer paid for.
            "sources": list(REGISTRY_URLS),
            "dataAttribution": DATA_ATTRIBUTION,
            # Filled only when the Portal refused the token (HTTP 401/403) or
            # when no token was given. A network failure is never reported
            # here: it is an error row with its own message.
            "authError": auth_error,
            "apiRequests": client.stats.requests_made if client else 0,
            "apiRetries": client.stats.retries if client else 0,
            "personRecordsDiscarded": person_records_discarded,
            # Key names the API returned that the allow-list does not map.
            # Names only, never values. Used to keep the allow-list honest.
            "unmappedApiKeys": sorted(unmapped_keys),
            # The same list minus every name this build refuses on purpose. It
            # is normally empty, and anything in it is a field the API started
            # sending after our last release: news, not noise.
            "unexpectedApiKeys": unexpected_keys(unmapped_keys),
            "chargedEvents": charge_state["charged"],
            "chargeFailures": charge_state["failures"],
            "chargeLimitReached": charge_state["limit_reached"],
        }

        # The row every successful run writes, findings or none. Before it
        # existed, "the token was refused so nothing was consulted" and "no
        # company is sanctioned" both came out of this Actor as an empty
        # dataset, and an empty dataset in a compliance check reads as "the
        # supplier is clean". The row says which of the two happened, in the
        # dataset itself, where the buyer actually looks.
        summary_row = build_summary_row(
            {
                **summary,
                "invalidInputCount": len(rejected),
                "skippedOverLimitCount": len(skipped_over_limit),
                "cnpjsChecked": successful_checks,
                "cnpjsClear": clear,
                "cnpjsFlagged": flagged,
                "cnpjsWithError": errors,
                "authFailed": auth_error is not None,
                "authErrorHttpStatus": (auth_error or {}).get("httpStatus"),
            }
        )
        await Actor.push_data(summary_row)
        rows += 1
        Actor.log.info(
            f"Wrote {rows} row(s) to dataset "
            f"{Actor.configuration.default_dataset_id} "
            f"({rows - 1} company row(s) plus 1 '{SUMMARY_ROW_TYPE}' row)"
        )

        await Actor.set_value("SUMMARY", summary)
        if auth_error:
            Actor.log.error(
                "Finished without checking any company: the Portal da Transparencia "
                f"refused the token (HTTP {auth_error['httpStatus']}). See 'authError' "
                "in the SUMMARY record and the 'summary' row of the dataset. Nothing "
                "was checked and no event was charged."
            )
        Actor.log.info(
            f"Finished: {rows} row(s), {flagged} flagged, {clear} clear, {errors} error(s)."
        )


if __name__ == "__main__":
    asyncio.run(main())
