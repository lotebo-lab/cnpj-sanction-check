"""End-to-end run of the CNPJ Sanction Check with a token the Portal refuses.

Why this script exists
----------------------
The Apify platform refuses to make an Actor public before it has run at least
once, and the only run we can fire from the lab carries a token that is not a
real Portal da Transparencia token, because that token belongs to the buyer.
So the behaviour on a refused token is not a corner case here: it is the first
thing the platform and the first buyer will see. It has to end SUCCEEDED, say
plainly that the token is the problem, and charge nothing.

This run is the real thing except for one substitution: the HTTP transport
answers `401 Unauthorized` instead of the Portal. Everything else is real:
`src/main.py`, the Apify Actor lifecycle (input, dataset, key-value store,
charging manager), `src/portal_client.py` and `src/rules.py`.

Why the substitution: this script must stay deterministic and runnable with no
network at all, so the 401 path is covered even when the Portal is down or
unreachable.

CORRECTION, 2026-09-20: an earlier version of this docstring claimed the lab
could not reach `api.portaldatransparencia.gov.br`. That is false. The host is
reachable from this session and the real service answers HTTP 401 to a bad
token:

    curl -H "chave-api-dados: TOKEN-INVALIDO-LOTEBO-TESTE" \
      "https://api.portaldatransparencia.gov.br/api-de-dados/ceis?codigoSancionado=11222333000181&pagina=1"
    {"Erro na API":"Chave de API inválida!"}
    HTTP_STATUS=401

The live end-to-end run lives in `run_local_e2e_bad_token_live.py`, and its
log is `logs/corrida-live-token-invalido-2026-09-20.log`. This fake-transport
script is kept as the offline regression test of the same path.

`ACTOR_TEST_PAY_PER_EVENT` is set below, which is the SDK's own switch for
exercising pay-per-event outside the platform, so the charging manager really
counts charges instead of ignoring them. That is what makes the line
"events charged: {}" in the output a fact about this run and not a promise.

Usage:
    python tests/run_local_e2e_bad_token.py <run-dir>
"""

from __future__ import annotations

import os

# Must be set before the Apify SDK reads the environment.
os.environ.setdefault("ACTOR_TEST_PAY_PER_EVENT", "true")

import asyncio  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import requests  # noqa: E402
from requests.adapters import BaseAdapter  # noqa: E402
from requests.structures import CaseInsensitiveDict  # noqa: E402

HERE = Path(__file__).resolve().parent
ACTOR_DIR = HERE.parent
sys.path.insert(0, str(ACTOR_DIR / "src"))

PORTAL_PREFIX = "https://api.portaldatransparencia.gov.br"

# A string that is not a Portal token. It is the kind of value a run fired from
# the lab carries, since the real token belongs to the buyer.
INVALID_TOKEN = "invalid-token-this-is-not-a-portal-token"

# The body the Portal answers on a refused token, as documented for its 401.
REFUSAL_BODY = {"message": "Unauthorized", "status": 401}


class RefusingPortalAdapter(BaseAdapter):
    """Answers every CEIS/CNEP GET with HTTP 401, like a refused token."""

    def __init__(self, status_code: int = 401) -> None:
        super().__init__()
        self.status_code = status_code
        self.calls: list[str] = []

    def send(self, request, stream=False, timeout=None, verify=True, cert=None,
             proxies=None):  # noqa: D102 - requests adapter interface
        self.calls.append(request.url)
        raw = json.dumps(REFUSAL_BODY).encode("utf-8")
        response = requests.Response()
        response.status_code = self.status_code
        response.reason = "Unauthorized"
        response.url = request.url
        response.request = request
        response.encoding = "utf-8"
        response.headers = CaseInsensitiveDict(
            {
                "Server": "fake-transport/1.0 (no network: this session cannot call the Portal)",
                "Content-Type": "application/json",
                "Content-Length": str(len(raw)),
            }
        )
        response.raw = io.BytesIO(raw)
        print(
            f"[portal] GET {request.url} -> {self.status_code} "
            f"(token header sent: "
            f"{'yes' if request.headers.get('chave-api-dados') else 'no'})",
            flush=True,
        )
        return response

    def close(self) -> None:
        return None


def install_adapter() -> RefusingPortalAdapter:
    adapter = RefusingPortalAdapter()
    original_get_adapter = requests.Session.get_adapter

    def get_adapter(self, url):  # noqa: ANN001 - patching requests
        if url.startswith(PORTAL_PREFIX):
            return adapter
        return original_get_adapter(self, url)

    requests.Session.get_adapter = get_adapter
    return adapter


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

    printable_input = {**actor_input, "portalToken": "<invalid token of this local run>"}
    print(f"Actor dir: {ACTOR_DIR}")
    print(f"Run dir:   {run_dir}")
    print("Transport: fake, answers 401 to every call to "
          "https://api.portaldatransparencia.gov.br (this session is not "
          "allowed to call the Portal).")
    print(f"ACTOR_TEST_PAY_PER_EVENT={os.environ.get('ACTOR_TEST_PAY_PER_EVENT')}")
    print(f"Actor input: {json.dumps(printable_input)}")
    print("-" * 72, flush=True)

    adapter = install_adapter()

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
    print(f"API calls answered with 401: {len(adapter.calls)}")

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

    # The SDK writes one row per charge into this local dataset when
    # ACTOR_TEST_PAY_PER_EVENT is on. Empty means nothing was billed.
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
    sys.exit(main())
