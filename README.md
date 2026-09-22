# Brazil CNPJ Sanction Check: CEIS, CNEP, CEPIM and Leniency Agreements

Run it on Apify Store: https://apify.com/lotebo-lab/cnpj-sanction-check

Before you contract a Brazilian company you have to check whether it is on the federal sanction lists, and doing it by hand means opening the CGU portal and searching the same CNPJ four times, one registry at a time, for every supplier on your list.

This Actor takes a list of CNPJ numbers and checks each company against **four federal sanction registries published by the CGU on the Portal da Transparência** — CEIS, CNEP, CEPIM and Acordos de Leniência — returning one row per company with a `clear` or `flagged` verdict, every record found, the official link to each record and the UTC timestamp of the check.

**It is not a certidão. A `clear` verdict does not attest that a company is idônea, and this Actor does not replace legal analysis.** It reports what four federal registries answered at one moment. What it leaves out is listed by name in "What this Actor does not do".

## Who runs it, and when

Procurement, legal and compliance teams that have to clear a Brazilian company before contracting it:

- **supplier homologation and onboarding**, when a new vendor has to be screened before the first purchase order;
- **third party due diligence and KYB**, where the file has to show which lists were checked and when;
- **bidding and public tender files**, where the check has to be evidenced, not remembered;
- **periodic re-screening of a supplier base**, running the whole list again on a schedule.

The output is built for the second reader: the auditor who opens the file months later and asks which registries were checked, at what time, and where each finding came from.

One requirement comes before all of that: this Actor runs on a **free Portal da Transparência token that you bring**, and getting that token means signing in to gov.br with a Brazilian CPF. Read "The token is yours, and it is free" below before you buy.

## The four registries it reads

Every route below is on the same open data API, `https://api.portaldatransparencia.gov.br`, and each registry keeps the Portuguese name the CGU publishes it under, so a row in the output matches the official list.

| registry | what it lists | official API route | CNPJ parameter sent |
|---|---|---|---|
| **CEIS** (Cadastro de Empresas Inidôneas e Suspensas) | companies declared unfit or suspended from contracting with the public administration | `GET` https://api.portaldatransparencia.gov.br/api-de-dados/ceis | `codigoSancionado` |
| **CNEP** (Cadastro Nacional de Empresas Punidas) | companies punished under the Anti-Corruption Law, Lei 12.846/2013 | `GET` https://api.portaldatransparencia.gov.br/api-de-dados/cnep | `codigoSancionado` |
| **CEPIM** (Cadastro de Entidades Privadas Sem Fins Lucrativos Impedidas) | non-profit entities barred from new agreements and transfers with the federal government | `GET` https://api.portaldatransparencia.gov.br/api-de-dados/cepim | `cnpjSancionado` |
| **Acordos de Leniência** (leniency agreements, Lei 12.846/2013) | companies that signed a leniency agreement with the CGU, and the sanctions attached to it | `GET` https://api.portaldatransparencia.gov.br/api-de-dados/acordos-leniencia | `cnpjSancionado` |

Each of the four links above is the exact route this Actor calls, and each one is declared under the tag `Sanções` in the Portal's own OpenAPI document (https://api.portaldatransparencia.gov.br/v3/api-docs, copy kept in this repository at `docs/portal-openapi-2026-09-20.json`, where the `servers` entry is the same `https://api.portaldatransparencia.gov.br`). A route only answers with data when the request carries your token in the `chave-api-dados` header, so opening one of these links in a plain browser tab returns an authentication error rather than the registry.

The parameter name is not the same on all four routes, this Actor sends the right one per route, and `tests/test_schema_contract.py` checks each name against the published schema. That test matters because the schema makes the CNPJ filter optional on all four routes: a request that misspells the filter is still a valid request for the unfiltered registry, and the rows coming back would be other companies' sanctions.

Each company is queried once per route, so at least four registry calls per CNPJ, and the four answers are merged into one row. Checking only CEIS and CNEP leaves two real gaps: a non-profit barred from federal transfers appears in CEPIM and nowhere else, and a company that negotiated a leniency agreement appears in the leniency registry.

