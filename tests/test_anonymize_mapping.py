import pytest

from airgap_scribe.anonymize.mapping import (
    MarkerRegistry,
    entity_key,
    fold,
    is_capitalized,
    is_significant,
    marker_prefix,
    significant_words,
    spelling_key,
    word_index,
)


def test_fold_ignores_case_and_accents() -> None:
    assert fold("JOÃO Antônio Conceição") == "joao antonio conceicao"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Thiago", "Tiago"),
        ("Luíza", "Luisa"),
        ("Yasmin", "Iasmin"),
        ("Rafaella", "Rafaela"),
        ("Wellington", "Vellington"),
        ("Heloísa", "Eloisa"),
        ("Kauã", "Caua"),
        ("Phelipe", "Felipe"),
    ],
)
def test_spelling_key_matches_common_transcription_variants(first: str, second: str) -> None:
    assert spelling_key(first) == spelling_key(second)


def test_spelling_key_keeps_different_names_apart() -> None:
    assert spelling_key("Mário") != spelling_key("Maria")


def test_entity_key_ignores_punctuation_case_and_spacing() -> None:
    assert entity_key("  Ana   PAULA, ") == entity_key("ana paula")


@pytest.mark.parametrize("word", ["de", "da", "dos", "ele", "Eu", "Lu"])
def test_short_words_and_stop_words_are_not_significant(word: str) -> None:
    assert not is_significant(word)


def test_names_are_significant() -> None:
    assert is_significant("Ana")
    assert is_significant("Sertão")


def test_significant_words_drop_connectors_of_compound_names() -> None:
    assert significant_words("Maria das Dores de Souza") == ["Maria", "Dores", "Souza"]


def test_is_capitalized_checks_the_first_letter() -> None:
    assert is_capitalized("Ana")
    assert not is_capitalized("ana")
    assert not is_capitalized("")


@pytest.mark.parametrize(
    ("label", "prefix"),
    [
        ("PER", "PERSON"),
        ("per", "PERSON"),
        ("PERSON", "PERSON"),
        ("LOC", "LOCATION"),
        ("GPE", "LOCATION"),
        ("ORG", "ORGANIZATION"),
        ("misc", "MISC"),
    ],
)
def test_marker_prefix_names_the_label(label: str, prefix: str) -> None:
    assert marker_prefix(label) == prefix


def test_registry_numbers_markers_per_prefix() -> None:
    registry = MarkerRegistry()

    assert registry.marker_for("Ana", "PER") == "[PERSON_1]"
    assert registry.marker_for("Vila Aurora", "LOC") == "[LOCATION_1]"
    assert registry.marker_for("Bruno", "PER") == "[PERSON_2]"


def test_registry_reuses_the_marker_for_the_same_entity_in_any_case_or_accent() -> None:
    registry = MarkerRegistry()

    first = registry.marker_for("Luíza", "PER")

    assert registry.marker_for("LUIZA", "PER") == first
    assert registry.marker_for("Luisa", "PER") == first


def test_registry_keeps_the_first_label_for_an_entity() -> None:
    registry = MarkerRegistry()

    first = registry.marker_for("Thiago", "LOC")

    assert registry.marker_for("Thiago", "PER") == first


def test_registry_maps_markers_to_the_first_surface_and_lists_every_surface() -> None:
    registry = MarkerRegistry()
    registry.marker_for("Luíza", "PER")
    registry.marker_for("luiza", "PER")
    registry.record("Lu")
    registry.record("Lu")

    assert registry.mapping == {"[PERSON_1]": "Luíza"}
    assert registry.entities() == ["Luíza", "luiza", "Lu"]


def test_word_index_keeps_the_first_marker_and_skips_insignificant_words() -> None:
    index = word_index([("Ana", "[PERSON_1]"), ("de", "[PERSON_1]"), ("Ana", "[PERSON_2]")])

    assert index == {spelling_key("Ana"): "[PERSON_1]"}
