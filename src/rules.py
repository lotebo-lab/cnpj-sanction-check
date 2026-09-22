"""Allow-list of output fields and the verdict for one CNPJ.

Two jobs, and the order matters:

1. **Filter.** A raw CEIS/CNEP record is copied field by field into the output
   through an allow-list of key paths. A key that is not on the list is
   dropped, whatever it holds. This is an allow-list on purpose, never a
   block-list: when the API adds a field we have never seen, the safe default
   has to be "drop it", not "publish it".

   Why it matters: the registries do cover sanctioned **individuals**. The
   official CEIS data dictionary
   (https://www.portaldatransparencia.gov.br/pagina-interna/603412-dicionario-de-dados-sancoes-ceis,
   read on 2026-09-20) lists "TIPO DE PESSOA - Identifica se a penalidade foi
   aplicada a 'pessoa fisica' ou 'pessoa juridica'", "CPF OU CNPJ DO
   SANCIONADO" and "NOME DO SANCIONADO". The OpenAPI document of both routes
   describes the query as "registros do CEIS por CNPJ ou CPF Sancionado". So
   personal data is reachable, and it must never reach the dataset: no name of
   a person, no CPF, no partner name, no legal representative name.

2. **Judge.** Whoever buys a compliance check pays for a verdict they can put
   in a file: what the answer is, why, from which sources, at what time. So
   every row carries verdict, counts per source, the sources with their
   official URL, the timestamp and a one sentence justification.

KEY NAMES, CONFIRMED ON 2026-09-20 AGAINST THE OFFICIAL SCHEMA. The OpenAPI
document of the API was downloaded whole (HTTP 200, 167630 bytes) from
https://api.portaldatransparencia.gov.br/v3/api-docs and saved in this repo at
`docs/portal-openapi-2026-09-20.json`. The 200 response of both routes is
`{"type": "array", "items": {"$ref": "#/components/schemas/CeisDTO"}}` (CnepDTO
for /cnep), so every key path below is checked against `CeisDTO` / `CnepDTO`
and the objects they reference. Three key names that had been guessed from the
CSV data dictionary were wrong and are fixed: `dataFinalSancao` is only a query
parameter and the response field is `dataFimSancao`; the response field is
`dataTransitadoJulgado`, not `dataTransitoJulgado`; and the legal basis is
`fundamentacao`, an array of `CodigoDescricaoDTO`, not a `fundamentacaoLegal`
string.

`tests/test_schema_contract.py` reads that saved document and fails if any key
declared by the schema is neither mapped here nor listed in
`DELIBERATELY_DROPPED`, so the next schema change breaks a test instead of
quietly emptying a column. `unmappedKeys` (per row) and `unmappedApiKeys` (per
run, in the SUMMARY) stay in place anyway: the saved schema is a snapshot, and
the live API is free to drift from it between two of our releases. The failure
mode of a wrong key is a missing column in the output, never a leaked personal
field, because anything unknown is dropped.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

SOURCES = {
    "ceis": {
        "source": "ceis",
        "label": "CEIS",
        "registry": "CEIS - Cadastro de Empresas Inidoneas e Suspensas",
        "publisher": "Controladoria-Geral da Uniao (CGU) - Portal da Transparencia",
        "url": "https://api.portaldatransparencia.gov.br/api-de-dados/ceis",
    },
    "cnep": {
        "source": "cnep",
        "label": "CNEP",
        "registry": "CNEP - Cadastro Nacional de Empresas Punidas",
        "publisher": "Controladoria-Geral da Uniao (CGU) - Portal da Transparencia",
        "url": "https://api.portaldatransparencia.gov.br/api-de-dados/cnep",
    },
    "cepim": {
        "source": "cepim",
        "label": "CEPIM",
        "registry": (
            "CEPIM - Cadastro de Entidades Privadas Sem Fins Lucrativos Impedidas"
        ),
        "publisher": "Controladoria-Geral da Uniao (CGU) - Portal da Transparencia",
        "url": "https://api.portaldatransparencia.gov.br/api-de-dados/cepim",
    },
    "leniencia": {
        "source": "leniencia",
        "label": "Acordos de Leniencia",
        "registry": "Acordos de Leniencia (Lei 12.846/2013)",
        "publisher": "Controladoria-Geral da Uniao (CGU) - Portal da Transparencia",
        "url": (
            "https://api.portaldatransparencia.gov.br/api-de-dados/acordos-leniencia"
        ),
    },
}

SOURCE_ORDER = ("ceis", "cnep", "cepim", "leniencia")

DATA_ATTRIBUTION = (
    "Source: Portal da Transparencia, Controladoria-Geral da Uniao (CGU), Brazil. "
    "Open data under Creative Commons Attribution, Decreto 8.777/2016. "
    "Data reformatted by this Actor; not an official document."
)


def _normalize_key(key: Any) -> str:
    """'DATA INICIO SANCAO', 'dataInicioSancao' and 'data_inicio_sancao' -> 'datainiciosancao'."""
    text = unicodedata.normalize("NFKD", str(key))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _digits(value: Any) -> str:
    """'42.041.852/0001-77' -> '42041852000177'. The only arithmetic we trust.

    A CNPJ has 14 digits and a CPF has 11. That difference is the one fact in
    this API that cannot be reworded by an upstream change of vocabulary, and
    both the person filter and the company name check below are built on it.
    """
    return "".join(ch for ch in str(value or "") if ch.isdigit())


CNPJ_DIGITS = 14
CPF_DIGITS = 11

# Keys of the raw record that carry the identifier of the sanctioned party.
# They are read to decide whether the party is a human being; their values are
# never copied into the output (all three are in DELIBERATELY_DROPPED, except
# `cnpjformatado`, which is published as `sanctionedCnpj`).
PARTY_IDENTIFIER_KEYS = frozenset({"cpfformatado", "cpf", "codigoformatado"})

# The subset of the keys above that can only ever hold a CPF. `codigoformatado`
# is out on purpose: the schema calls it "CPF ou CNPJ do sancionado".
CPF_ONLY_KEYS = frozenset({"cpfformatado", "cpf", "numeroinscricaosocial"})


# --------------------------------------------------------------------------
# The allow-list.
#
# Each entry is a path of normalized keys, from the root of one record, mapped
# to the name that goes into the dataset. Depth 1 covers a flat record; depth 2
# covers the nested objects the API is likely to use. Several paths may map to
# the same output name (the first one found wins), because the flat CSV export
# and the JSON API do not use identical names.
#
# Nothing that can hold a person's name or document is here. In particular:
# "nome" alone is NOT allowed at the root, and is only allowed inside a
# container that can only describe a public body ("orgaosancionador") or a
# sanction type ("tiposancao").
# --------------------------------------------------------------------------
ALLOWED_PATHS: dict[tuple[str, ...], str] = {
    # -- confirmed against CeisDTO / CnepDTO on 2026-09-20 ------------------
    # identifiers of the sanction itself (never of a person)
    ("id",): "sanctionCode",  # CeisDTO.id / CnepDTO.id (integer)
    ("numeroprocesso",): "processNumber",  # .numeroProcesso
    # the sanctioned party: only the keys that can hold a company, never a
    # person. PessoaDTO also declares `nome`, `cpfFormatado` and
    # `numeroInscricaoSocial`; those are in DELIBERATELY_DROPPED.
    ("pessoa", "tipo"): "personType",  # PessoaDTO.tipo
    ("pessoa", "cnpjformatado"): "sanctionedCnpj",  # PessoaDTO.cnpjFormatado
    ("pessoa", "razaosocialreceita"): "companyName",  # PessoaDTO.razaoSocialReceita
    ("pessoa", "nomefantasiareceita"): "tradeName",  # PessoaDTO.nomeFantasiaReceita
    # what the sanction is
    ("tiposancao", "descricaoresumida"): "sanctionCategory",  # TipoSancaoDTO
    ("tiposancao", "descricaoportal"): "sanctionCategoryDetail",  # TipoSancaoDTO
    ("abrangenciadefinidadecisaojudicial",): "sanctionScope",
    ("valormulta",): "fineAmount",  # CnepDTO only
    # where the sanction came from and where it was published
    ("fontesancao", "nomeexibicao"): "informationOrigin",  # FonteSancaoDTO
    ("textopublicacao",): "publicationText",
    ("linkpublicacao",): "publicationUrl",
    ("detalhamentopublicacao",): "publicationDetails",
    ("informacoesadicionaisdoorgaosancionador",): "notes",
    # dates, as strings, exactly as the API returns them (DD/MM/AAAA)
    ("datainiciosancao",): "sanctionStartDate",
    ("datafimsancao",): "sanctionEndDate",  # NOT dataFinalSancao: that is a filter
    ("datapublicacaosancao",): "publicationDate",
    ("datatransitadojulgado",): "finalJudgmentDate",  # dataTransitadoJulgado
    ("dataorigeminformacao",): "informationOriginDate",
    ("datareferencia",): "referenceDate",
    # who applied it
    ("orgaosancionador", "nome"): "sanctioningBody",  # OrgaoSancionadorDTO.nome
    ("orgaosancionador", "siglauf"): "sanctioningBodyState",
    ("orgaosancionador", "poder"): "sanctioningBodyBranch",
    ("orgaosancionador", "esfera"): "sanctioningBodySphere",
    # -- CEPIM, confirmed against CepimDTO on 2026-09-20 --------------------
    # CepimDTO = {id, dataReferencia, motivo, orgaoSuperior: OrgaoDTO,
    #             pessoaJuridica: PessoaDTO, convenio: DimConvenioDTO}.
    # The sanctioned party sits under `pessoaJuridica`, NOT under `pessoa`, and
    # the body is `orgaoSuperior`, NOT `orgaoSancionador`. Same PessoaDTO, so
    # the same personal keys are refused below.
    ("motivo",): "impedimentReason",
    ("pessoajuridica", "tipo"): "personType",
    ("pessoajuridica", "cnpjformatado"): "sanctionedCnpj",
    ("pessoajuridica", "razaosocialreceita"): "companyName",
    ("pessoajuridica", "nomefantasiareceita"): "tradeName",
    ("orgaosuperior", "nome"): "sanctioningBody",
    ("orgaosuperior", "sigla"): "sanctioningBodyAcronym",
    ("orgaosuperior", "descricaopoder"): "sanctioningBodyBranch",
    # the federal transfer agreement the entity is barred from
    ("convenio", "codigo"): "agreementCode",
    ("convenio", "numero"): "agreementNumber",
    ("convenio", "objeto"): "agreementSubject",
    # -- Acordos de Leniencia, confirmed against AcordosLenienciaDTO --------
    # AcordosLenienciaDTO = {id, dataInicioAcordo, dataFimAcordo,
    #                        orgaoResponsavel: string, situacaoAcordo,
    #                        sancoes: [EmpresaSancionadaDTO], quantidade}.
    # `orgaoResponsavel` is a plain string here, not an object.
    ("datainicioacordo",): "sanctionStartDate",
    ("datafimacordo",): "sanctionEndDate",
    ("orgaoresponsavel",): "sanctioningBody",
    ("situacaoacordo",): "agreementStatus",
    # -- defensive aliases, not in the JSON schema --------------------------
    # Names used by the CSV export of the same registries and by older
    # documentation. They can never collide with a confirmed key above, so
    # they cost nothing and they keep the Actor useful if the API is ever
    # aligned with the CSV headers.
    ("cadastro",): "sanctionRegistry",
    ("codigosancao",): "sanctionCode",
    ("razaosocial",): "companyName",
    ("nomefantasia",): "tradeName",
    ("categoriasancao",): "sanctionCategory",
    ("fundamentacaolegal",): "legalBasis",
    ("publicacao",): "publicationVehicle",
    ("detalhamentomeiopublicacao",): "publicationDetails",
    ("orgaosancionador",): "sanctioningBody",  # when it comes as a plain string
    ("uforgaosancionador",): "sanctioningBodyState",
    ("esferaorgaosancionador",): "sanctioningBodySphere",
    ("origeminformacoes",): "informationOrigin",
    ("observacoes",): "notes",
}

# Arrays of objects the API returns. Only the inner keys named here are read,
# and the result is flattened into one string, because a finding is a flat row.
#
# Each entry is (column name for the flattened string, inner keys allowed,
# promotion map). The promotion map copies an inner key into a column of its
# own. A leniency agreement can cover several companies at once, and taking the
# first name of a list of four would put another company's name in the row of
# the CNPJ that was asked about, so the object is chosen by `_object_for_cnpj`:
# the only one there is, or the one whose CNPJ matches the query digit for
# digit. Neither, and no column is filled.
#
#   CeisDTO.fundamentacao / CnepDTO.fundamentacao -> CodigoDescricaoDTO
#   AcordosLenienciaDTO.sancoes -> EmpresaSancionadaDTO
ALLOWED_LIST_PATHS: dict[str, tuple[str, tuple[str, ...], dict[str, str]]] = {
    "fundamentacao": ("legalBasis", ("codigo", "descricao"), {}),
    "sancoes": (
        "sanctionedParties",
        ("razaosocial", "nomefantasia", "cnpjformatado"),
        {
            "razaosocial": "companyName",
            "nomefantasia": "tradeName",
            "cnpjformatado": "sanctionedCnpj",
        },
    ),
}

# Keys the official schema declares and that we refuse on purpose. The schema
# contract test reads this list, so dropping a field is always a written
# decision and never an oversight.
DELIBERATELY_DROPPED: dict[tuple[str, ...], str] = {
    ("pessoa", "id"): "internal numeric id of the party, useless to a buyer",
    ("pessoa", "cpfformatado"): "CPF of an individual (LGPD)",
    ("pessoa", "nome"): "may be the full name of an individual (LGPD)",
    ("pessoa", "numeroinscricaosocial"): "social registration number of an individual (LGPD)",
    ("sancionado", "nome"): (
        "may be the full name of an individual (LGPD); used as companyName only "
        "when codigoFormatado is the queried 14-digit CNPJ and no CPF is in the "
        "record (_company_name_from_sancionado)"
    ),
    ("sancionado", "codigoformatado"): "holds a CPF when the party is an individual (LGPD)",
    ("fontesancao", "telefonecontato"): "contact phone of the public body, not needed",
    ("fontesancao", "enderecocontato"): "contact address of the public body, not needed",
    # -- CEPIM (CepimDTO) ---------------------------------------------------
    # Same PessoaDTO as CEIS/CNEP, under another container name.
    ("pessoajuridica", "id"): "internal numeric id of the party, useless to a buyer",
    ("pessoajuridica", "cpfformatado"): "CPF of an individual (LGPD)",
    ("pessoajuridica", "nome"): "may be the full name of an individual (LGPD)",
    ("pessoajuridica", "numeroinscricaosocial"): (
        "social registration number of an individual (LGPD)"
    ),
    ("orgaosuperior", "cnpj"): (
        "CNPJ of the public body, not of the company being checked; publishing it "
        "in the same row would invite reading it as the company's own number"
    ),
    ("orgaosuperior", "codigosiafi"): "internal SIAFI code of the public body",
    ("orgaosuperior", "orgaomaximo"): (
        "nested object (OrgaoMaximoDTO); the body that applied the measure is "
        "already published as sanctioningBody"
    ),
    # -- Acordos de Leniencia (AcordosLenienciaDTO) -------------------------
    ("quantidade",): (
        "count the API echoes for the query, not a property of the company"
    ),
    ("sancoes", "[]", "cnpj"): (
        "unformatted duplicate of cnpjFormatado, which is published"
    ),
    ("sancoes", "[]", "nomeinformadoorgaoresponsavel"): (
        "free text name typed by the responsible body; it can hold the name of an "
        "individual (LGPD) and the company name is already in razaoSocial"
    ),
}

def _dropped_key_name(path: tuple[str, ...]) -> str:
    """('sancoes', '[]', 'cnpj') -> 'sancoes[].cnpj', the form `filter_record` reports."""
    name = ""
    for part in path:
        if part == "[]":
            name += "[]"
        elif name:
            name += f".{part}"
        else:
            name = part
    return name


# The 18 key names the live API sent on 2026-09-20 and that we refuse are all
# in DELIBERATELY_DROPPED, so `unmappedKeys` reports the same 18 on every run.
# A key we have never seen would arrive as the 19th and be read by nobody. This
# set is what tells the two apart: `unmappedKeys` keeps listing everything we do
# not publish (the owner asked for that net and it stays), and
# `unexpected_keys()` below reduces it to what is actually news.
DROPPED_KEY_NAMES = frozenset(_dropped_key_name(path) for path in DELIBERATELY_DROPPED)


def unexpected_keys(names: Iterable[str]) -> list[str]:
    """The reported key names that are NOT a written decision of this file."""
    return sorted({str(name) for name in names or []} - DROPPED_KEY_NAMES)


# Containers we are willing to walk into. Anything else stays unopened, so a
# nested object we have never seen cannot contribute a single field. Containers
# that only hold refused keys are listed too, so their contents are reported as
# unmapped instead of the whole object being reported as one opaque name.
ALLOWED_CONTAINERS = frozenset(
    [path[0] for path in ALLOWED_PATHS if len(path) > 1]
    + [path[0] for path in DELIBERATELY_DROPPED if len(path) > 1]
)

# Order of the columns in the output, for a readable table.
FIELD_ORDER = (
    "source",
    "sanctionRegistry",
    "sourceUrl",
    "recordUrl",
    "sanctionCode",
    "sanctionedCnpj",
    "companyName",
    "tradeName",
    "sanctionedParties",
    "personType",
    "sanctionCategory",
    "sanctionCategoryDetail",
    "impedimentReason",
    "agreementStatus",
    "sanctionScope",
    "sanctionStartDate",
    "sanctionEndDate",
    "finalJudgmentDate",
    "sanctioningBody",
    "sanctioningBodyAcronym",
    "sanctioningBodyState",
    "sanctioningBodyBranch",
    "sanctioningBodySphere",
    "processNumber",
    "legalBasis",
    "fineAmount",
    "agreementCode",
    "agreementNumber",
    "agreementSubject",
    "publicationVehicle",
    "publicationDate",
    "publicationDetails",
    "publicationText",
    "publicationUrl",
    "informationOrigin",
    "informationOriginDate",
    "referenceDate",
    "notes",
)


def _object_for_cnpj(objects: list[dict], wanted: str) -> dict | None:
    """The one object of an array that describes the CNPJ that was asked about.

    A leniency agreement covers several companies at once, and the row belongs
    to one of them. With a single object there is nothing to confuse; with
    several, the object is chosen by comparing the 14 digits of its CNPJ with
    the 14 digits of the query, and no object matching means no columns.
    """
    if not objects:
        return None
    if len(objects) == 1:
        return objects[0]
    if not wanted:
        return None
    for item in objects:
        for inner_key, inner_value in item.items():
            if _normalize_key(inner_key) not in {"cnpj", "cnpjformatado"}:
                continue
            if _digits(_scalar(inner_value)) == wanted:
                return item
    return None


def _scalar(value: Any) -> Any:
    """Only scalars are published. A dict or a list is never copied as is."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def filter_record(
    record: dict, source: str, queried_cnpj: Any = None
) -> tuple[dict, list[str]]:
    """Copy one raw record through the allow-list.

    Returns the clean finding and the list of key paths that were dropped, so
    the run summary can show what the API sent that we do not map. Only key
    NAMES are reported, never values.

    `queried_cnpj` is the number the buyer asked about. It is used for one
    thing only: picking, out of an array that covers several companies, the one
    object whose CNPJ is the CNPJ that was asked about. See
    `ALLOWED_LIST_PATHS`.
    """
    clean: dict[str, Any] = {"source": source}
    unmapped: list[str] = []
    if not isinstance(record, dict):
        return clean, unmapped
    wanted = _digits(queried_cnpj)
    if len(wanted) != CNPJ_DIGITS:
        wanted = ""

    for raw_key, raw_value in record.items():
        key = _normalize_key(raw_key)
        if isinstance(raw_value, list):
            spec = ALLOWED_LIST_PATHS.get(key)
            if spec is None:
                unmapped.append(key + "[]")
                continue
            name, inner_allowed, promote = spec
            objects = [item for item in raw_value if isinstance(item, dict)]
            parts: list[str] = []
            for item in objects:
                bits: list[str] = []
                for inner_key, inner_value in item.items():
                    inner = _normalize_key(inner_key)
                    if inner not in inner_allowed:
                        unmapped.append(f"{key}[].{inner}")
                        continue
                    scalar = _scalar(inner_value)
                    if scalar is not None and scalar != "":
                        bits.append(str(scalar))
                if bits:
                    parts.append(" - ".join(bits))
            if parts and name not in clean:
                clean[name] = "; ".join(parts)
            # A single object in the array is unambiguous, so its fields can
            # become columns of their own. With two or more, the only object
            # that may fill the columns is the one whose CNPJ is the CNPJ the
            # buyer asked about: see the note on ALLOWED_LIST_PATHS. No match,
            # no columns, and the names stay in the flattened string only.
            target = _object_for_cnpj(objects, wanted) if promote else None
            if target is not None:
                for inner_key, inner_value in target.items():
                    column = promote.get(_normalize_key(inner_key))
                    if column is None or column in clean:
                        continue
                    scalar = _scalar(inner_value)
                    if scalar is not None and scalar != "":
                        clean[column] = scalar
            continue
        if isinstance(raw_value, dict):
            if key in ALLOWED_CONTAINERS:
                for inner_key, inner_value in raw_value.items():
                    path = (key, _normalize_key(inner_key))
                    name = ALLOWED_PATHS.get(path)
                    if name is None:
                        unmapped.append(".".join(path))
                        continue
                    if name not in clean:
                        value = _scalar(inner_value)
                        if value is not None and value != "":
                            clean[name] = value
            else:
                unmapped.append(key + ".*")
            continue

        name = ALLOWED_PATHS.get((key,))
        if name is None:
            unmapped.append(key)
            continue
        value = _scalar(raw_value)
        if value is None or value == "":
            continue
        if name not in clean:
            clean[name] = value

    # The official link for the record, so a finding can be reopened at the
    # source instead of being taken on our word. `sourceUrl` is the registry
    # route; `recordUrl` is the single record route `/api-de-dados/ceis/{id}`
    # and `/api-de-dados/cnep/{id}`, both documented in the Portal's OpenAPI
    # document (https://api.portaldatransparencia.gov.br/v3/api-docs). Opening
    # them needs the same free token, like every route of this API.
    registry = SOURCES.get(source)
    if registry:
        # The registry name, so a finding says which of the four lists it came
        # from without the reader having to decode the route. The live API does
        # not return a `cadastro` field (that is a column of the CSV export), so
        # without this line the column would be empty in every real run.
        clean.setdefault("sanctionRegistry", registry["label"])
        clean["sourceUrl"] = registry["url"]
        code = clean.get("sanctionCode")
        if code not in (None, ""):
            clean["recordUrl"] = f"{registry['url']}/{code}"

    ordered = {name: clean[name] for name in FIELD_ORDER if name in clean}
    return ordered, unmapped


