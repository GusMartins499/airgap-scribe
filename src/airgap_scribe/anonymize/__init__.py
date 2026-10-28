import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import takewhile

from airgap_scribe.anonymize.mapping import (
    WORD_PATTERN,
    MarkerRegistry,
    is_capitalized,
    is_significant,
    marker_prefix,
    significant_words,
    spelling_key,
    word_index,
)
from airgap_scribe.anonymize.ner import EntitySpan, find_entities

SPEAKER_LABEL = re.compile(r"^\s*[^\W\d_]+[ _-]?\d+\s*:\s*")
PERSON_MARKER_WITH_FOLLOWERS = re.compile(r"(\[PERSON_\d+\])((?: [^\W\d_]+)+)")
PERSON_PREFIX = "PERSON"


@dataclass(frozen=True)
class AnonymizationResult:
    text: str
    mapping: dict[str, str]
    entities: list[str]


def anonymize_text(text: str, model: str, labels: Sequence[str]) -> AnonymizationResult:
    lines = text.split("\n")
    labels_end = [_speaker_label_length(line) for line in lines]
    bodies = [line[end:] for line, end in zip(lines, labels_end, strict=True)]
    spans = find_entities(bodies, model, labels)
    registry = MarkerRegistry()
    person_words = []
    for body, line_spans in zip(bodies, spans, strict=True):
        for start, end, label in line_spans:
            surface = body[start:end]
            marker = registry.marker_for(surface, label)
            if marker_prefix(label) == PERSON_PREFIX:
                person_words.extend((word, marker) for word in significant_words(surface))
    index = word_index(person_words)
    anonymized = [
        line[:end] + _rewrite(body, line_spans, registry, index)
        for line, end, body, line_spans in zip(lines, labels_end, bodies, spans, strict=True)
    ]
    return AnonymizationResult(
        text="\n".join(anonymized),
        mapping=dict(registry.mapping),
        entities=registry.entities(),
    )


def _speaker_label_length(line: str) -> int:
    match = SPEAKER_LABEL.match(line)
    return match.end() if match else 0


def _rewrite(
    body: str, spans: list[EntitySpan], registry: MarkerRegistry, index: dict[str, str]
) -> str:
    pieces = []
    cursor = 0
    for start, end, label in sorted(spans):
        pieces.append(_replace_words(body[cursor:start], registry, index))
        pieces.append(registry.marker_for(body[start:end], label))
        cursor = end
    pieces.append(_replace_words(body[cursor:], registry, index))
    return _absorb_surnames("".join(pieces), registry)


def _absorb_surnames(text: str, registry: MarkerRegistry) -> str:
    def absorb(match: re.Match[str]) -> str:
        followers = match.group(2).split(" ")[1:]
        surnames = list(takewhile(_looks_like_surname, followers))
        for surname in surnames:
            registry.record(surname)
        kept = followers[len(surnames) :]
        return " ".join([match.group(1), *kept])

    return PERSON_MARKER_WITH_FOLLOWERS.sub(absorb, text)


def _looks_like_surname(word: str) -> bool:
    return is_capitalized(word) and is_significant(word)


def _replace_words(segment: str, registry: MarkerRegistry, index: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        marker = index.get(spelling_key(word)) if is_capitalized(word) else None
        if marker is None:
            return word
        registry.record(word)
        return marker

    return WORD_PATTERN.sub(replace, segment)
