"""Proves the safety net end to end: PortalClient -> rules -> run summary.

Why this file exists
---------------------
A wrong key name would make the Actor return an empty row instead of failing
loudly, which is the worst kind of bug for a compliance check: silent, and
found by the paying user, not by us.

Since 2026-09-20 the key names are no longer guesses: the official OpenAPI
document was downloaded from `https://api.portaldatransparencia.gov.br/v3/api-docs`
and saved at `docs/portal-openapi-2026-09-20.json`, and
`tests/test_schema_contract.py` checks the allow-list against it in both
directions. So the record below is shaped like the real `CeisDTO`: the exact
key names and the nested objects (`tipoSancao`, `fonteSancao`, `fundamentacao`,
`orgaoSancionador`, `sancionado`, `pessoa`) that the schema declares, instead
of the flat CSV-style record this test used while the names were guesses.

The safety net is `unmappedKeys` (per row, in `rules.build_result`) and
`unmappedApiKeys` (per run, in the `SUMMARY` key-value record written by
`src/main.py`). This test does not touch the Apify SDK or the network: it
wires `PortalClient` to a fake transport (the same pattern as
`tests/test_portal_client.py`), answers with a payload that has one key our
allow-list has never seen, runs it through `rules.build_result` and then
replicates the exact aggregation `src/main.py` does across the batch
(`unmapped_keys.update(result["unmappedKeys"])` -> `sorted(unmapped_keys)` in
`SUMMARY["unmappedApiKeys"]`), so the assertion below is about the same field
name a user would read in the run's SUMMARY, not about an internal detail.

Run on its own:
    ../../.venv/bin/python tests/test_unmapped_keys_summary.py
or from the workspace root:
    .venv/bin/python negocios/actor-idoneidade-cnpj/tests/test_unmapped_keys_summary.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.portal_client import CEIS_URL, CNEP_URL, PortalClient  # noqa: E402
from src.rules import DELIBERATELY_DROPPED, build_result  # noqa: E402

CNPJ = "12345678000199"

# The keys the official schema declares and that `src/rules.py` refuses on
# purpose (personal data and useless contact fields). A real record carries
# them on every call, so they are the expected, constant content of
# `unmappedApiKeys`; drift is anything ON TOP of this set.
REFUSED_ON_PURPOSE = sorted(".".join(path) for path in DELIBERATELY_DROPPED)


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeTransport:
    """Same fake used across this Actor's tests: records calls, scripted answers."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[dict] = []

    def get(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": dict(params)})
        return self.answers(url, params)


# One CEIS record, written with the key names and the nesting that
# `docs/portal-openapi-2026-09-20.json` declares for `CeisDTO`, including the
# three names that were wrong until 2026-09-20: `dataFimSancao` (not
# dataFinalSancao, which is only a query parameter), `dataTransitadoJulgado`
# (not dataTransitoJulgado) and `fundamentacao` as an array of
# `CodigoDescricaoDTO` (not a `fundamentacaoLegal` string).
SCHEMA_SHAPED_RECORD = {
    "id": 555,
    "dataReferencia": "19/09/2026",
    "dataInicioSancao": "01/03/2025",
    "dataFimSancao": "01/03/2027",
    "dataPublicacaoSancao": "05/03/2025",
    "dataTransitadoJulgado": "20/02/2025",
    "dataOrigemInformacao": "06/03/2025",
    "tipoSancao": {
        "descricaoResumida": "Inidoneidade",
        "descricaoPortal": "Declaracao de inidoneidade para licitar",
    },
    "fonteSancao": {
        "nomeExibicao": "CGU",
        "telefoneContato": "0800 000 0000",
        "enderecoContato": "SAS Quadra 1, Brasilia DF",
    },
    "fundamentacao": [
        {"codigo": "8666", "descricao": "Art. 87, inciso IV, da Lei 8.666/93"}
    ],
    "orgaoSancionador": {
        "nome": "Ministerio da Saude",
        "siglaUf": "DF",
        "poder": "Executivo",
        "esfera": "Federal",
    },
    "sancionado": {"nome": "EMPRESA EXEMPLO LTDA", "codigoFormatado": "12.345.678/0001-99"},
    "pessoa": {
        "id": 777,
        "cpfFormatado": "",
        "cnpjFormatado": "12.345.678/0001-99",
        "numeroInscricaoSocial": "",
        "nome": "EMPRESA EXEMPLO LTDA",
        "razaoSocialReceita": "EMPRESA EXEMPLO LTDA",
        "nomeFantasiaReceita": "EXEMPLO",
        "tipo": "Pessoa Juridica",
    },
    "textoPublicacao": "Portaria 123 de 2025",
    "linkPublicacao": "https://www.in.gov.br/exemplo",
    "detalhamentoPublicacao": "DOU secao 1",
    "numeroProcesso": "12345.678901/2025-00",
    "abrangenciaDefinidaDecisaoJudicial": "Nao",
    "informacoesAdicionaisDoOrgaoSancionador": "",
}

# The same record plus one key the allow-list has never seen. If the live API
# really adds this key (or renames an existing one), this is exactly the kind
# of thing that must surface, never disappear silently.
UNKNOWN_KEY_RECORD = dict(
    SCHEMA_SHAPED_RECORD, situacaoRecursal="Em grau de recurso"
)


def _fake_answer(url, params):
    page = params["pagina"]
    if page > 1:
        return FakeResponse(200, [])
    if url == CEIS_URL:
        return FakeResponse(200, [UNKNOWN_KEY_RECORD])
    return FakeResponse(200, [])  # CNEP: nothing for this CNPJ


def test_an_unknown_key_in_the_api_payload_reaches_unmapped_api_keys():
    """The exact chain a real run does, minus the Apify SDK and the network."""
    transport = FakeTransport(_fake_answer)
    client = PortalClient(token="test-token", transport=transport, sleep=lambda s: None)

    raw = client.fetch_sanctions(CNPJ)
    assert raw["ceis"] == [UNKNOWN_KEY_RECORD], "the fake transport must be reached"

    result = build_result(CNPJ, raw)

    # This is what src/main.py does for every CNPJ in the batch:
    #   unmapped_keys.update(result["unmappedKeys"])
    #   ...
    #   summary["unmappedApiKeys"] = sorted(unmapped_keys)
    unmapped_keys: set[str] = set()
    unmapped_keys.update(result["unmappedKeys"])
    unmapped_api_keys = sorted(unmapped_keys)

    assert "situacaorecursal" in unmapped_api_keys, (
        "an API key the allow-list does not map must show up in "
        f"unmappedApiKeys, got: {unmapped_api_keys}"
    )
    # And the row itself must not silently drop the finding: the record is
    # still published, just without the unmapped field.
    assert result["verdict"] == "flagged"
    assert result["findingCount"] == 1
    assert "situacaoRecursal" not in str(result["findings"])
    assert "Em grau de recurso" not in str(result["findings"])

    # A schema-shaped record must fill the columns, not produce an empty row.
    # These are the three names that were wrong until 2026-09-20, checked here
    # end to end (transport -> PortalClient -> rules) and not only in the
    # allow-list.
    finding = result["findings"][0]
    assert finding["sanctionEndDate"] == "01/03/2027", finding
    assert finding["finalJudgmentDate"] == "20/02/2025", finding
    assert finding["legalBasis"] == "8666 - Art. 87, inciso IV, da Lei 8.666/93", finding
    # and no personal field survived the allow-list
    assert "EMPRESA EXEMPLO LTDA" == finding.get("companyName")
    assert "cpf" not in str(finding).lower()


def test_a_payload_that_matches_the_schema_reports_no_drift():
    """The negative case: a record exactly as the schema declares it.

    `unmappedApiKeys` is then made only of the keys we refuse on purpose
    (`DELIBERATELY_DROPPED`), which a real record always carries. Anything on
    top of that set means the API changed and the allow-list has to be read
    again, which is the whole point of keeping this field in the SUMMARY.
    """

    def answer(url, params):
        if params["pagina"] > 1:
            return FakeResponse(200, [])
        if url == CEIS_URL:
            return FakeResponse(200, [SCHEMA_SHAPED_RECORD])
        return FakeResponse(200, [])

    transport = FakeTransport(answer)
    client = PortalClient(token="test-token", transport=transport, sleep=lambda s: None)
    raw = client.fetch_sanctions(CNPJ)
    result = build_result(CNPJ, raw)

    unmapped_keys: set[str] = set()
    unmapped_keys.update(result["unmappedKeys"])

    drift = sorted(unmapped_keys.difference(REFUSED_ON_PURPOSE))
    assert drift == [], (
        "a record shaped exactly like the official schema produced key names "
        f"the allow-list neither maps nor refuses on purpose: {drift}"
    )


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