def _declared_party_type(clean: dict) -> str | None:
    """'person', 'company' or None, read from the type the registry declares.

    None means the registry said nothing, and then the digit rules below decide.
    """
    if _is_person_record(clean):
        return "person"
    person_type = _normalize_key(clean.get("personType", ""))
    if not person_type:
        return None
    if "juridica" in person_type or person_type in {"j", "pj"}:
        return "company"
    # Anything else is prose we do not recognize ("Entidades Empresariais
    # Privadas"), and prose is not a decision: fall back to the digits.
    return None


def _cpf_only_field_is_filled(record: Any) -> bool:
    """True when a key that can ONLY hold a CPF carries something.

    `cpfFormatado` is not "CPF or CNPJ": the schema declares it as the CPF of
    the party. The live API masks it ("***.456.789-**"), so the digit count of
    `_party_identifier_is_cpf` never reaches 11 and would let the record
    through. A filled CPF-only field means the sanctioned party is a human
    being, whatever the number of digits that survived the mask.

    A value with 14 digits is a CNPJ written into the wrong field, and a value
    with no digit at all is the "Sem informacao" filler; neither is a CPF.
    """
    if not isinstance(record, dict):
        return False
    containers = [record] + [v for v in record.values() if isinstance(v, dict)]
    for container in containers:
        for raw_key, raw_value in container.items():
            if _normalize_key(raw_key) not in CPF_ONLY_KEYS:
                continue
            digits = _digits(_scalar(raw_value))
            if digits and len(digits) != CNPJ_DIGITS:
                return True
    return False


