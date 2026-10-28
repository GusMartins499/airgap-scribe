import re
from collections.abc import Sequence

import pytest

import airgap_scribe.anonymize as anonymize
from airgap_scribe.anonymize import AnonymizationResult, anonymize_text

MODEL = "fake-model"
LABELS = ("PER", "LOC")


class FakeRecognizer:
    def __init__(self, entities: dict[str, str]) -> None:
        self.entities = entities
        self.texts: list[str] = []
        self.models: list[str] = []

    def __call__(
        self, texts: Sequence[str], model: str, labels: Sequence[str]
    ) -> list[list[tuple[int, int, str]]]:
        self.texts.extend(texts)
        self.models.append(model)
        return [self._spans(text, labels) for text in texts]

    def _spans(self, text: str, labels: Sequence[str]) -> list[tuple[int, int, str]]:
        spans = []
        for surface, label in self.entities.items():
            if label in labels:
                pattern = rf"(?<!\w){re.escape(surface)}(?!\w)"
                spans.extend((m.start(), m.end(), label) for m in re.finditer(pattern, text))
        return sorted(spans)


def run(
    monkeypatch: pytest.MonkeyPatch, text: str, entities: dict[str, str]
) -> AnonymizationResult:
    monkeypatch.setattr(anonymize, "find_entities", FakeRecognizer(entities))
    return anonymize_text(text, MODEL, LABELS)


def test_text_without_entities_is_returned_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Uma conversa sem nomes.\n\nOutra linha.\n"

    result = run(monkeypatch, text, {})

    assert result == AnonymizationResult(text=text, mapping={}, entities=[])


def test_people_and_places_become_numbered_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run(
        monkeypatch,
        "A Ana mora na Vila Aurora e o Bruno mora em Lagoa Serena.",
        {"Ana": "PER", "Bruno": "PER", "Vila Aurora": "LOC", "Lagoa Serena": "LOC"},
    )

    assert result.text == "A [PERSON_1] mora na [LOCATION_1] e o [PERSON_2] mora em [LOCATION_2]."
    assert result.mapping == {
        "[PERSON_1]": "Ana",
        "[LOCATION_1]": "Vila Aurora",
        "[PERSON_2]": "Bruno",
        "[LOCATION_2]": "Lagoa Serena",
    }


def test_same_entity_gets_the_same_marker_regardless_of_case_and_accents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(
        monkeypatch,
        "Falei com a Luíza.\nDepois a LUIZA voltou e a luiza riu.",
        {"Luíza": "PER", "LUIZA": "PER", "luiza": "PER"},
    )

    assert result.text == "Falei com a [PERSON_1].\nDepois a [PERSON_1] voltou e a [PERSON_1] riu."
    assert result.mapping == {"[PERSON_1]": "Luíza"}
    assert result.entities == ["Luíza", "LUIZA", "luiza"]


def test_later_standalone_first_names_are_replaced_with_the_person_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(
        monkeypatch,
        "Speaker 1: Hoje a Ana Paula veio.\nSpeaker 2: Que bom, Paula, e a Ana?",
        {"Ana Paula": "PER"},
    )

    assert result.text == (
        "Speaker 1: Hoje a [PERSON_1] veio.\nSpeaker 2: Que bom, [PERSON_1], e a [PERSON_1]?"
    )
    assert result.entities == ["Ana Paula", "Paula", "Ana"]


def test_standalone_first_names_with_transcription_variants_are_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(monkeypatch, "O Thiago chegou. Depois o Tiago saiu.", {"Thiago": "PER"})

    assert result.text == "O [PERSON_1] chegou. Depois o [PERSON_1] saiu."
    assert "Tiago" in result.entities


def test_lowercase_words_that_match_a_first_name_are_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(monkeypatch, "A Rosa trouxe uma flor rosa.", {"Rosa": "PER"})

    assert result.text == "A [PERSON_1] trouxe uma flor rosa."


def test_words_of_place_names_are_not_replaced_on_their_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(monkeypatch, "Fomos a Vila Aurora. A Vila estava cheia.", {"Vila Aurora": "LOC"})

    assert result.text == "Fomos a [LOCATION_1]. A Vila estava cheia."


def test_connectors_inside_person_names_are_not_propagated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(
        monkeypatch, "A Maria das Dores veio. Das sete às oito.", {"Maria das Dores": "PER"}
    )

    assert result.text == "A [PERSON_1] veio. Das sete às oito."


def test_capitalized_surnames_after_a_person_marker_are_absorbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(
        monkeypatch,
        "Agradeço a Ana Cavalcanti Rezende e a Ana Souza de novo.",
        {"Ana": "PER"},
    )

    assert result.text == "Agradeço a [PERSON_1] e a [PERSON_1] de novo."
    assert result.entities == ["Ana", "Cavalcanti", "Rezende", "Souza"]


def test_lowercase_or_short_words_after_a_person_marker_are_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(monkeypatch, "A Ana Eu sei. A Ana falou.", {"Ana": "PER"})

    assert result.text == "A [PERSON_1] Eu sei. A [PERSON_1] falou."


def test_speaker_labels_are_preserved_and_never_sent_to_the_recognizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recognizer = FakeRecognizer({"Speaker": "PER", "SPEAKER": "PER", "Ana": "PER"})
    monkeypatch.setattr(anonymize, "find_entities", recognizer)

    result = anonymize_text(
        "Speaker 1: Oi, Ana.\nSPEAKER_02: Oi.\n  Falante 3 : Tudo bem?\nTexto livre.\n",
        MODEL,
        LABELS,
    )

    assert result.text == (
        "Speaker 1: Oi, [PERSON_1].\nSPEAKER_02: Oi.\n  Falante 3 : Tudo bem?\nTexto livre.\n"
    )
    assert recognizer.texts == ["Oi, Ana.", "Oi.", "Tudo bem?", "Texto livre.", ""]
    assert recognizer.models == [MODEL]


def test_entities_of_unconfigured_labels_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run(monkeypatch, "O Clube Azul abriu.", {"Clube Azul": "ORG"})

    assert result.text == "O Clube Azul abriu."


def test_other_configured_labels_get_their_own_marker_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(anonymize, "find_entities", FakeRecognizer({"Clube Azul": "ORG"}))

    result = anonymize_text("O Clube Azul abriu.", MODEL, ["ORG"])

    assert result.text == "O [ORGANIZATION_1] abriu."
