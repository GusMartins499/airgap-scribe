from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class AnonymizationResult:
    text: str
    mapping: dict[str, str]
    entities: list[str]


def anonymize_text(text: str, model: str, labels: Sequence[str]) -> AnonymizationResult:
    raise NotImplementedError
