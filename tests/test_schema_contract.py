"""The allow-list of src/rules.py, checked against the official OpenAPI schema.

Why this file exists
---------------------
Until 2026-09-20 every key name in `src/rules.py` had been guessed from the CSV
data dictionary of the registries, because the lab sandbox could not reach
`api.portaldatransparencia.gov.br`. The guesses looked right and the tests were
green, because the test fixtures had been written from the same guesses. Three
of them were wrong, and a wrong key name does not raise: it produces an empty
column in a compliance report, which the paying user discovers, not us.

The owner opened the host, the document was downloaded whole and saved at
`docs/portal-openapi-2026-09-20.json`, and this file turns it into a test. It
reads nothing from the network: it parses that saved file and asserts, in both
directions,

  * schema -> code: every key `CeisDTO` and `CnepDTO` declare is either mapped
    in `ALLOWED_PATHS` / `ALLOWED_LIST_PATHS` or refused on purpose in
    `DELIBERATELY_DROPPED`;
  * code -> schema: every key path in `ALLOWED_PATHS` exists in the schema, or
    is in the short list of known aliases below.

Refresh the saved document with `../../.venv/bin/python docs/fetch_openapi.py`.
If the API changed, this test fails first.

Run on its own:
    ../../.venv/bin/python tests/test_schema_contract.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.portal_client import (  # noqa: E402
    CEIS_PATH,
    CEPIM_PATH,
    CNEP_PATH,
    CNPJ_PARAM_BY_PATH,
    LENIENCIA_PATH,
    TOKEN_HEADER,
)
from src.rules import (  # noqa: E402
    ALLOWED_LIST_PATHS,
    ALLOWED_PATHS,
    DELIBERATELY_DROPPED,
    _normalize_key,
)

SCHEMA_FILE = ROOT / "docs" / "portal-openapi-2026-09-20.json"
DOC = json.loads(SCHEMA_FILE.read_text("utf-8"))
SCHEMAS = DOC["components"]["schemas"]

# The four registries the Actor reads, each with the DTO the official document
# declares for its 200 answer. CEPIM and acordos-leniencia were added on
# 2026-09-20; their DTOs reuse PessoaDTO and OrgaoDTO, which is why the
# allow-list of `src/rules.py` had to grow containers, not only columns.
ROUTES = (
    (CEIS_PATH, "CeisDTO"),
    (CNEP_PATH, "CnepDTO"),
    (CEPIM_PATH, "CepimDTO"),
    (LENIENCIA_PATH, "AcordosLenienciaDTO"),
)
DTOS = tuple(dto for _, dto in ROUTES)

# Key paths kept in ALLOWED_PATHS although the JSON schema does not declare
# them: they are the header names of the CSV export of the same registries and
# of older CGU documentation. They are harmless (a record never carries both)
# and they are listed here so that a real typo cannot hide among them.
KNOWN_ALIASES = {
    ("cadastro",),
    ("codigosancao",),
    ("razaosocial",),
    ("nomefantasia",),
    ("categoriasancao",),
    ("fundamentacaolegal",),
    ("publicacao",),
    ("detalhamentomeiopublicacao",),
    ("orgaosancionador",),
    ("uforgaosancionador",),
    ("esferaorgaosancionador",),
    ("origeminformacoes",),
    ("observacoes",),
}


def _ref_of(prop: dict) -> str:
    ref = prop.get("$ref") or (prop.get("items") or {}).get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else ""


def _schema_paths(root_name: str) -> set[tuple[str, ...]]:
    """Every key path of a DTO, normalized the way src/rules.py normalizes."""
    paths: set[tuple[str, ...]] = set()
    for key, prop in (SCHEMAS[root_name].get("properties") or {}).items():
        name = _normalize_key(key)
        ref = _ref_of(prop)
        if prop.get("type") == "array":
            paths.add((name, "[]"))
            for inner in (SCHEMAS.get(ref, {}).get("properties") or {}):
                paths.add((name, "[]", _normalize_key(inner)))
        elif ref:
            for inner in (SCHEMAS.get(ref, {}).get("properties") or {}):
                paths.add((name, _normalize_key(inner)))
        else:
            paths.add((name,))
    return paths


def _handled(path: tuple[str, ...]) -> bool:
    if path in ALLOWED_PATHS or path in DELIBERATELY_DROPPED:
        return True
    if len(path) >= 2 and path[1] == "[]":
        spec = ALLOWED_LIST_PATHS.get(path[0])
        if spec is None:
            return False
        return len(path) == 2 or path[2] in spec[1]
    return False


def test_the_saved_schema_is_the_one_we_think_it_is():
    assert DOC["openapi"].startswith("3."), DOC["openapi"]
    assert DOC["servers"][0]["url"] == "https://api.portaldatransparencia.gov.br"
    for path, dto in ROUTES:
        ok = DOC["paths"][path]["get"]["responses"]["200"]
        schema = ok["content"]["*/*"]["schema"]
        assert schema["type"] == "array", f"{path}: {schema}"
        assert schema["items"]["$ref"] == f"#/components/schemas/{dto}", schema


def test_the_token_header_name_matches_the_schema():
    """A wrong header name means HTTP 401 on every single call."""
    scheme = DOC["components"]["securitySchemes"]["Authorization"]
    assert scheme["in"] == "header", scheme
    assert scheme["name"] == TOKEN_HEADER, (
        f"the schema declares the token header {scheme['name']!r}, "
        f"src/portal_client.py sends {TOKEN_HEADER!r}"
    )


def test_the_query_parameters_we_send_exist():
    """Including the trap: the CNPJ parameter has a different name per route."""
    for path, _dto in ROUTES:
        names = {p["name"] for p in DOC["paths"][path]["get"]["parameters"]}
        expected = CNPJ_PARAM_BY_PATH[path]
        assert expected in names, (
            f"{path}: src/portal_client.py sends {expected!r}, the schema "
            f"declares {sorted(names)}"
        )
        assert "pagina" in names, (path, names)


def test_every_schema_key_is_either_mapped_or_refused_on_purpose():
    """schema -> code. This is the test that would have caught the three bugs."""
    missing: list[str] = []
    for dto in DTOS:
        for path in sorted(_schema_paths(dto)):
            if not _handled(path):
                missing.append(f"{dto}: {'.'.join(path)}")
    assert not missing, (
        "the official schema declares keys that src/rules.py neither maps nor "
        "refuses on purpose; map them in ALLOWED_PATHS or list them in "
        f"DELIBERATELY_DROPPED: {missing}"
    )


def test_every_mapped_key_exists_in_the_schema():
    """code -> schema. Catches a key name invented or mistyped in the allow-list."""
    declared: set[tuple[str, ...]] = set()
    for dto in DTOS:
        declared |= _schema_paths(dto)

    unknown = [
        ".".join(path)
        for path in sorted(ALLOWED_PATHS)
        if path not in declared and path not in KNOWN_ALIASES
    ]
    assert not unknown, (
        "these key paths are mapped in ALLOWED_PATHS but the official schema "
        f"does not declare them (a record will never carry them): {unknown}"
    )


def test_the_three_names_that_were_wrong_stay_fixed():
    """Regression guard, named after the actual bugs found on 2026-09-20."""
    # dataFinalSancao is a query PARAMETER; the response field is dataFimSancao.
    assert ("datafinalsancao",) not in ALLOWED_PATHS
    assert ALLOWED_PATHS[("datafimsancao",)] == "sanctionEndDate"
    # the response field is dataTransitadoJulgado, not dataTransitoJulgado
    assert ("datatransitojulgado",) not in ALLOWED_PATHS
    assert ALLOWED_PATHS[("datatransitadojulgado",)] == "finalJudgmentDate"
    # the legal basis is an ARRAY called fundamentacao, not a string
    assert ALLOWED_LIST_PATHS["fundamentacao"][0] == "legalBasis"


def test_no_personal_key_of_the_schema_is_mapped():
    """The LGPD line, stated against the real key names of the real schema."""
    for path in (
        ("pessoa", "cpfformatado"),
        ("pessoa", "nome"),
        ("pessoa", "numeroinscricaosocial"),
        ("sancionado", "nome"),
        ("sancionado", "codigoformatado"),
    ):
        assert path not in ALLOWED_PATHS, f"{'.'.join(path)} must never be published"
        assert path in DELIBERATELY_DROPPED, f"{'.'.join(path)} must be refused on purpose"


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
