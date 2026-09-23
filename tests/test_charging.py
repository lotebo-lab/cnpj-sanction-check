"""Offline tests for the pay-per-event charging in `src/main.py`.

Why this file exists: the defect that took down our first Actor in the cloud was
a charge call that hung and burned the whole run. So the two promises below are
tested, not assumed:

1. a charge that does not answer inside the ceiling is a warning, and the run
   goes on;
2. a charge that raises is a warning, and the run goes on.

No network and no Apify platform. `main.Actor` is replaced by a fake, so the
`_charge` helper is exercised exactly as written. pytest is not installed in the
lab sandbox and `pip install` has no network there, so this file runs on its
own:

    .venv/bin/python negocios/actor-idoneidade-cnpj/tests/test_charging.py
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import main as actor_main  # noqa: E402


@dataclass
class FakeChargeResult:
    """Same three fields the SDK's ChargeResult carries (apify 4.0.2)."""

    event_charge_limit_reached: bool = False
    charged_count: int = 1
    chargeable_within_limit: dict = field(default_factory=dict)


class FakeLog:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.infos: list[str] = []
        self.errors: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(str(message))

    def info(self, message: str) -> None:
        self.infos.append(str(message))

    def error(self, message: str) -> None:
        self.errors.append(str(message))


class FakeActor:
    """Stands in for `apify.Actor` during the test."""

    def __init__(self, behaviour) -> None:
        self.log = FakeLog()
        self.calls: list[tuple[str, int]] = []
        self._behaviour = behaviour

    async def charge(self, event_name: str, count: int = 1):
        self.calls.append((event_name, count))
        return await self._behaviour(event_name, count)


class _Swap:
    """Put the fake Actor in place, and always put the real one back."""

    def __init__(self, behaviour, timeout: float | None = None) -> None:
        self.fake = FakeActor(behaviour)
        self._timeout = timeout

    def __enter__(self) -> FakeActor:
        self._real_actor = actor_main.Actor
        self._real_timeout = actor_main.CHARGE_TIMEOUT_SECONDS
        actor_main.Actor = self.fake
        if self._timeout is not None:
            actor_main.CHARGE_TIMEOUT_SECONDS = self._timeout
        return self.fake

    def __exit__(self, *exc_info) -> None:
        actor_main.Actor = self._real_actor
        actor_main.CHARGE_TIMEOUT_SECONDS = self._real_timeout


def fresh_state(pay_per_event: bool = True) -> dict:
    return {
        "pay_per_event": pay_per_event,
        "limit_reached": False,
        "charged": {},
        "failures": 0,
    }


def test_the_charge_ceiling_is_a_few_seconds() -> None:
    """The ceiling is part of the contract, not a detail.

    Run dDAxrKSafjiCYssUa of page-audit-tool hung 60.1 s on one failed charge.
    The ceiling has to stay in the low single digits of seconds so that no
    charge can ever hold a finished run hostage.
    """
    assert actor_main.CHARGE_TIMEOUT_SECONDS == 5.0
    assert 0 < actor_main.CHARGE_TIMEOUT_SECONDS <= 5.0


def test_a_normal_charge_is_counted() -> None:
    async def behaviour(event_name, count):
        return FakeChargeResult(charged_count=count)

    state = fresh_state()
    with _Swap(behaviour) as fake:
        assert asyncio.run(
            actor_main._charge(actor_main.EVENT_COMPANY_CHECKED, 1, state)
        ) is True
        assert asyncio.run(
            actor_main._charge(actor_main.EVENT_BATCH_REPORT, 1, state)
        ) is True

    assert fake.calls == [("company-checked", 1), ("batch-report", 1)]
    assert state["charged"] == {"company-checked": 1, "batch-report": 1}
    assert state["failures"] == 0
    assert state["limit_reached"] is False
    assert fake.log.warnings == []


