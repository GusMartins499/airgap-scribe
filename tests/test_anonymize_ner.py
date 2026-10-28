from collections.abc import Iterator
from pathlib import Path

import pytest
import spacy

from airgap_scribe.anonymize import ner


@pytest.fixture(scope="module")
def rule_model(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    pipeline = spacy.blank("pt")
    ruler = pipeline.add_pipe("entity_ruler")
    ruler.add_patterns(
        [
            {"label": "PER", "pattern": "Ana Souza"},
            {"label": "LOC", "pattern": "Vila Aurora"},
            {"label": "ORG", "pattern": "Clube Azul"},
        ]
    )
    path: Path = tmp_path_factory.mktemp("model") / "rules"
    pipeline.to_disk(path)
    yield str(path)
    ner.load_pipeline.cache_clear()


def test_find_entities_returns_character_spans_for_the_wanted_labels(rule_model: str) -> None:
    texts = ["A Ana Souza mora na Vila Aurora.", "Ninguém foi ao Clube Azul.", ""]

    assert ner.find_entities(texts, rule_model, ["PER", "LOC"]) == [
        [(2, 11, "PER"), (20, 31, "LOC")],
        [],
        [],
    ]


def test_find_entities_keeps_other_labels_when_configured(rule_model: str) -> None:
    assert ner.find_entities(["Clube Azul"], rule_model, ["ORG"]) == [[(0, 10, "ORG")]]


def test_pipeline_is_loaded_once_per_model(rule_model: str) -> None:
    assert ner.load_pipeline(rule_model) is ner.load_pipeline(rule_model)
