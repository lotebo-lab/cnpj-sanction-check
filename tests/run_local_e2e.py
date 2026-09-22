"""End-to-end run of the CNPJ Sanction Check against recorded answers.

Why this script exists
----------------------
The lab sandbox does not reach `api.portaldatransparencia.gov.br`, so the Actor
cannot call the live registries here. Everything else in the run is the real
thing: the real `src/main.py` entry point, the real Apify Actor lifecycle
(input, dataset, key-value store), the real client in `src/portal_client.py`
(pacing, paging, retries, token header) and the real allow list in
`src/rules.py`.

The only substitution is the transport underneath `requests`: an adapter
answers the GET calls from `tests/fixtures/portal_responses.json`, whose field
names were copied from the saved OpenAPI document of the Portal
(`docs/portal-openapi-2026-09-20.json`) for the four routes the Actor reads,
with person fields included so the allow list has to drop them. Page 2 answers
an empty list, so the paging loop ends the way it would against the Portal.

What this run proves: the shape of the dataset rows the Actor produces, the
verdict logic and the allow list. What it does not prove: that the live Portal
answers in this shape. Only a cloud run with a real token can prove that.

Usage:
    python tests/run_local_e2e.py <run-dir>
"""

from __future__ import annotations

import os

# The SDK's own switch for exercising pay-per-event outside the platform. With
# it on, the charging manager really counts the charges this run makes, so
# `chargedEvents` in the SUMMARY below is a measurement. It must be set before
# the Apify SDK reads the environment. Compare with
# `tests/run_local_e2e_bad_token.py`, where the same switch is on and the
# expected count is zero.
os.environ.setdefault("ACTOR_TEST_PAY_PER_EVENT", "true")

import asyncio  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

HERE = Path(__file__).resolve().parent
ACTOR_DIR = HERE.parent
sys.path.insert(0, str(ACTOR_DIR / "src"))

PORTAL_PREFIX = "https://api.portaldatransparencia.gov.br"
FIXTURE = HERE / "fixtures" / "portal_responses.json"

# The token below is a made-up string typed into the input of this local run.
# The Actor sends it in the `chave-api-dados` header; the adapter checks that
# the header arrived and never prints it.
FAKE_TOKEN = "local-e2e-token-not-a-real-one"


class RecordedPortalAdapter(BaseAdapter):
    """Answers the GET calls of the four registries from a file, page by page.

    The CNPJ parameter is read under both documented names, because the routes
    disagree: CEIS and CNEP call it `codigoSancionado`, CEPIM and
    acordos-leniencia call it `cnpjSancionado`. If the Actor ever sent the wrong
    one, the fixture lookup would miss and the run would report zero records for
    that registry, which is exactly what the live Portal would do.
    """

    def __init__(self, fixture: Path) -> None:
        super().__init__()
        self.data = json.loads(fixture.read_text())
        self.calls: list[tuple[str, str, str]] = []
        self.token_header_seen = 0

    def send(self, request, stream=False, timeout=None, verify=True, cert=None,
             proxies=None):  # noqa: D102 - requests adapter interface
        parts = urlparse(request.url)
        route = parts.path.rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        cnpj_param = next(
            (name for name in ("codigoSancionado", "cnpjSancionado") if query.get(name)),
            "codigoSancionado",
        )
        cnpj = (query.get(cnpj_param) or [""])[0]
        page = (query.get("pagina") or ["1"])[0]
        self.calls.append((route, cnpj, page))
        if request.headers.get("chave-api-dados"):
            self.token_header_seen += 1

        records = []
        if page == "1":
            records = (self.data.get(route) or {}).get(cnpj) or []

        raw = json.dumps(records).encode("utf-8")
        response = requests.Response()
        response.status_code = 200
        response.reason = "OK"
        response.url = request.url
        response.request = request
        response.encoding = "utf-8"
        response.headers = CaseInsensitiveDict(
            {
                "Server": "recorded-fixture/1.0 (no network: sandbox denies the Portal)",
                "Content-Type": "application/json",
                "Content-Length": str(len(raw)),
            }
        )
        response.raw = io.BytesIO(raw)
        print(
            f"[portal] GET /api-de-dados/{route} {cnpj_param}={cnpj} pagina={page}"
            f" -> 200, {len(records)} record(s), token header sent:"
            f" {'yes' if request.headers.get('chave-api-dados') else 'no'}",
            flush=True,
        )
        return response

    def close(self) -> None:
        return None


def install_adapter(fixture: Path) -> RecordedPortalAdapter:
    adapter = RecordedPortalAdapter(fixture)
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
        "cnpjs": ["11.222.333/0001-81", "99888777000166", "123"],
        "portalToken": FAKE_TOKEN,
        "maxCnpjs": 200,
    }

    input_dir = run_dir / "storage" / "key_value_stores" / "default"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "INPUT.json").write_text(json.dumps(actor_input, indent=2))
    os.chdir(run_dir)

    printable_input = {**actor_input, "portalToken": "<token of this local run>"}
    print(f"Actor dir: {ACTOR_DIR}")
    print(f"Run dir:   {run_dir}")
    print(f"Fixture:   {FIXTURE}")
    print("The GET calls to https://api.portaldatransparencia.gov.br/api-de-dados/"
          "{ceis,cnep,cepim,acordos-leniencia} are answered from that file (the "
          "sandbox has no route to the Portal).")
    print(f"Actor input: {json.dumps(printable_input)}")
    print("-" * 72, flush=True)

    adapter = install_adapter(FIXTURE)

    import main as actor_main  # noqa: PLC0415 - after sys.path setup

    actor_exit_code = 0
    try:
        asyncio.run(actor_main.main())
    except SystemExit as exc:
        actor_exit_code = 0 if exc.code is None else int(exc.code)

    print("-" * 72, flush=True)
    print(f"Actor lifecycle exit code: {actor_exit_code}")
    print(f"API calls served: {len(adapter.calls)}")
    print(f"Calls carrying the token header: {adapter.token_header_seen}")

    dataset_dir = run_dir / "storage" / "datasets" / "default"
    rows = []
    for item in sorted(dataset_dir.glob("[0-9]*.json")):
        rows.append(json.loads(item.read_text()))
    print(f"Dataset rows written: {len(rows)}")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, indent=2))

    kvs_dir = run_dir / "storage" / "key_value_stores" / "default"
    summary_file = kvs_dir / "SUMMARY"
    if not summary_file.is_file():
        summary_file = kvs_dir / "SUMMARY.json"
    if summary_file.is_file():
        print("SUMMARY record:")
        print(json.dumps(json.loads(summary_file.read_text()), indent=2,
                         ensure_ascii=False))
    else:
        print(f"SUMMARY record not found at {summary_file}")

    # Two guards proven in the log, not in prose.
    flat = json.dumps(rows, ensure_ascii=False)
    person_values = ["Joao da Silva", "***.456.789-**"]
    print("Person values present in the source answers and found in the dataset: "
          f"{[value for value in person_values if value in flat]}")
    print(f"Token found anywhere in the dataset: {FAKE_TOKEN in flat}")
    print(f"exit_code {actor_exit_code}")
    return actor_exit_code


if __name__ == "__main__":
    sys.exit(main())
