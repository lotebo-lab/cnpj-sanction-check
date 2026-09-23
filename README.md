# Brazil CNPJ Sanction Check: CEIS, CNEP, CEPIM and Leniency Agreements

**Run it on the Apify Store: https://apify.com/lotebo-lab/cnpj-sanction-check**

This repository holds the source code of that Actor. The Actor itself runs on the Apify
platform, so there is nothing to install to use it.

## What it does

Give it a list of Brazilian company numbers (CNPJ) and it checks each company against **four
federal sanction registries published by the CGU on the Portal da Transparência** — CEIS, CNEP,
CEPIM and Acordos de Leniência. You get one dataset row per company with a `clear` or `flagged`
verdict, every sanction record found, the official link to each record, and the UTC timestamp of
the check.

Doing this by hand means opening the CGU portal and searching the same CNPJ four times, one
registry at a time, for every supplier on the list.

**An automated lookup does not attest that a company is idônea, it is not a certidão, and it
does not replace legal analysis.** A `clear` verdict means only that these four federal
registries returned no record for that CNPJ at that moment. What is left out is listed by name
under "What this Actor does not do".

## Who runs it

Procurement, legal and compliance teams that have to clear a Brazilian company before
contracting it:

- supplier homologation and onboarding, before the first purchase order;
- third party due diligence and KYB, where the file has to show which lists were checked and when;
- bidding and public tender files, where the check has to be evidenced, not remembered;
- periodic re-screening of a whole supplier base.

The output is written for the second reader: the auditor who opens the file months later and asks
which registries were checked, at what time, and where each finding came from.

## The four registries it reads

Every route is on the same open data API, `https://api.portaldatransparencia.gov.br`, and each
registry keeps the Portuguese name the CGU publishes it under, so a row in the output matches the
official list.

| registry | what it lists | official API route | CNPJ parameter sent |
|---|---|---|---|
| **CEIS** (Cadastro de Empresas Inidôneas e Suspensas) | companies declared unfit or suspended from contracting with the public administration | `GET` https://api.portaldatransparencia.gov.br/api-de-dados/ceis | `codigoSancionado` |
| **CNEP** (Cadastro Nacional de Empresas Punidas) | companies punished under the Anti-Corruption Law, Lei 12.846/2013 | `GET` https://api.portaldatransparencia.gov.br/api-de-dados/cnep | `codigoSancionado` |
| **CEPIM** (Cadastro de Entidades Privadas Sem Fins Lucrativos Impedidas) | non-profit entities barred from new agreements and federal transfers | `GET` https://api.portaldatransparencia.gov.br/api-de-dados/cepim | `cnpjSancionado` |
| **Acordos de Leniência** (leniency agreements, Lei 12.846/2013) | companies that signed a leniency agreement with the CGU, and the sanctions attached to it | `GET` https://api.portaldatransparencia.gov.br/api-de-dados/acordos-leniencia | `cnpjSancionado` |

The parameter name is not the same on all four routes. This Actor sends the right one per route,
and `tests/test_schema_contract.py` checks each name against the registry's published schema.
That test matters because the schema makes the CNPJ filter optional on all four routes: a request
that misspells the filter is still a valid request for the *unfiltered* registry, and the rows
coming back would be other companies' sanctions.

Each company is queried once per route, so at least four registry calls per CNPJ, and the four
answers are merged into one row. Checking only CEIS and CNEP leaves two real gaps: a non-profit
barred from federal transfers appears in CEPIM and nowhere else, and a company that negotiated a
leniency agreement appears in the leniency registry.

Opening one of the four links in a plain browser tab returns an authentication error rather than
the registry, because a route only answers with data when the request carries a token in the
`chave-api-dados` header.

## Input

The example below is exactly what the Actor is prefilled with, copied from the `prefill` and
`default` values in [`.actor/input_schema.json`](.actor/input_schema.json):

```json
{
  "cnpjs": ["00000000000191"],
  "portalToken": "<your own free Portal da Transparencia token>",
  "maxCnpjs": 200
}
```

| field | type | default | notes |
|---|---|---|---|
| `cnpjs` (required) | array of strings | — | punctuation optional: `12.345.678/0001-99` and `12345678000199` both work. Anything that is not 14 digits is skipped and listed in the run summary; duplicates are removed, so the same company is never charged twice |
| `portalToken` (required) | string, secret | — | your own token for the Portal da Transparência API. See the next section |
| `maxCnpjs` | integer | 200 | 1 to 1000 companies per run; companies above the ceiling are skipped and listed in the run summary |

### The token is yours, it is free, and it never stays with us

`portalToken` is required: without a token this Actor cannot run at all.

