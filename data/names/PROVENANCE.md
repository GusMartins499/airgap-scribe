# First-name list provenance

`first-names.txt` is the list of common Brazilian first names used by `airgap-scribe verify`
to flag names that may have survived anonymization in a draft.

## Source

- Publisher: IBGE (Instituto Brasileiro de Geografia e Estatística), *Nomes no Brasil*,
  first-name frequencies from the 2010 Demographic Census.
- API: `https://servicodados.ibge.gov.br/api/v2/censos/nomes/ranking`
  (documentation: https://servicodados.ibge.gov.br/api/docs/nomes?versao=2).
- Municipality codes: `https://servicodados.ibge.gov.br/api/v1/localidades/municipios`.
- Retrieved: 2026-10-28.

## Terms of use

IBGE allows third parties to reuse data it publishes, as long as IBGE is cited as the source.
Its library service terms (https://biblioteca.ibge.gov.br/biblioteca-servicos.html, section
"Autorização de uso de imagens e dados", checked on 2026-10-28) state that selected data from
IBGE publications, presented as text, databases, tables, charts, maps or images, may be used
by third parties in publications, citing IBGE as the source; when the data is transformed,
whoever performed the transformation must be identified.

In addition, a list of first names is factual data; Brazilian copyright law (Lei 9.610/1998,
art. 8) does not protect facts or information as such. The list is redistributed here with
the code (MIT) under those terms:

> Source: IBGE, Censo Demográfico 2010, *Nomes no Brasil*. Transformation (selection of
> ranked names, lowercase and accent removal, deduplication) by the airgap-scribe
> contributors with `scripts/build-names-list.py`.

## What the list contains

The union of the top-20 ranking returned by the API for:

- Brazil and each of the 27 federative units, for every decade the API offers
  (all births, 1930 to 2010) and for both sexes combined, male only and female only;
- every municipality, male only and female only (all decades).

Each name is lowercased, stripped of accents and of any non-letter character, deduplicated
and sorted, one name per line. The file holds NAME_COUNT names.

## Regenerating

```sh
uv run python scripts/build-names-list.py
```

The script uses only the Python standard library, queries the API with four parallel
workers and retries transient network errors. It writes `data/names/first-names.txt`
(`--output` changes the destination). The API serves census data that does not change,
so a rerun should produce the same file; review the diff before committing a regenerated
list and update the retrieval date above.

## Packaging

`pyproject.toml` force-includes the file into the wheel as
`airgap_scribe/data/names/first-names.txt`; `verify.py` reads that copy when the package is
installed and falls back to `data/names/first-names.txt` in a source checkout.
