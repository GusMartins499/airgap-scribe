import argparse
import gzip
import json
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

API_URL = "https://servicodados.ibge.gov.br/api/v2/censos/nomes/ranking"
MUNICIPALITIES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"
STATE_CODES = [
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "21",
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "28",
    "29",
    "31",
    "32",
    "33",
    "35",
    "41",
    "42",
    "43",
    "50",
    "51",
    "52",
    "53",
]
LOCATIONS = ("BR", *STATE_CODES)
DECADES = (None, 1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000, 2010)
SEXES = (None, "m", "f")
MUNICIPALITY_SEXES = ("m", "f")
WORKERS = 4
RETRIES = 4
GZIP_MAGIC = b"\x1f\x8b"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "names" / "first-names.txt"

Fetch = Callable[[str], Any]


def ranking_url(location: str, decade: int | None, sex: str | None) -> str:
    params = {"localidade": location}
    if decade is not None:
        params["decada"] = str(decade)
    if sex is not None:
        params["sexo"] = sex
    return f"{API_URL}?{urllib.parse.urlencode(params)}"


def ranking_urls(municipalities: Sequence[str]) -> Iterator[str]:
    for location in LOCATIONS:
        for decade in DECADES:
            for sex in SEXES:
                yield ranking_url(location, decade, sex)
    for municipality in municipalities:
        for sex in MUNICIPALITY_SEXES:
            yield ranking_url(municipality, None, sex)


def municipality_ids(payload: Any) -> list[str]:
    return sorted(str(entry["municipio-id"]) for entry in payload)


def normalize_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.strip().lower())
    return "".join(char for char in decomposed if "a" <= char <= "z")


def names_in_response(payload: Any) -> set[str]:
    names = set()
    for group in payload:
        for entry in group.get("res", []):
            name = normalize_name(entry["nome"])
            if name:
                names.add(name)
    return names


def build_names(fetch: Fetch, urls: Sequence[str]) -> list[str]:
    names: set[str] = set()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for payload in pool.map(fetch, urls):
            names |= names_in_response(payload)
    return sorted(names)


def decode_body(body: bytes) -> Any:
    if body[:2] == GZIP_MAGIC:
        body = gzip.decompress(body)
    return json.loads(body)


def http_fetch(url: str, delay: float = 0.1) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "airgap-scribe-names-builder"})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = decode_body(response.read())
            break
        except OSError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(2**attempt)
    time.sleep(delay)
    return payload


def main(argv: Sequence[str] | None = None, fetch: Fetch = http_fetch) -> int:
    parser = argparse.ArgumentParser(
        description="Build the bundled first-name list from the IBGE 2010 census names API."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    municipalities = municipality_ids(fetch(MUNICIPALITIES_URL))
    names = build_names(fetch, list(ranking_urls(municipalities)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(f"{name}\n" for name in names), encoding="utf-8")
    print(f"wrote {len(names)} names to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