def _is_person_record(clean: dict) -> bool:
    """True when the declared party type says individual, not company.

    `PessoaDTO.tipo` is a free string and the live API does NOT fill it with
    "pessoa juridica" / "pessoa fisica": the run of 2026-09-20 came back with
    "Entidades Empresariais Privadas", which is the Receita Federal grouping of
    the legal nature. That grouping has a bucket for individuals, written in the
    plural ("Pessoas Fisicas"), and an exact match on "pessoafisica" would walk
    straight past it. So the test is a substring, and it is only half of the
    defence: `_party_identifier_is_cpf` below decides by digit count, which no
    change of vocabulary upstream can defeat.
    """
    person_type = _normalize_key(clean.get("personType", ""))
    if not person_type:
        return False
    return "fisica" in person_type or person_type in {"f", "pf"}


def _party_identifier_is_cpf(record: Any) -> bool:
    """True when the identifier of the sanctioned party is a CPF (11 digits).

    Read from the raw record, never from the filtered one, because every key
    that can hold a CPF is dropped before the filtered row exists:
    `pessoa.cpfFormatado`, `pessoaJuridica.cpfFormatado` and
    `sancionado.codigoFormatado`, which the official schema declares as "CPF ou
    CNPJ do sancionado". Reading a value in order to THROW THE RECORD AWAY is
    not publishing it: nothing found here ever reaches the dataset.

    The test is the digit count, not emptiness. The registries write "Sem
    informacao" into fields they do not have, and a non-empty test would read
    that as a CPF and drop a real company record, which is a false "clear" and
    the worst answer a compliance check can give. 11 digits is a CPF, 14 is a
    CNPJ, and "Sem informacao" is neither.
    """
    if not isinstance(record, dict):
        return False
    for raw_key, raw_value in record.items():
        if _normalize_key(raw_key) in PARTY_IDENTIFIER_KEYS:
            if len(_digits(_scalar(raw_value))) == CPF_DIGITS:
                return True
        elif isinstance(raw_value, dict):
            for inner_key, inner_value in raw_value.items():
                if _normalize_key(inner_key) in PARTY_IDENTIFIER_KEYS:
                    if len(_digits(_scalar(inner_value))) == CPF_DIGITS:
                        return True
    return False


