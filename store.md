# Página da Apify Store, pronta para colar no Console

Tudo aqui saiu da leitura do código em `src/` e do `.actor/` desta pasta, feita
depois de rodar os testes. Nenhuma frase promete o que o código não entrega.

Enquadramento obrigatório, que já está no README e precisa aparecer na página:
o Actor lê quatro cadastros federais brasileiros por CNPJ e devolve o que eles
responderam, com data e hora. Veredito `clear` significa apenas que estes quatro
cadastros não devolveram registro naquele instante. **Não é certidão, não atesta
idoneidade e não substitui análise jurídica.**

## Os quatro cadastros, pelo nome e com o link oficial

| cadastro | o que lista | rota oficial |
|---|---|---|
| CEIS - Cadastro de Empresas Inidôneas e Suspensas | empresas inidôneas ou suspensas de contratar com a administração pública | https://api.portaldatransparencia.gov.br/api-de-dados/ceis |
| CNEP - Cadastro Nacional de Empresas Punidas | empresas punidas pela Lei Anticorrupção (Lei 12.846/2013) | https://api.portaldatransparencia.gov.br/api-de-dados/cnep |
| CEPIM - Cadastro de Entidades Privadas Sem Fins Lucrativos Impedidas | entidades sem fins lucrativos impedidas de novo convênio ou repasse federal | https://api.portaldatransparencia.gov.br/api-de-dados/cepim |
| Acordos de Leniência (Lei 12.846/2013) | empresas que assinaram acordo de leniência com a CGU e as sanções ligadas a ele | https://api.portaldatransparencia.gov.br/api-de-dados/acordos-leniencia |

Os nomes, os rótulos e as URLs saem de `SOURCES` em `src/rules.py`, que é a
única lista de cadastros do código. Todo cadastro é federal e publicado pela
CGU; cada achado do dataset carrega o nome do cadastro, a URL do cadastro e a
URL do registro individual.

## Título

```
Brazil CNPJ Sanction Check: CEIS, CNEP, CEPIM and Leniency
```

Igual ao `title` de `.actor/actor.json`.

## Nome técnico (URL do Actor)

```
cnpj-sanction-check
```

Igual ao `name` de `.actor/actor.json`.

## Descrição curta (aparece na busca da Store)

```
Give it a list of Brazilian CNPJs and get one row per company with a clear or flagged verdict against the four federal sanction registries, CEIS, CNEP, CEPIM and Acordos de Leniencia, with every record, the official link and the timestamp for your audit file.
```

## Categorias

1. Business
2. Developer tools

Motivo: quem compra é compras, jurídico e compliance homologando fornecedor no
Brasil, e a entrega é um dataset. Conferir os rótulos exatos no seletor do
Console; a lista completa de categorias só aparece com a conta aberta.

## Descrição longa

Usar o `README.md` desta pasta, que o Console renderiza na página do Actor. Ele
traz, nesta ordem: o que o Actor faz, a tabela dos quatro cadastros lidos com o
link oficial de cada um, o parágrafo de por que quatro cadastros valem mais que
dois, a entrada, a seção do token grátis do comprador, a saída com exemplo de
linha, os limites, a seção "What this Actor does not do" e a atribuição da
fonte com a declaração de IA.

## O que o comprador recebe, campo por campo (está no código)

Uma linha de dataset por CNPJ, com: `cnpj`, `cnpjFormatted`, `verdict`
(`clear`, `flagged` ou `error`), `findingCount`, `findingsBySource`, `findings`,
`sourcesChecked`, `checkedAt`, `justification`, `dataAttribution`,
`personRecordsDiscarded`, `unmappedKeys` e `error`.

Cada item de `findings` traz `source`, `sourceUrl` (a rota oficial do cadastro),
`recordUrl` (a rota oficial do registro único, `/api-de-dados/ceis/{id}` ou
`/api-de-dados/cnep/{id}`), tipo da sanção, abrangência, data de início, data de
fim, órgão sancionador com UF e esfera, número do processo, fundamentação legal,
valor da multa e dados de publicação, quando a API devolver cada um.

Mais um registro `SUMMARY` no key value store, com a contagem do lote:
requisitados, checados, flagged, clear, erros, entradas inválidas, ignorados
pelo teto, requisições à API, registros de pessoa física descartados, eventos
cobrados e falhas de cobrança.

## O que a página NÃO pode prometer

Copiar a lista do README, sem suavizar:

- não cobre sanção estadual nem municipal que não alimente um destes quatro
  cadastros federais;
- não cobre a lista de inidôneos do TCU nem nenhuma lista de tribunal;
- não cobre processo judicial, falência, dívida trabalhista, dívida fiscal,
  multa ambiental nem crédito;
- não consulta situação cadastral na Receita Federal;
- `clear` não é certidão de idoneidade e não substitui análise jurídica;
  `flagged` é um ponteiro para um registro público que o comprador ainda tem de
  ler.

## Dado pessoal: o que a página afirma

Os cadastros incluem pessoa física sancionada; os dicionários oficiais da
CGU têm os campos tipo de pessoa, nome e CPF do sancionado. A saída deste Actor
passa por lista de permissão de campos, com duas camadas: campo fora da lista é
descartado, e registro cujo tipo de pessoa é física é descartado inteiro e só
aparece como contagem em `personRecordsDiscarded`. Nome de pessoa, CPF, nome de
sócio e nome de representante legal não chegam ao dataset. Isso é testado em
`tests/test_rules.py`.