**The token costs nothing.** The Portal da Transparência API has no paid tier and there is nothing
to buy. The page that hands it out, https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email,
redirects to the gov.br single sign-on and asks for a login with a CPF, the Brazilian individual
taxpayer number; we measured that on 2026-09-20, when we requested our own. So the token is free
and gated by a Brazilian identity: whoever already has a gov.br account can get one, and if nobody
you can ask is able to sign in to gov.br, you will not get a token and this Actor will not run for
you. We know of no other way to obtain one and will not suggest that there is.

**This Actor has no token of its own and shares none.** It uses the token you paste into
`portalToken`, sends it only in the `chave-api-dados` header of the request to the Portal, and
never writes it to the dataset, the log or a URL. The field is declared `isSecret` in the input
schema, and nothing about it survives the run. If the Portal refuses the token (HTTP 401 or 403)
the run stops, charges nothing, and writes a summary row saying `NOT CHECKED`, with the HTTP
status and what to fix. A company that was not checked is never reported as clear.

## Output

[`.actor/output_schema.json`](.actor/output_schema.json) declares two places to read:

| output | where it lands |
|---|---|
| `verdicts` — verdict per company | the run dataset, `.../dataset/items` |
| `summary` — run summary | the key-value store record `SUMMARY` |

The dataset holds two kinds of row, told apart by `rowType`: one **company** row per CNPJ, and
exactly one **summary** row per run, written last.

### The company row

Every value in the block below is the `example` value declared for that field in
[`.actor/dataset_schema.json`](.actor/dataset_schema.json), which is the schema the Apify Console
renders as the output table. The field names are the real ones; the values are illustrative.

```json
{
  "rowType": "company",
  "cnpj": "00000000000191",
  "cnpjFormatted": "00.000.000/0001-91",
  "companyName": "EMPRESA EXEMPLO LTDA",
  "tradeName": "EXEMPLO",
  "verdict": "clear",
  "findingCount": 0,
  "findingsBySource": {"ceis": 1, "cnep": 1, "cepim": 1, "leniencia": 1},
  "findings": [
    {
      "source": "ceis",
      "sanctionRegistry": "CEIS",
      "sourceUrl": "https://api.portaldatransparencia.gov.br/api-de-dados/ceis",
      "recordUrl": "https://api.portaldatransparencia.gov.br/api-de-dados/ceis/987654",
      "sanctionCategory": "Inidoneidade",
      "sanctionStartDate": "01/03/2025",
      "sanctioningBody": "Ministerio da Saude"
    }
  ],
  "sourcesChecked": [
    {
      "source": "ceis",
      "label": "CEIS",
      "registry": "CEIS - Cadastro de Empresas Inidoneas e Suspensas",
      "publisher": "Controladoria-Geral da Uniao (CGU) - Portal da Transparencia",
      "url": "https://api.portaldatransparencia.gov.br/api-de-dados/ceis"
    }
  ],
  "checkedAt": "2026-09-20T12:00:00+00:00",
  "justification": "No sanction record was returned for CNPJ 00.000.000/0001-91 by CEIS and CNEP and CEPIM and LENIENCIA at 2026-09-20T12:00:00+00:00; this is not a certificate of good standing and it covers only these federal registries.",
  "dataAttribution": "Source: Portal da Transparencia, Controladoria-Geral da Uniao (CGU), Brazil. Open data under Creative Commons Attribution, Decreto 8.777/2016.",
  "personRecordsDiscarded": 0,
  "unmappedKeys": [],
  "error": null
}
```

- `verdict` is `clear` (no registry returned a record), `flagged` (at least one did) or `error`
  (the check could not be completed).
- `findings` carries one entry per sanction record. The four registries do not fill the same
  fields, so a field a registry left empty is absent from that record: `impedimentReason` comes
  from CEPIM, `agreementStatus` and `sanctionedParties` from the leniency registry, `fineAmount`
  from CNEP, `sanctionCategory` and `legalBasis` from CEIS and CNEP. The complete list of mapped
  fields is `ALLOWED_PATHS` in [`src/rules.py`](src/rules.py).
- `companyName` and `tradeName` are copied from a sanction record only when that record's own
  identifier carries the 14 digits you asked about, and they are never the name of a person.
- `unmappedKeys` reports, **by name only and never by value**, fields the API returned that this
  Actor does not publish.

### The summary row, one per run

An empty dataset is the dangerous answer in a compliance check: "the token was refused, so
nothing was looked up" and "no company is sanctioned" would look identical. So every run writes
one last row, `rowType` = `summary`, saying which of the two happened. Its fields, as written in
[`src/summary_row.py`](src/summary_row.py): `finishedAt`, `cnpjsRequested`, `cnpjsChecked`,
`cnpjsClear`, `cnpjsFlagged`, `cnpjsWithError`, `cnpjsNotChecked`, `invalidInputCount`,
`skippedOverLimitCount`, `registriesChecked`, `findingsByRegistry`,
`companiesFlaggedByRegistry`, `authFailed`, `authErrorHttpStatus`, `unmappedApiKeys`,
`chargedEvents`, `chargeFailures`, `chargeLimitReached`, `dataAttribution` and `message`.