def _company_name_from_sancionado(record: Any, clean: dict, queried_cnpj: Any) -> str | None:
    """`sancionado.nome`, but only when the record is PROVABLY about a company.

    `sancionado.nome` is "NOME DO SANCIONADO" in the CEIS data dictionary and
    holds the name of a human being when the party is an individual, so it is
    in DELIBERATELY_DROPPED and never copied by the allow-list. This is the one
    narrow exception, and every condition below has to hold, or nothing:

    1. `sancionado.codigoFormatado` has exactly 14 digits (a CNPJ, never the 11
       of a CPF) and they are the 14 digits that were asked about;
    2. no field of the record holds a CPF (`_party_identifier_is_cpf` and
       `_cpf_only_field_is_filled`), and the declared party type does not say
       individual;
    3. if the record also names a CNPJ elsewhere (`sanctionedCnpj`), it is the
       same one.

    Missing identifier, masked identifier, anything in doubt: None, as before.
    `pessoa.nome` is never read here.
    """
    if not isinstance(record, dict):
        return None
    wanted = _digits(queried_cnpj)
    if len(wanted) != CNPJ_DIGITS:
        return None
    party = None
    for raw_key, raw_value in record.items():
        if _normalize_key(raw_key) == "sancionado" and isinstance(raw_value, dict):
            party = raw_value
            break
    if party is None:
        return None
    name = None
    code = ""
    for raw_key, raw_value in party.items():
        key = _normalize_key(raw_key)
        if key == "nome":
            name = _scalar(raw_value)
        elif key == "codigoformatado":
            code = _digits(_scalar(raw_value))
    if len(code) != CNPJ_DIGITS or code != wanted:
        return None
    if _declared_party_type(clean) == "person":
        return None
    if _party_identifier_is_cpf(record) or _cpf_only_field_is_filled(record):
        return None
    published_id = _digits(clean.get("sanctionedCnpj"))
    if published_id and published_id != code:
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def filter_records(
    records: Iterable[dict], source: str, queried_cnpj: Any = None
) -> tuple[list[dict], list[str], int]:
    """Allow-list a whole page of records.

    Returns (findings, unmapped key names, number of records dropped for being
    about an individual).
    """
    findings: list[dict] = []
    unmapped: list[str] = []
    dropped_person_records = 0
    for record in records or []:
        clean, record_unmapped = filter_record(record, source, queried_cnpj)
        unmapped.extend(record_unmapped)
        # What the registry CALLS the party decides first, because it is the
        # only statement the registry makes on purpose. When it says nothing we
        # count digits instead. A declared company still has to survive one
        # digit test: the identifier we are about to PUBLISH as `sanctionedCnpj`
        # must be a CNPJ (14 digits) or absent, never an 11-digit CPF.
        declared = _declared_party_type(clean)
        if declared == "company":
            published_id = _digits(clean.get("sanctionedCnpj"))
            is_person = bool(published_id) and len(published_id) != CNPJ_DIGITS
        else:
            is_person = (
                declared == "person"
                or _party_identifier_is_cpf(record)
                or _cpf_only_field_is_filled(record)
            )
        if is_person:
            # We query by CNPJ, so this should not happen. If it does, the row
            # is about a human being and it is not published.
            dropped_person_records += 1
            continue
        if not clean.get("companyName"):
            # The Receita name (`razaoSocialReceita`) is preferred; when the
            # registry did not send it, the sanctioned party's own name is used
            # only for a record proven to be about the queried company.
            name = _company_name_from_sancionado(record, clean, queried_cnpj)
            if name:
                clean["companyName"] = name
                clean.setdefault("sanctionedCnpj", _format_cnpj(_digits(queried_cnpj)))
                clean = {key: clean[key] for key in FIELD_ORDER if key in clean}
        findings.append(clean)
    return findings, sorted(set(unmapped)), dropped_person_records