## What comes out, field by field

The dataset holds two kinds of row, told apart by the `rowType` field: one **company** row per CNPJ, and exactly one **summary** row per run, written last. The names below are the keys the Actor writes, in `src/rules.py` and `src/summary_row.py`.

### The company row

| field | type | what it holds |
|---|---|---|
| `rowType` | string | `company` on every row of this kind; the run summary carries `summary` instead |
| `cnpj` | string | the company number that was checked, digits only |
| `cnpjFormatted` | string | the same number as `00.000.000/0001-91` |
| `companyName` | string or null | the legal name (razao social) the Receita Federal has for this CNPJ, taken from a sanction record only when that record's own identifier carries the 14 digits you asked about. `null` when no record matched. It is never the name of a person: a record whose identifier has 11 digits is a CPF, it is discarded before this line is written, and it can never hand a name to it |
| `tradeName` | string or null | the nome fantasia for the same CNPJ, under the same 14-digit rule |
| `verdict` | string | `clear` (none of the four registries returned a record), `flagged` (at least one did) or `error` (the check could not be completed) |
| `findingCount` | integer | how many sanction records the four registries returned |
| `findingsBySource` | object | the count per registry, for example `{"ceis": 1, "cnep": 1, "cepim": 1, "leniencia": 1}` |
| `findings` | array of objects | one entry per sanction record; the fields inside are in the next table |
| `sourcesChecked` | array of objects | the four registries read for this row, each with `source`, `label`, `registry`, `publisher` and `url` |
| `checkedAt` | string | when the check ran, ISO 8601 in UTC |
| `justification` | string | one sentence saying why the verdict is what it is, written for your audit file |
| `dataAttribution` | string | the credit line for the CGU open data |
| `personRecordsDiscarded` | integer | records the registries returned about a natural person; counted, never published |
| `unmappedKeys` | array of strings | field names the API returned that this Actor does not publish; names only, never values |
| `error` | string or null | why the check failed, when `verdict` is `error` |

### Inside a finding

Inside `findings`, each record carries the fields the registry actually filled. The four registries do not fill the same fields, so a field a registry left empty is absent from that record.

| field in a finding | filled by | what it holds |
|---|---|---|
| `source` | all four | `ceis`, `cnep`, `cepim` or `leniencia` |
| `sanctionRegistry` | all four | the registry label, for example `CEIS` or `Acordos de Leniencia` |
| `sourceUrl` | all four | the API route of the registry |
| `recordUrl` | all four, when the record has an id | the URL of that single record, so a reviewer can reopen it at the source |
| `sanctionCode` | all four | the record id in the registry |
| `sanctionedCnpj`, `companyName`, `tradeName` | all four | the sanctioned company as the registry names it |
| `sanctionedParties` | leniency | every company covered by the agreement, flattened into one string |
| `personType` | CEIS, CNEP, CEPIM | the party type the registry declares, always a company here |
| `sanctionCategory`, `sanctionCategoryDetail` | CEIS, CNEP | what kind of sanction it is |
| `impedimentReason` | CEPIM | why the entity is barred from federal transfers |
| `agreementStatus` | leniency | the status of the leniency agreement |
| `sanctionScope` | CEIS, CNEP | the scope defined by the decision |
| `sanctionStartDate`, `sanctionEndDate` | CEIS, CNEP, leniency | dates as the API returns them, `DD/MM/AAAA` |
| `finalJudgmentDate` | CEIS, CNEP | date of final judgment |
| `sanctioningBody` | all four | the public body that applied the measure |
| `sanctioningBodyState`, `sanctioningBodySphere` | CEIS, CNEP | state and sphere of that body |
| `sanctioningBodyBranch` | CEIS, CNEP, CEPIM | branch of government of that body |
| `sanctioningBodyAcronym` | CEPIM | the acronym of the body; only the CEPIM route returns one |
| `processNumber` | CEIS, CNEP | the administrative or court process number |
| `legalBasis` | CEIS, CNEP | the legal grounds, flattened into one string |
| `fineAmount` | CNEP | the fine, when the registry states one |
| `agreementCode`, `agreementNumber`, `agreementSubject` | CEPIM | the federal transfer agreement involved |
| `publicationDate`, `publicationDetails`, `publicationText`, `publicationUrl` | CEIS, CNEP | where and when the sanction was published |
| `publicationVehicle` | none today | mapped from a `publicacao` field the published schema does not declare for any of the four registries, so it is absent from every record |
| `informationOrigin`, `informationOriginDate` | CEIS, CNEP | where the registry got the information and when |
| `referenceDate` | CEIS, CNEP, CEPIM | the reference date of the record in the registry |
| `notes` | CEIS, CNEP | free text the sanctioning body added |