def test_a_hanging_charge_times_out_and_the_run_continues() -> None:
    """The bug we are paying for: a charge that never answers."""

    async def behaviour(event_name, count):
        await asyncio.sleep(5.0)  # far longer than the ceiling used below
        return FakeChargeResult()

    state = fresh_state()
    with _Swap(behaviour, timeout=0.05) as fake:
        kept_going = asyncio.run(
            actor_main._charge(actor_main.EVENT_COMPANY_CHECKED, 1, state)
        )

    assert kept_going is True, "a timed out charge must not stop the run"
    assert state["failures"] == 1
    assert state["charged"] == {}
    assert state["limit_reached"] is False
    assert len(fake.log.warnings) == 1
    assert "timed out" in fake.log.warnings[0]


def test_a_charge_that_raises_is_only_a_warning() -> None:
    async def behaviour(event_name, count):
        raise RuntimeError("apify api 502")

    state = fresh_state()
    with _Swap(behaviour) as fake:
        kept_going = asyncio.run(
            actor_main._charge(actor_main.EVENT_COMPANY_CHECKED, 1, state)
        )

    assert kept_going is True, "a failed charge must not stop the run"
    assert state["failures"] == 1
    assert state["charged"] == {}
    assert len(fake.log.warnings) == 1
    assert "apify api 502" in fake.log.warnings[0]


def test_charge_limit_stops_the_loop_and_is_not_retried() -> None:
    async def behaviour(event_name, count):
        return FakeChargeResult(event_charge_limit_reached=True, charged_count=0)

    state = fresh_state()
    with _Swap(behaviour) as fake:
        first = asyncio.run(
            actor_main._charge(actor_main.EVENT_COMPANY_CHECKED, 1, state)
        )
        second = asyncio.run(
            actor_main._charge(actor_main.EVENT_COMPANY_CHECKED, 1, state)
        )

    assert first is False
    assert second is False
    assert state["limit_reached"] is True
    # Once the limit is known, no further call is made to the platform.
    assert len(fake.calls) == 1


def test_a_run_that_is_not_billed_per_event_is_never_charged() -> None:
    async def behaviour(event_name, count):  # pragma: no cover - must not run
        raise AssertionError("charge must not be called outside pay per event")

    state = fresh_state(pay_per_event=False)
    with _Swap(behaviour) as fake:
        assert asyncio.run(
            actor_main._charge(actor_main.EVENT_COMPANY_CHECKED, 1, state)
        ) is True

    assert fake.calls == []
    assert state["charged"] == {}
    assert state["failures"] == 0


def test_a_charge_the_platform_ignored_is_counted_as_a_failure() -> None:
    """charged_count = 0 without the limit flag: not billed, keep working."""

    async def behaviour(event_name, count):
        return FakeChargeResult(charged_count=0)

    state = fresh_state()
    with _Swap(behaviour):
        assert asyncio.run(
            actor_main._charge(actor_main.EVENT_COMPANY_CHECKED, 1, state)
        ) is True

    assert state["failures"] == 1
    assert state["charged"] == {}


def test_event_names_match_the_actor_definition() -> None:
    """The names in the code and the priced events in actor.json are one list."""
    import json

    definition = json.loads(
        (Path(__file__).resolve().parents[1] / ".actor" / "actor.json").read_text()
    )
    events = definition["pay_per_event"]["actorChargeEvents"]
    assert set(events) == {
        actor_main.EVENT_COMPANY_CHECKED,
        actor_main.EVENT_BATCH_REPORT,
    }
    assert events[actor_main.EVENT_COMPANY_CHECKED]["eventPriceUsd"] == 0.02
    assert events[actor_main.EVENT_BATCH_REPORT]["eventPriceUsd"] == 0.05


def test_the_cnpj_ceiling_per_run_exists_and_matches_the_input_schema() -> None:
    import json

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / ".actor" / "input_schema.json").read_text()
    )
    max_field = schema["properties"]["maxCnpjs"]
    assert max_field["default"] == actor_main.DEFAULT_MAX_CNPJS
    assert max_field["maximum"] == actor_main.HARD_MAX_CNPJS
    assert actor_main.DEFAULT_MAX_CNPJS <= actor_main.HARD_MAX_CNPJS


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
