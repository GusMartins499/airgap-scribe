import dataclasses
import json
import re
from pathlib import Path

import pytest

from airgap_scribe import workdir
from airgap_scribe.config import (
    Config,
    VerifySettings,
    container_config_toml,
    load_config,
    load_container_config,
)
from airgap_scribe.verify import (
    CAPITALIZED_WORD,
    COMMON_FIRST_NAME,
    DETECTED_ENTITY,
    LEFTOVER_MARKER,
    NAMES_LOCATIONS,
    Alert,
    bundled_names,
    names_path,
    run_verify,
    verify_draft,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "pipeline.example.toml"
NAMES = {"ana", "maria", "raquel", "marcelo", "rosa", "domingos"}


def check(draft: str, entities: tuple[str, ...] = (), ignore: tuple[str, ...] = ()) -> list[Alert]:
    return verify_draft(draft, entities, NAMES, ignore)


def test_a_draft_without_names_has_no_alerts() -> None:
    assert check("# Uma conversa\n\nFalamos sobre paciência e tempo.\n") == []


def test_leftover_markers_are_reported_with_their_line() -> None:
    assert check("Título\n\nA [PERSON_1] falou com LOCATION_2 e [ORGANIZATION_3].") == [
        Alert(3, "[PERSON_1]", LEFTOVER_MARKER),
        Alert(3, "LOCATION_2", LEFTOVER_MARKER),
        Alert(3, "[ORGANIZATION_3]", LEFTOVER_MARKER),
    ]


def test_words_of_detected_entities_are_reported_ignoring_case_and_accents() -> None:
    alerts = check("A Luiza voltou para o Sertao.", entities=("Luíza", "Vila Nova do Sertão"))

    assert alerts == [Alert(1, "Luiza", DETECTED_ENTITY), Alert(1, "Sertao", DETECTED_ENTITY)]


def test_transcription_variants_of_detected_entities_are_reported() -> None:
    assert check("O Tiago chegou.", entities=("Thiago",)) == [Alert(1, "Tiago", DETECTED_ENTITY)]


def test_connectors_of_detected_entities_are_not_reported() -> None:
    assert check("Do Norte de lá.", entities=("Vila do Norte de Cima",)) == [
        Alert(1, "Norte", DETECTED_ENTITY)
    ]


def test_lowercase_words_are_not_reported() -> None:
    assert check("uma flor rosa no domingos da maria", entities=("Rosa",)) == []


def test_common_first_names_are_reported() -> None:
    assert check("Depois a Maria contou.") == [Alert(1, "Maria", COMMON_FIRST_NAME)]


def test_detected_entities_take_precedence_over_common_first_names() -> None:
    assert check("A Maria contou.", entities=("Maria",)) == [Alert(1, "Maria", DETECTED_ENTITY)]


@pytest.mark.parametrize("word", ["Raquéu", "Marcelio", "Marclo", "Marcelu", "Raquell"])
def test_names_one_letter_away_from_a_common_first_name_are_reported(word: str) -> None:
    assert check(f"Falei com {word} ontem.") == [Alert(1, word, COMMON_FIRST_NAME)]


@pytest.mark.parametrize("word", ["Ama", "Mara", "Mracleo", "Quando"])
def test_short_words_and_distant_spellings_are_not_near_matches(word: str) -> None:
    assert check(f"{word} chegou.") == []


def test_capitalized_words_right_after_a_reported_name_are_reported_as_surnames() -> None:
    alerts = check("A Ana Cavalcanti Rezende e a Luiza Souza. Eu, Ana. Depois Souza.", ("Luiza",))

    assert alerts == [
        Alert(1, "Ana", COMMON_FIRST_NAME),
        Alert(1, "Cavalcanti", COMMON_FIRST_NAME),
        Alert(1, "Rezende", COMMON_FIRST_NAME),
        Alert(1, "Luiza", DETECTED_ENTITY),
        Alert(1, "Souza", DETECTED_ENTITY),
        Alert(1, "Ana", COMMON_FIRST_NAME),
        Alert(1, "Souza", CAPITALIZED_WORD),
    ]


def test_lowercase_or_short_words_after_a_name_are_not_surnames() -> None:
    assert check("A Ana de Ou souza.") == [Alert(1, "Ana", COMMON_FIRST_NAME)]


def test_capitalized_words_in_the_middle_of_a_sentence_are_reported() -> None:
    assert check("Quem falou foi o Wellington, junto com a talita.") == [
        Alert(1, "Wellington", CAPITALIZED_WORD)
    ]


@pytest.mark.parametrize(
    "draft",
    [
        "Hoje falamos de espera.",
        "Foi longo. Depois veio a pausa.",
        "Falante 1: Bom dia a todos.",
        "## Temas da conversa",
        "- Primeiro ponto",
        "1. Segundo ponto",
        "> Citação inicial",
        'Ela disse: "Calma que passa."',
        "(Silêncio na sala)",
    ],
)
def test_capitalized_words_that_start_a_sentence_are_not_reported(draft: str) -> None:
    assert check(draft) == []


def test_acronyms_and_short_capitalized_words_are_not_reported() -> None:
    assert check("O processo passa pelo SNA e pela vara, diz o Eu.") == []


def test_ignore_list_suppresses_words_ignoring_case_and_accents() -> None:
    alerts = check(
        "Domingos é dia de festa. A Rosa Lima veio.",
        entities=("Rosa",),
        ignore=("DOMINGOS", "rósa", "lima"),
    )

    assert alerts == []


def test_markers_do_not_hide_names_next_to_them() -> None:
    assert check("[PERSON_1]Maria") == [
        Alert(1, "[PERSON_1]", LEFTOVER_MARKER),
        Alert(1, "Maria", COMMON_FIRST_NAME),
    ]


@pytest.fixture
def container_config(tmp_path: Path) -> Config:
    path = tmp_path / "container.toml"
    path.write_text(container_config_toml(load_config(EXAMPLE_CONFIG)), encoding="utf-8")
    config = load_container_config(path)
    return dataclasses.replace(config, verify=VerifySettings(ignore=("Domingos",)))


def test_run_verify_reads_the_work_dir_and_writes_alerts(
    tmp_path: Path, container_config: Config
) -> None:
    (tmp_path / workdir.DRAFT).write_text(
        "---\ntitle: Conversa\n---\n\nA Bruna e a Maria, no Domingos, e o [PERSON_2].\n",
        encoding="utf-8",
    )
    (tmp_path / workdir.ENTITIES).write_text(json.dumps(["Bruna"]), encoding="utf-8")

    alerts = run_verify(tmp_path, container_config)

    assert alerts == [
        Alert(5, "[PERSON_2]", LEFTOVER_MARKER),
        Alert(5, "Bruna", DETECTED_ENTITY),
        Alert(5, "Maria", COMMON_FIRST_NAME),
    ]
    assert json.loads((tmp_path / workdir.ALERTS).read_text(encoding="utf-8")) == [
        {"line": 5, "token": "[PERSON_2]", "reason": LEFTOVER_MARKER},
        {"line": 5, "token": "Bruna", "reason": DETECTED_ENTITY},
        {"line": 5, "token": "Maria", "reason": COMMON_FIRST_NAME},
    ]


def test_run_verify_writes_an_empty_list_for_a_clean_draft(
    tmp_path: Path, container_config: Config
) -> None:
    (tmp_path / workdir.DRAFT).write_text("Uma conversa tranquila.\n", encoding="utf-8")
    (tmp_path / workdir.ENTITIES).write_text("[]", encoding="utf-8")

    assert run_verify(tmp_path, container_config) == []
    assert json.loads((tmp_path / workdir.ALERTS).read_text(encoding="utf-8")) == []


def test_bundled_names_are_normalized_common_first_names() -> None:
    names = bundled_names()

    assert {"maria", "jose", "ana", "joao"} <= names
    assert all(re.fullmatch(r"[a-z]+", name) for name in names)


def test_bundled_name_file_is_sorted_unique_and_one_name_per_line() -> None:
    lines = names_path().read_text(encoding="utf-8").splitlines()

    assert lines == sorted(set(lines))
    assert all(re.fullmatch(r"[a-z]+", line) for line in lines)


def test_names_path_prefers_the_installed_package_copy(tmp_path: Path) -> None:
    installed = tmp_path / "installed.txt"
    source = tmp_path / "source.txt"
    installed.write_text("ana\n", encoding="utf-8")
    source.write_text("ana\n", encoding="utf-8")

    assert names_path((installed, source)) == installed


def test_names_path_falls_back_to_the_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("ana\n", encoding="utf-8")

    assert names_path((tmp_path / "missing.txt", source)) == source


def test_names_path_fails_clearly_when_the_list_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="first-name list"):
        names_path((tmp_path / "missing.txt",))


def test_source_tree_location_points_at_the_repository_data_dir() -> None:
    assert REPO_ROOT / "data" / "names" / "first-names.txt" in NAMES_LOCATIONS