### The summary row, one per run

An empty dataset is the dangerous answer in a compliance check: "the token was refused, so nothing was looked up" and "no company is sanctioned" would look identical. So every run writes one last row, `rowType` = `summary`, which says which of the two happened, in the dataset itself.

| field | type | what it holds |
|---|---|---|
| `rowType` | string | always `summary` on this row |
| `finishedAt` | string | when the run finished, ISO 8601 in UTC |
| `cnpjsRequested` | integer | how many company numbers the input asked for, including the ones skipped by the `maxCnpjs` ceiling |
| `cnpjsChecked` | integer | how many were really screened, that is `clear` plus `flagged` |
| `cnpjsClear` | integer | how many came back with no record in any of the four registries |
| `cnpjsFlagged` | integer | how many came back with at least one record |
| `cnpjsWithError` | integer | how many have an `error` row instead of a verdict |
| `cnpjsNotChecked` | integer | how many were never looked up at all, so they have no row: unknown, not clear |
| `invalidInputCount` | integer | input values that are not 14 digits and were skipped |
| `skippedOverLimitCount` | integer | companies dropped by the `maxCnpjs` ceiling |
| `registriesChecked` | array of strings | the registry labels this build reads: `CEIS`, `CNEP`, `CEPIM`, `Acordos de Leniencia` |
| `findingsByRegistry` | object | records found per registry; empty when no company was screened, so zeros always mean "we looked and found nothing" |
| `companiesFlaggedByRegistry` | object | how many distinct companies each registry flagged, under the same rule |
| `authFailed` | boolean | true when the Portal refused the token or no token was given |
| `authErrorHttpStatus` | integer or null | the status the Portal answered, 401 or 403; null when the token field was empty and no call was made |
| `unmappedApiKeys` | array of strings | field names the API returned that this Actor does not publish; names only, never values |
| `chargedEvents` | object | what was charged in this run, per event name |
| `chargeFailures` | integer | charge calls that did not go through; the run finishes the work anyway |
| `chargeLimitReached` | boolean | true when the run stopped early on your charge limit, so part of the list was not checked |
| `dataAttribution` | string | the credit line for the CGU open data |
| `message` | string | the same counts in one plain sentence. When the token was refused it opens with `NOT CHECKED`, never reports a clear count, and says an unchecked company is unknown, not clear |

Every run also writes a `SUMMARY` record in the key-value store, with the same counts plus the four source URLs, API requests and retries, `personRecordsDiscarded`, the invalid inputs themselves and, when the token was refused, an `authError` block.

### No personal data comes out of this Actor