def _now_iso(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _format_cnpj(cnpj: str) -> str:
    digits = "".join(ch for ch in str(cnpj) if ch.isdigit())
    if len(digits) != 14:
        return str(cnpj)
    return f"{digits[0:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:14]}"


def _sources_checked(sources: Iterable[str]) -> list[dict]:
    return [dict(SOURCES[name]) for name in sources if name in SOURCES]


def _label(source: str) -> str:
    """'leniencia' -> 'Acordos de Leniencia'. The name the buyer recognizes."""
    return (SOURCES.get(source) or {}).get("label") or source.upper()


def _registry_list(sources: Iterable[str]) -> str:
    """'CEIS, CNEP, CEPIM and Acordos de Leniencia', for the justification line."""
    labels = [_label(name) for name in sources]
    if not labels:
        return "any registry"
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def company_identity(cnpj: Any, findings: Iterable[dict]) -> dict:
    """The legal name of the company the row is about, or nothing.

    A buyer who pastes 300 CNPJs gets 300 rows back, and a row without a name
    forces them to join the answer against their own list before they can act
    on it. The name is already inside the findings; this lifts it to the row.

    THE RULE, AND IT IS A RULE ABOUT DIGITS, NOT ABOUT TRUST. A name is taken
    from a finding only when that finding's `sanctionedCnpj` has exactly 14
    digits AND those digits are the 14 digits that were asked about. Having
    queried by CNPJ is not an argument: the answer is what is checked, not the
    question. A CPF has 11 digits and can never satisfy the test, so the name
    of a natural person cannot arrive here even if the registry returned one
    and even if every other guard in this file were removed.

    `companyName` is `razaoSocialReceita` and `tradeName` is
    `nomeFantasiaReceita`, both of them the Receita Federal registry entry for
    that CNPJ, which is public data about a legal person. `pessoa.nome` stays
    dropped always; `sancionado.nome` fills `companyName` only through
    `_company_name_from_sancionado` (14-digit CNPJ equal to the query, no CPF).
    """
    wanted = _digits(cnpj)
    identity: dict[str, Any] = {"companyName": None, "tradeName": None}
    if len(wanted) != CNPJ_DIGITS:
        return identity
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        found = _digits(finding.get("sanctionedCnpj"))
        if len(found) != CNPJ_DIGITS or found != wanted:
            continue
        for column in ("companyName", "tradeName"):
            if identity[column]:
                continue
            value = finding.get(column)
            if isinstance(value, str) and value.strip():
                identity[column] = value.strip()
        if identity["companyName"] and identity["tradeName"]:
            break
    return identity


def build_result(
    cnpj: str,
    raw_by_source: dict[str, list[dict]],
    checked_at: datetime | None = None,
) -> dict:
    """One dataset row for one CNPJ: verdict, evidence and audit trail."""
    findings: list[dict] = []
    counts: dict[str, int] = {}
    unmapped: list[str] = []
    dropped_person_records = 0

    for source in SOURCE_ORDER:
        if source not in raw_by_source:
            continue
        source_findings, source_unmapped, dropped = filter_records(
            raw_by_source.get(source) or [], source, cnpj
        )
        findings.extend(source_findings)
        counts[source] = len(source_findings)
        unmapped.extend(source_unmapped)
        dropped_person_records += dropped

    sources_used = [name for name in SOURCE_ORDER if name in counts]
    timestamp = _now_iso(checked_at)
    total = len(findings)
    verdict = "flagged" if total else "clear"
    flagged_sources = [name for name in sources_used if counts.get(name)]

    if verdict == "clear":
        justification = (
            f"No sanction record was returned for CNPJ {_format_cnpj(cnpj)} by "
            f"{' and '.join(name.upper() for name in sources_used) or 'any registry'} "
            f"at {timestamp}; this is not a certificate of good standing and it "
            "covers only these federal registries."
        )
    else:
        detail = ", ".join(
            f"{name.upper()}: {counts.get(name, 0)}" for name in flagged_sources
        )
        justification = (
            f"{total} sanction record(s) returned for CNPJ {_format_cnpj(cnpj)} "
            f"({detail}) at {timestamp}; read each record below before contracting."
        )

    identity = company_identity(cnpj, findings)

    return {
        "cnpj": cnpj,
        "cnpjFormatted": _format_cnpj(cnpj),
        # Null on a clear row, and that is not a gap: the four registries only
        # name a company they have a record about, so a company with no record
        # has no name to give. Null here means "no record", never "unknown".
        "companyName": identity["companyName"],
        "tradeName": identity["tradeName"],
        "verdict": verdict,
        "findingCount": total,
        "findingsBySource": {name: counts.get(name, 0) for name in sources_used},
        "findings": findings,
        "sourcesChecked": _sources_checked(sources_used),
        "checkedAt": timestamp,
        "justification": justification,
        "dataAttribution": DATA_ATTRIBUTION,
        "personRecordsDiscarded": dropped_person_records,
        "unmappedKeys": sorted(set(unmapped)),
        "error": None,
    }


def build_error_result(
    cnpj: str,
    message: str,
    checked_at: datetime | None = None,
    sources: Iterable[str] = SOURCE_ORDER,
) -> dict:
    """A row for a CNPJ we could not check. Never a verdict we did not earn."""
    timestamp = _now_iso(checked_at)
    return {
        "cnpj": cnpj,
        "cnpjFormatted": _format_cnpj(cnpj),
        # An error row never carries a name: nothing was read, so there is
        # nothing to name the company with.
        "companyName": None,
        "tradeName": None,
        "verdict": "error",
        "findingCount": 0,
        "findingsBySource": {},
        "findings": [],
        "sourcesChecked": _sources_checked(sources),
        "checkedAt": timestamp,
        "justification": (
            f"CNPJ {_format_cnpj(cnpj)} was not checked at {timestamp}: {message}"
        ),
        "dataAttribution": DATA_ATTRIBUTION,
        "personRecordsDiscarded": 0,
        "unmappedKeys": [],
        "error": message,
    }
