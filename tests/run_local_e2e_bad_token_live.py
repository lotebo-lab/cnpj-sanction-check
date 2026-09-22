"""End-to-end run against the LIVE Portal da Transparencia with a bad token.

Why this script exists
----------------------
The Apify platform refuses to make an Actor public before it has run at least
once, and the only run we can fire from the lab carries a token that is not a
real Portal da Transparencia token, because the real token belongs to the
buyer. So the behaviour on a refused token is the first thing the platform and
the first buyer will see. It has to end with exit code 0, say plainly that the
token is the problem, invent no data, and charge nothing.

Difference from `run_local_e2e_bad_token.py`
--------------------------------------------
That script substitutes the HTTP transport with a fake that answers 401. This
one substitutes NOTHING. It lets `src/portal_client.py` open a real TCP
connection to https://api.portaldatransparencia.gov.br and be refused by the
real service, so the 401 in the log is the Portal's own answer, not ours.

Measured on 2026-09-20 from this session, with curl:

    curl -H "chave-api-dados: TOKEN-INVALIDO-LOTEBO-TESTE" \
      "https://api.portaldatransparencia.gov.br/api-de-dados/ceis?codigoSancionado=11222333000181&pagina=1"
    {"Erro na API":"Chave de API inválida!"}
    HTTP_STATUS=401

`ACTOR_TEST_PAY_PER_EVENT` is set below, which is the SDK's own switch for
exercising pay-per-event outside the platform, so the charging manager really
counts charges instead of ignoring them. That is what makes the line
"Charge calls recorded by the SDK charging log: 0" a fact about this run.

Usage:
    python tests/run_local_e2e_bad_token_live.py <run-dir>
"""

from __future__ import annotations

import os

# Must be set before the Apify SDK reads the environment.
os.environ.setdefault("ACTOR_TEST_PAY_PER_EVENT", "true")

import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
ACTOR_DIR = HERE.parent
sys.path.insert(0, str(ACTOR_DIR / "src"))

# A string that is not a Portal token. It is the kind of value a run fired from
# the lab carries, since the real token belongs to the buyer.
INVALID_TOKEN = "invalid-token-this-is-not-a-portal-token"


def main() -> int:
    run_dir = Path(sys.argv[1]).resolve()

    actor_input = {
        "cnpjs": ["11.222.333/0001-81", "99888777000166"],
        "portalToken": INVALID_TOKEN,
        "maxCnpjs": 200,
    }

    input_dir = run_dir / "storage" / "key_value_stores" / "default"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "INPUT.json").write_text(json.dumps(actor_input, indent=2))
    os.chdir(run_dir)

    printable_input = {**actor_input, "portalToken": "<invalid token of this live run>"}
    print(f"Actor dir: {ACTOR_DIR}")
    print(f"Run dir:   {run_dir}")
    print("Transport: REAL. No adapter installed, no mock, no patch. The client "
          "opens a real connection to https://api.portaldatransparencia.gov.br "
          "and is refused by the real service.")
    print(f"ACTOR_TEST_PAY_PER_EVENT={os.environ.get('ACTOR_TEST_PAY_PER_EVENT')}")
    print(f"Actor input: {json.dumps(printable_input)}")
    print("-" * 72, flush=True)

    import main as actor_main  # noqa: PLC0415 - after sys.path setup

    actor_exit_code = 0
    crashed = None
    try:
        asyncio.run(actor_main.main())
    except SystemExit as exc:
        actor_exit_code = 0 if exc.code is None else int(exc.code)
    except BaseException as exc:  # noqa: BLE001 - this is what we are measuring
        crashed = f"{exc.__class__.__name__}: {exc}"
        actor_exit_code = 1

    print("-" * 72, flush=True)
    print(f"Actor raised: {crashed or 'nothing'}")
    print(f"Actor lifecycle exit code: {actor_exit_code}")

    dataset_dir = run_dir / "storage" / "datasets" / "default"
    rows = []
    if dataset_dir.is_dir():
        for item in sorted(dataset_dir.glob("[0-9]*.json")):
            rows.append(json.loads(item.read_text()))
    print(f"Dataset rows written: {len(rows)}")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, indent=2))

    kvs_dir = run_dir / "storage" / "key_value_stores" / "default"
    summary_file = kvs_dir / "SUMMARY"
    if not summary_file.is_file():
        summary_file = kvs_dir / "SUMMARY.json"
    summary = None
    if summary_file.is_file():
        summary = json.loads(summary_file.read_text())
        print("SUMMARY record:")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"SUMMARY record not found at {summary_file}")

    charging_log_dir = run_dir / "storage" / "datasets" / "charging_log"
    charge_rows = []
    if charging_log_dir.is_dir():
        for item in sorted(charging_log_dir.glob("[0-9]*.json")):
            charge_rows.append(json.loads(item.read_text()))
    print(f"Charge calls recorded by the SDK charging log: {len(charge_rows)}")
    for row in charge_rows:
        print(json.dumps(row, ensure_ascii=False))

    flat = json.dumps(rows, ensure_ascii=False) + json.dumps(summary, ensure_ascii=False)
    print(f"Invalid token found anywhere in the output: {INVALID_TOKEN in flat}")
    print(f"authError in SUMMARY: "
          f"{json.dumps((summary or {}).get('authError'), ensure_ascii=False)}")
    print(f"chargedEvents in SUMMARY: "
          f"{json.dumps((summary or {}).get('chargedEvents'), ensure_ascii=False)}")
    print(f"exit_code {actor_exit_code}")
    return actor_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