The registries do list sanctioned **individuals**: the official CEIS data dictionary declares "TIPO DE PESSOA", "CPF OU CNPJ DO SANCIONADO" and "NOME DO SANCIONADO" (https://www.portaldatransparencia.gov.br/pagina-interna/603412-dicionario-de-dados-sancoes-ceis). Personal data is therefore reachable through this API, and this Actor is built so that it never reaches your dataset:

- the output is an **allow list**, not a block list: a field is published only if it is explicitly mapped in `src/rules.py`. A field never seen before is dropped, never published;
- a record about a natural person is discarded whole, counted in `personRecordsDiscarded`, and never written;
- names and documents of people are refused by name: `pessoa.nome`, `pessoa.cpfFormatado`, `pessoa.numeroInscricaoSocial`, the same three under `pessoaJuridica`, `sancionado.nome`, `sancionado.codigoFormatado` and `sancoes[].nomeInformadoOrgaoResponsavel`;
- fields the API returns that the allow list does not map are reported **by name only**, never by value, in `unmappedKeys` and `unmappedApiKeys`.

This is tested: `tests/test_rules.py` passes 16/16, and five of those tests exist only for this rule. Lines 294 and 295 of `logs/corrida-local-2026-09-20-quatro-cadastros.log` read `Person values present in the source answers and found in the dataset: []` and `Token found anywhere in the dataset: False`.

### Example output

Copied from `logs/corrida-local-2026-09-20-quatro-cadastros.log` in this repository: an end-to-end run of `src/main.py` covering the four registries, in which the calls to the Portal are answered from `tests/fixtures/portal_responses.json`, because the build sandbox has no route to the Portal. **The values are illustrative; the column names are the real ones.** No run of this Actor has ever received sanction data from the live Portal — see "How this was checked". That log was recorded before the `rowType` field and the summary row existed, so neither of the two appears in the snippets below; both are covered by `tests/test_summary_row.py`.

A flagged company, shortened to two of its four records (full row at line 32 of the log):

```json
{
  "cnpj": "11222333000181",
  "cnpjFormatted": "11.222.333/0001-81",
  "companyName": "EMPRESA EXEMPLO LTDA",
  "tradeName": "EXEMPLO",
  "verdict": "flagged",
  "findingCount": 4,
  "findingsBySource": {"ceis": 1, "cnep": 1, "cepim": 1, "leniencia": 1},
  "findings": [
    {
      "source": "ceis",
      "sanctionRegistry": "CEIS",
      "sourceUrl": "https://api.portaldatransparencia.gov.br/api-de-dados/ceis",
      "recordUrl": "https://api.portaldatransparencia.gov.br/api-de-dados/ceis/8123456",
      "sanctionCode": 8123456,
      "companyName": "FORNECEDORA EXEMPLO LTDA",
      "sanctionCategory": "Impedimento",
      "sanctionScope": "Orgao sancionador",
      "sanctionStartDate": "03/11/2025",
      "sanctionEndDate": "02/11/2027",
      "sanctioningBody": "MINISTERIO DA SAUDE",
      "processNumber": "0008123-45.2025.4.03.6100",
      "legalBasis": "14133-156-III - Lei 14.133/2021, art. 156, III",
      "publicationDate": "05/11/2025"
    },
    {
      "source": "cepim",
      "sanctionRegistry": "CEPIM",
      "sourceUrl": "https://api.portaldatransparencia.gov.br/api-de-dados/cepim",
      "recordUrl": "https://api.portaldatransparencia.gov.br/api-de-dados/cepim/55112",
      "sanctionCode": 55112,
      "companyName": "FORNECEDORA EXEMPLO LTDA",
      "impedimentReason": "Prestacao de contas nao aprovada",
      "sanctioningBody": "MINISTERIO DA EDUCACAO",
      "sanctioningBodyAcronym": "MEC",
      "agreementNumber": "912345/2023",
      "agreementSubject": "Aquisicao de equipamentos para laboratorio escolar",
      "referenceDate": "19/09/2026"
    }
  ],
  "personRecordsDiscarded": 0,
  "unmappedKeys": [],
  "error": null
}
```

A clear company (line 196 of the log), with `sourcesChecked` and `dataAttribution` trimmed for length:

```json
{
  "cnpj": "99888777000166",
  "cnpjFormatted": "99.888.777/0001-66",
  "verdict": "clear",
  "findingCount": 0,
  "findingsBySource": {"ceis": 0, "cnep": 0, "cepim": 0, "leniencia": 0},
  "findings": [],
  "checkedAt": "2026-09-20T14:41:07+00:00",
  "justification": "No sanction record was returned for CNPJ 99.888.777/0001-66 by CEIS and CNEP and CEPIM and LENIENCIA at 2026-09-20T14:41:07+00:00; this is not a certificate of good standing and it covers only these federal registries.",
  "personRecordsDiscarded": 0,
  "error": null
}
```

The run summary (line 246 of the log) lists the four routes read and reports an invalid value instead of silently dropping it:

```json
{
  "cnpjsRequested": 2,
  "cnpjsChecked": 2,
  "flagged": 1,
  "clear": 1,
  "errors": 0,
  "invalidInputs": ["123"],
  "sources": [
    "https://api.portaldatransparencia.gov.br/api-de-dados/ceis",
    "https://api.portaldatransparencia.gov.br/api-de-dados/cnep",
    "https://api.portaldatransparencia.gov.br/api-de-dados/cepim",
    "https://api.portaldatransparencia.gov.br/api-de-dados/acordos-leniencia"
  ],
  "apiRequests": 12,
  "apiRetries": 0,
  "personRecordsDiscarded": 1,
  "chargedEvents": {"company-checked": 2, "batch-report": 1}
}
```

## Input

The example below is the input this Actor is prefilled with, so you can run one company and read the shape of the row before you send your list.

```json
{
  "cnpjs": ["00000000000191"],
  "portalToken": "<your own free Portal da Transparencia token>",
  "maxCnpjs": 200
}
```

| field | type | default | notes |
|---|---|---|---|
| `cnpjs` (required) | array of strings | — | formatted or digits only: `12.345.678/0001-99` and `12345678000199` both work. Anything that is not 14 digits is skipped and listed in the summary; duplicates are removed, so you never pay twice for the same company |
| `portalToken` (required) | string, secret | — | your own token for the Portal da Transparência API. See below |
| `maxCnpjs` | integer | 200 | 1 to 1000 companies per run; companies above the limit are skipped and listed in the summary |

### The token is yours, and it is free

`portalToken` is required: without a token this Actor cannot run at all.

**The token costs nothing.** The Portal da Transparência API has no paid tier and there is nothing to buy. But the page that hands the token out, https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email, redirects to the gov.br single sign-on, and gov.br asks for a login with a CPF, the Brazilian individual taxpayer number. We measured that on 2026-09-20, when we requested our own token. So the token is free and it is gated by a Brazilian identity: whoever already has a gov.br account can get one, and if nobody you can ask is able to sign in to gov.br, you will not get a token and this Actor will not run for you. We know of no other way to obtain one and will not suggest that there is.

**This Actor has no token of its own and shares none.** It uses the token you paste into `portalToken`, sends it only in the `chave-api-dados` header of the request to the Portal, and never writes it to the dataset, the log or a URL. The field is declared secret in the input schema, and nothing survives the run. If the Portal refuses the token (HTTP 401 or 403), the run stops, charges nothing, and writes the summary row saying `NOT CHECKED`, with the HTTP status and what to fix; a company that was not checked is never reported as clear.

## What this Actor does not do

- **It is not a certidão and does not replace one.** A `clear` verdict is not a certificate of good standing; it means these four federal registries returned no record for that CNPJ at that moment.
- **It does not give legal advice and does not replace legal analysis.** It does not attest that a company is idônea, it does not certify that a company is fit to contract, and it does not tell you whether you may contract it. The decision, and the review behind it, stay with you.
- **No state or municipal sanction registries.** A supplier debarred by a state or a city government is not in these four federal lists.
- **No TCU list** of contractors declared unfit by the Tribunal de Contas da União.
- **No court or judicial records**, no lawsuits, no enforcement proceedings, no bankruptcy or recuperação judicial.
- **No MTE employers register** (the "lista suja" of labour analogous to slavery): it is published as a semiannual file, not through this API (https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/cadastro_de_empregadores.pdf).
- **No tax, labour or credit debt registers**, and no check of the company's registration status at the Receita Federal.
- **No data about natural persons**: no name, no CPF, no partner or legal representative of a company.
- **No company registration lookup**: no address, no partners, no activity code.
- **No monitoring over time.** The verdict is the answer those four registries gave at `checkedAt`. A sanction published later is not in an earlier run; re-run the list to re-check it.

### Limits of what it does cover

- **Expired sanctions are included.** The Actor sends only the CNPJ and the page number; it does not use the date filters the API offers, and CEPIM offers none. A `flagged` verdict can rest on a sanction whose `sanctionEndDate` is already in the past. Read the dates in each record; the Actor does not decide for you whether a sanction is still in force.
- **The same case can be counted twice.** A company punished under Lei 12.846/2013 can appear in CNEP and in the leniency registry at the same time. `findingCount` counts records per registry and does not merge them into a single case.
- **A leniency agreement can cover several companies.** When it does, the companies stay in the flattened `sanctionedParties` text and `companyName` and `sanctionedCnpj` are deliberately left empty, so the row never shows another company's name as if it were the one you asked about.
- **Matching is by the 14 digits of the CNPJ only.** A branch or another company of the same group, under a different CNPJ, is a different company here, and there is no search by company name.
- **Pagination has a ceiling.** Each registry is read page by page until an empty page, a repeated page, or 40 pages, whichever comes first. If the ceiling is reached, the run log says the result may be incomplete (`src/portal_client.py`).
- **"Nothing found" and HTTP 404 are treated the same.** When a route answers HTTP 404 this Actor reads it as "no record for this CNPJ" instead of raising an error (`src/portal_client.py`). A route that stopped existing would look like an empty answer, and the row would come back `clear`.
- **Speed.** The run keeps the access rate the Portal publishes (400 requests per minute from 06:00 to 23:59 Brasília time and 700 from 00:00 to 05:59, https://portaldatransparencia.gov.br/api-de-dados), and each company costs at least four requests, so a long list takes minutes, not seconds.

## Manners, `robots.txt` and your responsibility

- **This Actor requests no URL that you supply.** It calls only the four documented open data routes listed above, on `api.portaldatransparencia.gov.br`, with your token. There is no crawl: no site of yours, and no site of the company you are checking, is ever fetched, so there is no `robots.txt` for this Actor to consult on your behalf.
- **It respects the rules the Portal publishes**: the documented request rate per minute, the `chave-api-dados` header, and the pagination parameter of each route.
- **You are responsible for using your own token within the Portal's terms**, and for having the right to process the company data you take out of here. The CNPJs you check and the file you build from them are yours.
- The CGU data is open data under Creative Commons Attribution, Decreto 8.777/2016, and the credit line travels in every row in `dataAttribution`.

## Price

Pay per event, two events, exactly as declared in `.actor/actor.json`:

| event | price | when it is charged |
|---|---|---|
| `company-checked` | US$ 0.10 | once per company, and that one event covers all four registries for that company |
| `batch-report` | US$ 0.25 | once per run, and only when at least one company was actually checked |

A run of 20 companies is 20 `company-checked` events plus 1 `batch-report`: US$ 2.25. Apify charges its own Actor start event and the platform usage of the run on top of this; those are not set by this Actor. A company the Actor could not check (network failure, refused token) produces an error row and is not charged, and a run that checked nothing is not charged the batch report either. If a run hits your charge limit it stops, keeps every company already checked in the dataset, and says so in the log and in `chargeLimitReached`.

## How this was checked

The mapping between the fields of each registry and the columns of the output was checked against the OpenAPI document of the Portal da Transparência, not against a live sanction answer. The document was downloaded whole from https://api.portaldatransparencia.gov.br/v3/api-docs on 2026-09-20 and is kept in this repository at `docs/portal-openapi-2026-09-20.json`. `tests/test_schema_contract.py` reads it and fails if any field the schema declares is neither mapped nor explicitly dropped, for the four response types `CeisDTO`, `CnepDTO`, `CepimDTO` and `AcordosLenienciaDTO`.

The whole suite runs offline with `python tests/run_all.py`: `test_schema_contract.py` 7/7, `test_rules.py` 16/16, `test_portal_client.py` 22/22, `test_charging.py` 9/9, `test_unmapped_keys_summary.py` 2/2 and `test_summary_row.py` 6/6, six files and 62 checks, all green on 2026-09-20.

What has **not** happened yet, stated plainly: no run of this Actor has ever received sanction data from the live Portal. One run did reach the real API with a deliberately invalid token and was refused with HTTP 401, recorded in `logs/corrida-live-token-invalido-2026-09-20.log`; that run exercised an earlier two registry build. So treat the column names as verified against the published schema, and the example values as illustrative. If the live API ever returns a field this Actor does not map, the field is dropped and its name appears in `unmappedApiKeys` in your run summary; send it to us through the Apify Store issues tab and it gets mapped.

## Where this Actor runs

This repository holds the source code. The Actor itself runs on the Apify platform, and its page is https://apify.com/lotebo-lab/cnpj-sanction-check, where you can read the input schema, the price per event and the example runs, and start it without installing anything.

## Source and credit

Data from the Portal da Transparência, Controladoria-Geral da União (CGU), Brazilian federal government. The credit line in every row reads: "Source: Portal da Transparencia, Controladoria-Geral da Uniao (CGU), Brazil. Open data under Creative Commons Attribution, Decreto 8.777/2016. Data reformatted by this Actor; not an official document." This Actor reformats that data and adds a verdict field; it is not an official document and it is not endorsed by the CGU.

This Actor was built with the help of AI. Every module has an offline automated test in `tests/`, run with `python tests/run_all.py`.

## Example tasks

Each page below is a published task of this Actor: a ready-made run that shows the input that produced it, the fields that come back, and a Run button that starts the Actor with that input. Every URL in this list answered HTTP 200 when it was checked on 2026-09-20.

- [How to screen a supplier CNPJ against Brazil sanctions](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/consultar-cnpj-de-fornecedor-em-lista-de-sancao): checks a list of supplier CNPJs against the four federal sanction registries before homologation, with one row per company.
- [Bulk due diligence on a list of Brazilian companies](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/due-diligence-em-lote-de-cnpj): runs federal sanction due diligence on hundreds of CNPJs in one pass, with a clear or flagged verdict per company.
- [Check bidder eligibility before a public tender in Brazil](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/checar-fornecedor-em-licitacao-antes-do-contrato): screens every bidder CNPJ in a licitação against the four registries, so an impeded company is caught before the contract is awarded.
- [Como checar uma lista de fornecedores por CNPJ?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/checar-lista-de-fornecedores-por-cnpj) (check a whole supplier list by CNPJ, page in Portuguese): send the CNPJ list of your supplier base and get one verdict per company, clear or flagged, plus the record of the check to attach to the file.
- [Como consultar se um CNPJ está no CEIS ou no CNEP?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/consultar-cnpj-no-ceis-e-no-cnep) (is this CNPJ in CEIS or CNEP, page in Portuguese): send the CNPJ and your free Portal da Transparência token and get one row per company with what was found in CEIS, CNEP, CEPIM and Acordos de Leniência.
- [How do I check if a Brazilian supplier is debarred?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/check-if-a-brazilian-supplier-is-debarred): screens one CNPJ against the four CGU federal registries and returns a clear or flagged verdict with the official link of each record.
- [Como saber se uma ONG está impedida de receber repasse?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/verificar-se-uma-ong-esta-impedida-no-cepim) (is this non-profit barred from federal agreements, page in Portuguese): checks a CNPJ in CEPIM together with CEIS, CNEP and leniency agreements.
- [Como saber se uma empresa tem acordo de leniência?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/checar-se-uma-empresa-tem-acordo-de-leniencia) (does this company have a leniency agreement, page in Portuguese): reads the leniency agreements and CNEP for each CNPJ, with the record count per registry.