## Token: é do comprador, não nosso

O campo de entrada `portalToken` recebe o token gratuito do próprio comprador,
pedido em https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email sem
custo e entregue por e-mail. Correção medida em 20/09/2026: essa página
redireciona para o login do gov.br e exige CPF, então o cadastro não é só um
e-mail, como esta nota dizia antes. Não
temos token e não emprestamos nenhum; a descrição do campo no
`input_schema.json` diz isso com todas as letras. O Actor manda o token só no cabeçalho `chave-api-dados` da
API do Portal, e nunca no dataset, no log ou na query. O campo está marcado
`isSecret` no `input_schema.json`.

## Limites que a página declara, porque estão no código

- Teto de empresas por execução: `maxCnpjs`, padrão 200, máximo 1000.
- Ritmo: no mínimo 0,15 s entre chamadas, ou seja 400 requisições por minuto, o
  menor dos dois limites publicados pelo Portal (400/min das 6h às 23h59,
  700/min das 0h às 5h59). Vale em qualquer hora do dia.
- Quatro requisições por empresa, uma por cadastro, mais uma por página extra de
  resultado. Teto de 40 páginas por consulta.
- CNPJ repetido na entrada é checado e cobrado uma vez só; entrada que não tenha
  14 dígitos é ignorada, listada no resumo e não cobrada.
- Token recusado (HTTP 401 ou 403) não é repetido: a execução para e diz o que
  corrigir.
- Datas saem como a API devolve, em formato brasileiro, sem reformatação.
- Nome de campo que a API devolver e a lista de permissão não conhecer aparece
  em `unmappedKeys`, pelo nome, nunca pelo valor.

## Preço

Pay per event, dois eventos, iguais aos de `.actor/actor.json`:

| evento | preço | quando é cobrado |
|---|---|---|
| `company-checked` | US$ 0.10 | uma vez por CNPJ checado, depois que a linha já está no dataset |
| `batch-report` | US$ 0.25 | uma vez por execução, só quando pelo menos uma empresa foi checada |

Um lote de 20 empresas cobra 20 eventos `company-checked` mais 1 `batch-report`,
ou seja US$ 2,25. Entrada inválida e empresa ignorada pelo teto não são
cobradas. Se a execução bater o limite de cobrança, ela para, guarda tudo o que
já checou e não cobra o `batch-report`.

Referência de preço na prateleira: `brasildados/cnpj-lawsuits-check` cobra
US$ 0,10 por empresa para risco judicial por CNPJ.

O que sustenta o preço é a cobertura, e a página precisa dizer isso em uma
frase: os mesmos US$ 0,10 por empresa cobrem os quatro cadastros federais numa
execução só, com o link oficial de cada registro. Quem consulta só CEIS e CNEP
deixa dois buracos reais: entidade sem fins lucrativos impedida de repasse
federal só aparece no CEPIM, e empresa que negociou acordo com a CGU só aparece
em Acordos de Leniência. Feito à mão, é uma busca por cadastro, por empresa,
sem arquivo de auditoria no fim.

## Atribuição da fonte

Dado do Portal da Transparência, Controladoria-Geral da União (CGU), Governo
Federal do Brasil, publicado como dado aberto sob Creative Commons Attribution e
Decreto 8.777/2016. O Actor reformata esse dado e acrescenta um campo de
veredito; não é documento oficial e não tem endosso da CGU. A atribuição vai em
toda linha do dataset, no campo `dataAttribution`.

## IA declarada

O README diz, e a página repete: este Actor foi construído com ajuda de IA, e
todo módulo tem teste automatizado offline em `tests/` (62 verificações em 6
arquivos, `python tests/run_all.py`, medido em 20/09/2026).

## Ainda não verificado, e o que fazer antes de publicar

1. **Os nomes exatos das chaves JSON de CeisDTO e CnepDTO.** A lista de
   permissão foi escrita contra os dicionários de dados oficiais e os parâmetros
   documentados, porque o `/v3/api-docs` é grande demais para o leitor de página
   e o sandbox do laboratório não alcança o host. Rodar uma vez com token real e
   comparar `unmappedApiKeys` do `SUMMARY` com a lista de `ALLOWED_PATHS`. O
   risco de chave errada é coluna faltando, nunca campo pessoal vazado, porque o
   desconhecido é sempre descartado.
2. Rótulos exatos das categorias no seletor do Console.
3. Preço ainda não configurado na aba Publishing: os valores acima são os do
   `.actor/actor.json`, não uma leitura do Console.
4. Resolvido em 20/09/2026: a fixture `tests/fixtures/portal_responses.json` e o
   log `logs/corrida-local-2026-09-20-quatro-cadastros.log` já cobrem os quatro
   cadastros. Os demais arquivos em `logs/` continuam sendo da versão de dois
   cadastros, e o exemplo de saída do README avisa isso em texto.
5. Resolvido em 20/09/2026: `src/main.py` monta `REGISTRY_URLS` a partir de
   `rules.SOURCES` com `SOURCE_ORDER` (linha 68) e o bloco `sources` do `SUMMARY`
   usa `list(REGISTRY_URLS)` (linha 307), então o resumo da execução declara os
   mesmos quatro cadastros que o Actor lê.
