import re
import unicodedata
from collections.abc import Iterable

from spacy.lang.pt.stop_words import STOP_WORDS

WORD_PATTERN = re.compile(r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*")
MIN_SIGNIFICANT_LENGTH = 3
LABEL_PREFIXES = {
    "PER": "PERSON",
    "PERSON": "PERSON",
    "LOC": "LOCATION",
    "GPE": "LOCATION",
    "ORG": "ORGANIZATION",
}
SPELLING_VARIANTS = (
    ("ph", "f"),
    ("th", "t"),
    ("y", "i"),
    ("w", "v"),
    ("k", "c"),
    ("z", "s"),
)


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def spelling_key(word: str) -> str:
    key = fold(word)
    for written, spoken in SPELLING_VARIANTS:
        key = key.replace(written, spoken)
    key = re.sub(r"(.)\1+", r"\1", key)
    return key.removeprefix("h")


def entity_key(surface: str) -> str:
    return " ".join(spelling_key(word) for word in WORD_PATTERN.findall(surface))


def is_significant(word: str) -> bool:
    folded = fold(word)
    return len(folded) >= MIN_SIGNIFICANT_LENGTH and folded not in STOP_WORDS


def significant_words(surface: str) -> list[str]:
    return [word for word in WORD_PATTERN.findall(surface) if is_significant(word)]


def is_capitalized(word: str) -> bool:
    return word[:1].isupper()


def marker_prefix(label: str) -> str:
    return LABEL_PREFIXES.get(label.upper(), label.upper())


class MarkerRegistry:
    def __init__(self) -> None:
        self._markers: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self.mapping: dict[str, str] = {}
        self._entities: dict[str, None] = {}

    def marker_for(self, surface: str, label: str) -> str:
        key = entity_key(surface)
        marker = self._markers.get(key)
        if marker is None:
            prefix = marker_prefix(label)
            self._counts[prefix] = self._counts.get(prefix, 0) + 1
            marker = f"[{prefix}_{self._counts[prefix]}]"
            self._markers[key] = marker
            self.mapping[marker] = surface
        self.record(surface)
        return marker

    def record(self, surface: str) -> None:
        self._entities.setdefault(surface, None)

    def entities(self) -> list[str]:
        return list(self._entities)


def word_index(words: Iterable[tuple[str, str]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for word, marker in words:
        if is_significant(word):
            index.setdefault(spelling_key(word), marker)
    return index