`cnpjsNotChecked` counts companies that were never looked up at all, so they have no row:
unknown, not clear. When the token was refused, `message` opens with `NOT CHECKED` and never
reports a clear count. The same counts, plus the four source URLs, the API request and retry
totals and the invalid inputs themselves, also go to the `SUMMARY` record in the key-value store.

### No personal data comes out of this Actor

The registries do list sanctioned **individuals**: the official CEIS data dictionary declares
"TIPO DE PESSOA", "CPF OU CNPJ DO SANCIONADO" and "NOME DO SANCIONADO"
(https://www.portaldatransparencia.gov.br/pagina-interna/603412-dicionario-de-dados-sancoes-ceis).
Personal data is therefore reachable through this API, and this Actor is built so that it never
reaches your dataset:

- the output is an **allow list**, not a block list: a field is published only if it is explicitly
  mapped in `src/rules.py`. A field never seen before is dropped, never published;
- a record about a natural person is discarded whole, counted in `personRecordsDiscarded`, and
  never written;
- names and documents of people are refused by name: `pessoa.nome`, `pessoa.cpfFormatado`,
  `pessoa.numeroInscricaoSocial`, the same three under `pessoaJuridica`, `sancionado.nome`,
  `sancionado.codigoFormatado` and `sancoes[].nomeInformadoOrgaoResponsavel`;
- fields the API returns that the allow list does not map are reported by name only in
  `unmappedKeys` and `unmappedApiKeys`.

Several of the checks in `tests/test_rules.py` exist only for this rule.

## What this Actor does not do

- **It is not a certidão and does not replace one.** A `clear` verdict is not a certificate of
  good standing.
- **It gives no legal advice and does not replace legal analysis.** It does not attest that a
  company is idônea and it does not tell you whether you may contract it. The decision, and the
  review behind it, stay with you.
- **No state or municipal sanction registries.** A supplier debarred by a state or a city
  government is not in these four federal lists.
- **No TCU list** of contractors declared unfit by the Tribunal de Contas da União.
- **No court or judicial records**, no lawsuits, no enforcement proceedings, no bankruptcy or
  recuperação judicial.
- **No MTE employers register** (the "lista suja" of labour analogous to slavery): it is published
  as a semiannual file, not through this API
  (https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/cadastro_de_empregadores.pdf).
- **No tax, labour or credit debt registers**, and no check of the company's registration status
  at the Receita Federal.
- **No data about natural persons**: no name, no CPF, no partner, no legal representative.
- **No company registration lookup**: no address, no partners, no activity code.
- **No monitoring over time.** The verdict is the answer those four registries gave at
  `checkedAt`. Re-run the list to re-check it.

### Limits of what it does cover

- **Expired sanctions are included.** The Actor sends only the CNPJ and the page number; it uses
  none of the date filters the API offers, and CEPIM offers none. A `flagged` verdict can rest on
  a sanction whose `sanctionEndDate` is already in the past. Read the dates in each record; the
  Actor does not decide for you whether a sanction is still in force.
- **The same case can be counted twice.** A company punished under Lei 12.846/2013 can appear in
  CNEP and in the leniency registry at once. `findingCount` counts records, not cases.
- **A leniency agreement can cover several companies.** When it does, the companies stay in the
  flattened `sanctionedParties` text and `companyName` and `sanctionedCnpj` are deliberately left
  empty, so the row never shows another company's name as if it were the one you asked about.
- **Matching is by the 14 digits of the CNPJ only.** A branch or another company of the same
  group, under a different CNPJ, is a different company here, and there is no search by name.
- **Pagination has a ceiling.** Each registry is read page by page until an empty page, a repeated
  page, or 40 pages, whichever comes first. If the ceiling is reached, the log says the result may
  be incomplete (`src/portal_client.py`).
- **"Nothing found" and HTTP 404 are treated the same.** A route that stopped existing would look
  like an empty answer, and the row would come back `clear` (`src/portal_client.py`).
- **Speed.** The run keeps the access rate the Portal publishes (400 requests per minute from
  06:00 to 23:59 Brasília time and 700 from 00:00 to 05:59,
  https://portaldatransparencia.gov.br/api-de-dados), and each company costs at least four
  requests, so a long list takes minutes, not seconds.

## Manners and your responsibility

- **This Actor requests no URL that you supply.** It calls only the four documented open data
  routes listed above, with your token. There is no crawl: no site of yours, and no site of the
  company you are checking, is ever fetched, so there is no `robots.txt` for this Actor to consult
  on your behalf.
- **It respects the rules the Portal publishes**: the documented request rate per minute, the
  `chave-api-dados` header, and the pagination parameter of each route.
- **You are responsible for using your own token within the Portal's terms**, and for having the
  right to process the company data you take out of here.

## Price

Pay per event, two events, exactly as declared in [`.actor/actor.json`](.actor/actor.json):

| event | price | when it is charged |
|---|---|---|
| `company-checked` | US$ 0.10 | once per company, and that one event covers all four registries for that company |
| `batch-report` | US$ 0.25 | once per run, and only when at least one company was actually checked |

A run of 20 companies is 20 `company-checked` events plus 1 `batch-report`: US$ 2.25. Apify
charges its own Actor start event and the platform usage of the run on top of that; those are not
set by this Actor. A company the Actor could not check (network failure, refused token) produces
an error row and is not charged, and a run that checked nothing is not charged the batch report
either. If a run hits your charge limit it stops, keeps every company already checked in the
dataset, and says so in the log and in `chargeLimitReached`.

## For developers

```
.actor/            actor.json, input, output and dataset schemas
src/main.py        the run: reads input, calls the registries, writes rows, charges events
src/portal_client.py  HTTP against the Portal: rate limit, retries, pagination, auth errors
src/rules.py       the allow list of fields, the verdict and the four SOURCES
src/summary_row.py the run summary row and its message
tests/             the offline suite, six files, no network
docs/              the Portal OpenAPI document as downloaded, and the scripts that dump it
```

Run the whole suite offline, from this folder, with any Python 3.13+ that has `requests`:

```
python tests/run_all.py
```

Measured on 2026-09-22: `test_schema_contract.py` 7/7, `test_rules.py` 26/26,
`test_portal_client.py` 27/27, `test_charging.py` 9/9, `test_unmapped_keys_summary.py` 2/2 and
`test_summary_row.py` 6/6 — **77 checks in six files, all passing**. The suite makes no network
call: the Portal answers come from `tests/fixtures/portal_responses.json`.

### How the field mapping was checked

The mapping between each registry's fields and the columns of the output was checked against the
OpenAPI document of the Portal da Transparência, not against a live sanction answer. The document
was downloaded whole from https://api.portaldatransparencia.gov.br/v3/api-docs on 2026-09-20 and
is kept here as `docs/portal-openapi-2026-09-20.json`. `tests/test_schema_contract.py` reads it
and fails if any field the schema declares is neither mapped nor explicitly dropped, for the four
response types `CeisDTO`, `CnepDTO`, `CepimDTO` and `AcordosLenienciaDTO`.

What has **not** happened yet, stated plainly: no run of this Actor has ever received sanction
data from the live Portal. One run did reach the real API with a deliberately invalid token and
was refused with HTTP 401. So treat the column names as verified against the published schema, and
the example values as illustrative. If the live API ever returns a field this Actor does not map,
the field is dropped and its name appears in `unmappedApiKeys` in your run summary; send it to us
through the issues tab of the Actor on the Apify Store and it gets mapped.

## Ready-made example runs

Each page below is a published task of this Actor: a ready-made run that shows the input, the
fields that come back, and a Run button. Every URL in this list answered HTTP 200 when it was
checked on 2026-09-22.

- [How to screen a supplier CNPJ against Brazil sanctions](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/consultar-cnpj-de-fornecedor-em-lista-de-sancao)
- [Bulk due diligence on a list of Brazilian companies](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/due-diligence-em-lote-de-cnpj)
- [Check bidder eligibility before a public tender in Brazil](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/checar-fornecedor-em-licitacao-antes-do-contrato)
- [How do I check if a Brazilian supplier is debarred?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/check-if-a-brazilian-supplier-is-debarred)
- [Como checar uma lista de fornecedores por CNPJ?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/checar-lista-de-fornecedores-por-cnpj) (page in Portuguese)
- [Como consultar se um CNPJ está no CEIS ou no CNEP?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/consultar-cnpj-no-ceis-e-no-cnep) (page in Portuguese)
- [Como saber se uma ONG está impedida de receber repasse?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/verificar-se-uma-ong-esta-impedida-no-cepim) (page in Portuguese)
- [Como saber se uma empresa tem acordo de leniência?](https://apify.com/lotebo-lab/cnpj-sanction-check/examples/checar-se-uma-empresa-tem-acordo-de-leniencia) (page in Portuguese)

## Source and credit

Data from the Portal da Transparência, Controladoria-Geral da União (CGU), Brazilian federal
government, published as open data under Creative Commons Attribution and Decreto 8.777/2016. The
credit line travels in every row, in `dataAttribution`. This Actor reformats that data and adds a
verdict field; it is not an official document and it is not endorsed by the CGU.

This Actor was built with the help of AI. Every module has an offline automated test in `tests/`.

---

**Actor page on the Apify Store: https://apify.com/lotebo-lab/cnpj-sanction-check**
