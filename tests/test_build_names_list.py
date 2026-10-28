import gzip
import importlib.util
import json
import urllib.parse
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build-names-list.py"


@pytest.fixture(scope="module")
def builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_names_list", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def query(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def test_ranking_url_only_sends_the_given_filters(builder: ModuleType) -> None:
    assert query(builder.ranking_url("BR", None, None)) == {"localidade": "BR"}
    assert query(builder.ranking_url("33", 1950, "f")) == {
        "localidade": "33",
        "decada": "1950",
        "sexo": "f",
    }


def test_ranking_urls_cover_country_states_decades_sexes_and_municipalities(
    builder: ModuleType,
) -> None:
    urls = list(builder.ranking_urls(["3304557"]))

    assert len(urls) == 28 * 10 * 3 + 2
    assert query(urls[-1]) == {"localidade": "3304557", "sexo": "f"}
    assert len(set(urls)) == len(urls)


def test_municipality_ids_are_sorted_strings(builder: ModuleType) -> None:
    payload = [{"municipio-id": 3550308}, {"municipio-id": 1100015}]

    assert builder.municipality_ids(payload) == ["1100015", "3550308"]


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("MARIA", "maria"), ("JOÃO", "joao"), (" Conceição ", "conceicao"), ("D'ARC", "darc")],
)
def test_names_are_lowercase_ascii(builder: ModuleType, raw: str, normalized: str) -> None:
    assert builder.normalize_name(raw) == normalized


def test_names_in_response_reads_every_ranking_group(builder: ModuleType) -> None:
    payload = [
        {"localidade": "BR", "res": [{"nome": "MARIA"}, {"nome": "JOSE"}]},
        {"localidade": "33", "res": [{"nome": "MARIA"}, {"nome": "123"}]},
        {"localidade": "99"},
    ]

    assert builder.names_in_response(payload) == {"maria", "jose"}


def test_build_names_merges_rankings_sorted_and_unique(builder: ModuleType) -> None:
    responses = {
        "a": [{"res": [{"nome": "PEDRO"}, {"nome": "ANA"}]}],
        "b": [{"res": [{"nome": "ANA"}, {"nome": "BRUNO"}]}],
        "c": [],
    }

    assert builder.build_names(responses.__getitem__, ["a", "b", "c"]) == [
        "ana",
        "bruno",
        "pedro",
    ]


def test_decode_body_accepts_plain_and_gzipped_json(builder: ModuleType) -> None:
    body = json.dumps([{"res": []}]).encode()

    assert builder.decode_body(body) == [{"res": []}]
    assert builder.decode_body(gzip.compress(body)) == [{"res": []}]


def test_main_writes_one_name_per_line(builder: ModuleType, tmp_path: Path) -> None:
    def fetch(url: str) -> Any:
        if url == builder.MUNICIPALITIES_URL:
            return [{"municipio-id": 3304557}]
        if query(url) == {"localidade": "3304557", "sexo": "m"}:
            return [{"res": [{"nome": "JOÃO"}]}]
        return [{"res": [{"nome": "MARIA"}]}]

    output = tmp_path / "names" / "first-names.txt"

    assert builder.main(["--output", str(output)], fetch=fetch) == 0
    assert output.read_text(encoding="utf-8") == "joao\nmaria\n"


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_http_fetch_retries_transient_network_errors(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = []

    def urlopen(request: Any, timeout: int) -> FakeResponse:
        attempts.append(request.full_url)
        if len(attempts) < 3:
            raise OSError("connection reset")
        return FakeResponse(b"[]")

    monkeypatch.setattr(builder.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(builder.time, "sleep", lambda seconds: None)

    assert builder.http_fetch("https://example.invalid/ranking") == []
    assert len(attempts) == 3


def test_http_fetch_gives_up_after_the_last_retry(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def urlopen(request: Any, timeout: int) -> FakeResponse:
        raise OSError("unreachable")

    monkeypatch.setattr(builder.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(builder.time, "sleep", lambda seconds: None)

    with pytest.raises(OSError, match="unreachable"):
        builder.http_fetch("https://example.invalid/ranking")
