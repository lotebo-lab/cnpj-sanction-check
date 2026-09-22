"""Offline tests for the allow-list and the verdict. No network, no Apify.

pytest is not installed in the lab sandbox and `pip install` has no network
there, so this file runs on its own:

    .venv/bin/python negocios/actor-idoneidade-cnpj/tests/test_rules.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rules import (  # noqa: E402
    ALLOWED_PATHS,
    build_error_result,
    build_result,
    company_identity,
    filter_record,
)

CNPJ = "12345678000199"
WHEN = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

# A CeisDTO-shaped record with personal data injected on purpose, at the root
# and inside nested objects. The registries really do carry these: the official
# CEIS data dictionary lists "CPF OU CNPJ DO SANCIONADO" and "NOME DO
# SANCIONADO", and the route documentation says the query works "por CNPJ ou
# CPF Sancionado".
#
# Every key below that is not marked as injected comes from `CeisDTO` in the
# official OpenAPI document saved at `docs/portal-openapi-2026-09-20.json`.
# Until 2026-09-20 these fixtures used guessed names (`dataFinalSancao`,
# `fundamentacaoLegal`, `tipoPessoa`), so they agreed with the allow-list and
# proved nothing about the real API. `tests/test_schema_contract.py` now holds
# the line against the schema itself.
RECORD_WITH_PERSONAL_DATA = {
    "id": 987654,
    "dataReferencia": "19/09/2026",
    "dataInicioSancao": "01/03/2025",
    "dataFimSancao": "01/03/2027",
    "dataPublicacaoSancao": "05/03/2025",
    "dataTransitadoJulgado": "10/04/2025",
    "numeroProcesso": "0001/2025",
    "textoPublicacao": "Portaria 123",
    "linkPublicacao": "https://www.in.gov.br/web/dou/-/portaria-123",
    "detalhamentoPublicacao": "DOU secao 1, pagina 42",
    "informacoesAdicionaisDoOrgaoSancionador": "Sancao mantida em segunda instancia",
    "fundamentacao": [
        {"codigo": "87-IV", "descricao": "Lei 8.666/93, art. 87, IV"},
        {"codigo": "7", "descricao": "Lei 10.520/02, art. 7"},
    ],
    "abrangenciaDefinidaDecisaoJudicial": "Todos os orgaos",
    "orgaoSancionador": {
        "nome": "Ministerio da Saude",
        "siglaUf": "DF",
        "poder": "Executivo",
        "esfera": "Federal",
    },
    "tipoSancao": {
        "descricaoResumida": "Inidoneidade",
        "descricaoPortal": "Declaracao de inidoneidade para licitar ou contratar",
    },
    "fonteSancao": {"nomeExibicao": "Portal da Transparencia"},
    "pessoa": {
        # company fields: these are the ones that must survive
        "tipo": "Pessoa Juridica",
        "cnpjFormatado": "12.345.678/0001-99",
        "razaoSocialReceita": "Silva Comercio Ltda",
        "nomeFantasiaReceita": "Silva Comercio",
        # personal data declared by PessoaDTO: must not survive
        "id": 4242,
        "nome": "Joao da Silva",
        "cpfFormatado": "***.456.789-**",
        "numeroInscricaoSocial": "12345678900",
    },
    "sancionado": {
        # SancionadoDTO: both keys can hold a person, both must be refused
        "nome": "Joao da Silva",
        "codigoFormatado": "123.456.789-00",
    },
    # injected keys the schema does not declare, to prove the allow-list drops
    # anything it has never seen instead of copying it
    "nomeSancionado": "Joao da Silva",
    "cpfCnpjSancionado": "123.456.789-00",
    "socios": [{"nome": "Maria Souza", "cpf": "98765432100"}],
    "responsavelLegal": "Carlos Pereira",
    "faixaEtaria": "41 a 50 anos",
}

PERSONAL_STRINGS = (
    "Joao da Silva",
    "Maria Souza",
    "Carlos Pereira",
    "123.456.789-00",
    "12345678900",
    "***.456.789-**",
    "41 a 50 anos",
)

CEIS_RECORD = {
    "id": 1,
    "dataInicioSancao": "01/03/2025",
    "orgaoSancionador": {"nome": "Ministerio da Saude", "siglaUf": "DF"},
    "tipoSancao": {"descricaoResumida": "Inidoneidade"},
}

CNEP_RECORD = {
    "id": 2,
    "dataInicioSancao": "10/06/2025",
    "valorMulta": 150000.0,
    "orgaoSancionador": {"nome": "CGU", "siglaUf": "DF"},
    "tipoSancao": {"descricaoResumida": "Multa - Lei 12.846/2013"},
}


# (a) personal data injected into the response never reaches the output
def test_personal_fields_are_dropped_by_the_allow_list():
    clean, unmapped = filter_record(RECORD_WITH_PERSONAL_DATA, "ceis")
    dumped = json.dumps(clean, ensure_ascii=False)
    for needle in PERSONAL_STRINGS:
        assert needle not in dumped, f"{needle!r} leaked into the finding: {dumped}"
    for banned in ("nomeSancionado", "cpfCnpjSancionado", "socios", "responsavelLegal"):
        assert banned not in dumped
    # and the company level fields we do want are there
    assert clean["sanctioningBody"] == "Ministerio da Saude"
    assert clean["sanctionCategory"] == "Inidoneidade"
    assert clean["sanctionStartDate"] == "01/03/2025"
    assert clean["companyName"] == "Silva Comercio Ltda"
    assert clean["sanctionedCnpj"] == "12.345.678/0001-99"
    # the dropped keys are reported by name, for the drift check
    assert "nomesancionado" in unmapped
    assert "socios[]" in unmapped
    # the refused keys of the real schema are reported too, by name only
    assert "pessoa.cpfformatado" in unmapped
    assert "sancionado.nome" in unmapped


def test_the_three_corrected_schema_fields_reach_the_output():
    """The fields that silently came back empty before 2026-09-20.

    `dataFimSancao` decides whether a sanction is still in force, which is the
    single thing a compliance buyer is looking for, and it was mapped as
    `dataFinalSancao`, a name that only exists as a query filter.
    """
    clean, _ = filter_record(RECORD_WITH_PERSONAL_DATA, "ceis")
    assert clean["sanctionEndDate"] == "01/03/2027"
    assert clean["finalJudgmentDate"] == "10/04/2025"
    # fundamentacao is an array of {codigo, descricao}, flattened into one cell
    assert clean["legalBasis"] == (
        "87-IV - Lei 8.666/93, art. 87, IV; 7 - Lei 10.520/02, art. 7"
    )


def test_the_other_schema_fields_reach_the_output():
    clean, _ = filter_record(RECORD_WITH_PERSONAL_DATA, "ceis")
    assert clean["sanctionCategoryDetail"].startswith("Declaracao de inidoneidade")
    assert clean["sanctioningBodyBranch"] == "Executivo"
    assert clean["sanctioningBodySphere"] == "Federal"
    assert clean["informationOrigin"] == "Portal da Transparencia"
    assert clean["publicationUrl"].startswith("https://www.in.gov.br/")
    assert clean["referenceDate"] == "19/09/2026"
    assert clean["notes"] == "Sancao mantida em segunda instancia"
    assert clean["tradeName"] == "Silva Comercio"


def test_a_cpf_without_a_declared_type_is_treated_as_an_individual():
    """Fallback for the case where the API leaves `pessoa.tipo` empty."""
    record = dict(CEIS_RECORD, pessoa={"cpfFormatado": "***.456.789-**"})
    result = build_result(CNPJ, {"ceis": [record], "cnep": []}, WHEN)
    assert result["personRecordsDiscarded"] == 1
    assert result["findings"] == []


def test_a_declared_company_is_never_dropped_for_carrying_a_cpf():
    """A wrongly dropped record is a false 'clear', the worst answer of all."""
    record = dict(
        CEIS_RECORD,
        pessoa={"tipo": "Pessoa Juridica", "cpfFormatado": "***.456.789-**"},
    )
    result = build_result(CNPJ, {"ceis": [record], "cnep": []}, WHEN)
    assert result["personRecordsDiscarded"] == 0
    assert result["verdict"] == "flagged"
    assert "456.789" not in json.dumps(result, ensure_ascii=False)


def test_personal_fields_are_dropped_from_the_whole_result():
    result = build_result(CNPJ, {"ceis": [RECORD_WITH_PERSONAL_DATA], "cnep": []}, WHEN)
    dumped = json.dumps(result, ensure_ascii=False)
    for needle in PERSONAL_STRINGS:
        assert needle not in dumped, f"{needle!r} leaked into the dataset row"
    assert result["verdict"] == "flagged"


def test_the_company_line_carries_the_legal_name_when_the_cnpj_matches():
    """The name only leaves a finding whose own identifier is the CNPJ asked."""
    result = build_result(CNPJ, {"ceis": [RECORD_WITH_PERSONAL_DATA], "cnep": []}, WHEN)
    assert result["companyName"] == "Silva Comercio Ltda"
    assert result["tradeName"] == "Silva Comercio"


def test_a_clear_company_line_has_no_name_to_show():
    result = build_result(CNPJ, {"ceis": [], "cnep": []}, WHEN)
    assert result["companyName"] is None
    assert result["tradeName"] is None


def test_an_eleven_digit_identifier_never_donates_a_name_to_the_line():
    """The digit count is the rule, and it stands alone.

    Even if every other guard were removed and a record about a natural person
    reached this function, an 11-digit identifier is a CPF and the name that
    travels with it is the name of a human being. It does not get published.
    """
    finding = {"sanctionedCnpj": "123.456.789-00", "companyName": "Joao da Silva"}
    assert company_identity("12345678900", [finding]) == {
        "companyName": None,
        "tradeName": None,
    }


def test_a_record_about_another_company_does_not_donate_its_name():
    finding = {"sanctionedCnpj": "99.999.999/0001-99", "companyName": "Outra Ltda"}
    identity = company_identity(CNPJ, [finding])
    assert identity["companyName"] is None


def test_unknown_container_is_not_walked_into():
    record = {"cadastro": "CEIS", "novoObjeto": {"nome": "Joao da Silva"}}
    clean, unmapped = filter_record(record, "ceis")
    assert "Joao da Silva" not in json.dumps(clean, ensure_ascii=False)
    assert "novoobjeto.*" in unmapped


def test_allow_list_has_no_bare_name_field():
    # A bare "nome" at the root of a record is a person's name in these
    # registries. It must never be allow-listed.
    assert ("nome",) not in ALLOWED_PATHS
    assert ("cpf",) not in ALLOWED_PATHS
    assert ("nomesancionado",) not in ALLOWED_PATHS


# (b) no record at all -> clear
def test_no_records_gives_clear_verdict():
    result = build_result(CNPJ, {"ceis": [], "cnep": []}, WHEN)
    assert result["verdict"] == "clear"
    assert result["findingCount"] == 0
    assert result["findingsBySource"] == {"ceis": 0, "cnep": 0}
    assert result["checkedAt"] == "2026-09-20T12:00:00+00:00"
    assert result["cnpj"] == CNPJ
    assert result["cnpjFormatted"] == "12.345.678/0001-99"
    assert "not a certificate of good standing" in result["justification"]
    assert [source["source"] for source in result["sourcesChecked"]] == ["ceis", "cnep"]
    for source in result["sourcesChecked"]:
        assert source["url"].startswith(
            "https://api.portaldatransparencia.gov.br/api-de-dados/"
        )
    assert "Creative Commons Attribution" in result["dataAttribution"]
    assert result["error"] is None


# (c) a record in each registry -> flagged, with both sources counted
def test_records_in_both_registries_give_flagged_verdict():
    result = build_result(CNPJ, {"ceis": [CEIS_RECORD], "cnep": [CNEP_RECORD]}, WHEN)
    assert result["verdict"] == "flagged"
    assert result["findingCount"] == 2
    assert result["findingsBySource"] == {"ceis": 1, "cnep": 1}
    assert [finding["source"] for finding in result["findings"]] == ["ceis", "cnep"]
    assert result["findings"][1]["fineAmount"] == 150000.0
    assert "CEIS: 1" in result["justification"]
    assert "CNEP: 1" in result["justification"]
    assert result["checkedAt"] == "2026-09-20T12:00:00+00:00"


def test_person_record_is_counted_and_never_published():
    # PessoaDTO.tipo is the key the official schema declares for this.
    person_record = dict(CEIS_RECORD, pessoa={"tipo": "Pessoa Fisica"})
    result = build_result(CNPJ, {"ceis": [person_record], "cnep": []}, WHEN)
    assert result["personRecordsDiscarded"] == 1
    assert result["findings"] == []
    assert result["verdict"] == "clear"


def test_company_record_keeps_person_type_when_juridica():
    company_record = dict(CEIS_RECORD, pessoa={"tipo": "Pessoa Juridica"})
    result = build_result(CNPJ, {"ceis": [company_record], "cnep": []}, WHEN)
    assert result["findings"][0]["personType"] == "Pessoa Juridica"
    assert result["verdict"] == "flagged"


def test_error_result_never_claims_a_verdict():
    result = build_error_result(CNPJ, "HTTP 500 after 4 attempts", WHEN)
    assert result["verdict"] == "error"
    assert result["findingCount"] == 0
    assert result["error"] == "HTTP 500 after 4 attempts"
    assert "was not checked" in result["justification"]


def test_findings_are_flat_scalars_only():
    clean, _ = filter_record(RECORD_WITH_PERSONAL_DATA, "ceis")
    for key, value in clean.items():
        assert isinstance(value, (str, int, float, bool)), f"{key} is {type(value)}"


def test_every_finding_carries_the_official_links():
    """A buyer has to be able to reopen the record at the source."""
    clean, _ = filter_record(RECORD_WITH_PERSONAL_DATA, "ceis")
    assert (
        clean["sourceUrl"]
        == "https://api.portaldatransparencia.gov.br/api-de-dados/ceis"
    )
    # /api-de-dados/ceis/{id} is the documented single record route.
    assert clean["recordUrl"] == (
        "https://api.portaldatransparencia.gov.br/api-de-dados/ceis/"
        f"{RECORD_WITH_PERSONAL_DATA['id']}"
    )
    cnep, _ = filter_record({"id": 42, "cadastro": "CNEP"}, "cnep")
    assert cnep["recordUrl"] == (
        "https://api.portaldatransparencia.gov.br/api-de-dados/cnep/42"
    )


def test_a_record_without_an_id_still_carries_the_registry_link():
    clean, _ = filter_record({"dataInicioSancao": "01/03/2025"}, "ceis")
    assert clean["sourceUrl"].endswith("/api-de-dados/ceis")
    assert "recordUrl" not in clean


# -- companyName from sancionado.nome, only for a proven company ------------
# CeisDTO-shaped records WITHOUT pessoa.razaoSocialReceita, so the only name
# available is sancionado.nome. Before 2026-09-20 the row came out null here.


def _sancionado_record(codigo, nome="EMPRESA EXEMPLO LTDA", **extra):
    record = {
        "id": 555,
        "dataInicioSancao": "01/03/2025",
        "orgaoSancionador": {"nome": "Ministerio da Saude"},
        "tipoSancao": {"descricaoResumida": "Inidoneidade"},
        "sancionado": {"nome": nome},
    }
    if codigo is not None:
        record["sancionado"]["codigoFormatado"] = codigo
    record.update(extra)
    return record


def test_company_record_fills_company_name_from_sancionado():
    row = build_result(
        CNPJ, {"ceis": [_sancionado_record("12.345.678/0001-99")]}, checked_at=WHEN
    )
    assert row["companyName"] == "EMPRESA EXEMPLO LTDA"
    assert row["findings"][0]["companyName"] == "EMPRESA EXEMPLO LTDA"
    assert row["findings"][0]["sanctionedCnpj"] == "12.345.678/0001-99"
    assert row["personRecordsDiscarded"] == 0


def test_person_record_with_cpf_never_fills_company_name():
    row = build_result(
        CNPJ,
        {"ceis": [_sancionado_record("123.456.789-00", nome="Joao da Silva")]},
        checked_at=WHEN,
    )
    assert row["companyName"] is None
    assert "Joao da Silva" not in json.dumps(row, ensure_ascii=False)
    # CNPJ in sancionado but a CPF elsewhere in the record: still no name
    mixed = _sancionado_record(
        "12.345.678/0001-99",
        nome="Joao da Silva",
        pessoa={"cpfFormatado": "***.456.789-**"},
    )
    row = build_result(CNPJ, {"ceis": [mixed]}, checked_at=WHEN)
    assert row["companyName"] is None
    assert "Joao da Silva" not in json.dumps(row, ensure_ascii=False)
    # declared as individual: still no name
    declared = _sancionado_record(
        "12.345.678/0001-99", nome="Joao da Silva", pessoa={"tipo": "Pessoa Fisica"}
    )
    row = build_result(CNPJ, {"ceis": [declared]}, checked_at=WHEN)
    assert "Joao da Silva" not in json.dumps(row, ensure_ascii=False)


def test_record_without_identifier_never_fills_company_name():
    for codigo in (None, "", "Sem informacao", "***.456.789-**"):
        row = build_result(
            CNPJ, {"ceis": [_sancionado_record(codigo)]}, checked_at=WHEN
        )
        assert row["companyName"] is None, codigo
        assert "EMPRESA EXEMPLO LTDA" not in json.dumps(row, ensure_ascii=False)


def test_sancionado_name_of_another_cnpj_is_not_used():
    row = build_result(
        CNPJ, {"ceis": [_sancionado_record("99.888.777/0001-66")]}, checked_at=WHEN
    )
    assert row["companyName"] is None
    assert "EMPRESA EXEMPLO LTDA" not in json.dumps(row, ensure_ascii=False)


def test_pessoa_nome_is_never_used_as_company_name():
    record = _sancionado_record(None)
    del record["sancionado"]
    record["pessoa"] = {"nome": "Joao da Silva", "cnpjFormatado": "12.345.678/0001-99"}
    row = build_result(CNPJ, {"ceis": [record]}, checked_at=WHEN)
    assert row["companyName"] is None
    assert "Joao da Silva" not in json.dumps(row, ensure_ascii=False)


def test_receita_name_wins_over_sancionado_name():
    record = _sancionado_record(
        "12.345.678/0001-99",
        pessoa={"cnpjFormatado": "12.345.678/0001-99", "razaoSocialReceita": "Receita Ltda"},
    )
    row = build_result(CNPJ, {"ceis": [record]}, checked_at=WHEN)
    assert row["companyName"] == "Receita Ltda"


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
