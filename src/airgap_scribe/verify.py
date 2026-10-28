import dataclasses
import json
import re
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from airgap_scribe import workdir
from airgap_scribe.anonymize.mapping import (
    WORD_PATTERN,
    fold,
    is_capitalized,
    is_significant,
    significant_words,
    spelling_key,
)
from airgap_scribe.config import Config

DETECTED_ENTITY = "detected-entity"
COMMON_FIRST_NAME = "common-first-name"
LEFTOVER_MARKER = "leftover-marker"
CAPITALIZED_WORD = "capitalized-word"
SENTENCE_START = re.compile(r"(?:^|[.!?:;\"“(\[]|^\s*(?:[#>*+-]+|\d+[.)])\s*)\s*$")
MARKER_PATTERN = re.compile(r"\[?\b[A-Z]{2,}_\d+\b\]?")
NEAR_MATCH_MIN_LENGTH = 5
NAMES_FILE = Path("data") / "names" / "first-names.txt"
PACKAGE_DIR = Path(__file__).resolve().parent
NAMES_LOCATIONS = (PACKAGE_DIR / NAMES_FILE, PACKAGE_DIR.parents[1] / NAMES_FILE)


@dataclass(frozen=True)
class Alert:
    line: int
    token: str
    reason: str


def verify_draft(
    draft: str,
    entities: Sequence[str],
    names: Collection[str],
    ignore: Collection[str],
) -> list[Alert]:
    entity_keys = {spelling_key(word) for entity in entities for word in significant_words(entity)}
    name_matcher = NearSpellingMatcher(spelling_key(name) for name in names)
    ignored = {fold(word) for phrase in ignore for word in WORD_PATTERN.findall(phrase)}
    alerts = []
    for number, line in enumerate(draft.splitlines(), start=1):
        alerts.extend(
            Alert(number, marker, LEFTOVER_MARKER) for marker in MARKER_PATTERN.findall(line)
        )
        previous: tuple[int, str] | None = None
        text = MARKER_PATTERN.sub(" ", line)
        for match in WORD_PATTERN.finditer(text):
            word = match.group(0)
            reason = None
            if fold(word) not in ignored:
                reason = (
                    _reason(word, entity_keys, name_matcher)
                    or _surname_reason(text, match, previous)
                    or _capitalized_reason(text, match)
                )
            previous = None if reason is None else (match.end(), reason)
            if reason is not None:
                alerts.append(Alert(number, word, reason))
    return alerts


def run_verify(work_dir: Path, config: Config) -> list[Alert]:
    draft = (work_dir / workdir.DRAFT).read_text(encoding="utf-8")
    entities = json.loads((work_dir / workdir.ENTITIES).read_text(encoding="utf-8"))
    alerts = verify_draft(draft, entities, bundled_names(), config.verify.ignore)
    (work_dir / workdir.ALERTS).write_text(
        json.dumps([dataclasses.asdict(alert) for alert in alerts], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return alerts


@cache
def bundled_names() -> frozenset[str]:
    return frozenset(names_path().read_text(encoding="utf-8").split())


def names_path(locations: Sequence[Path] = NAMES_LOCATIONS) -> Path:
    for location in locations:
        if location.is_file():
            return location
    raise FileNotFoundError(f"bundled first-name list not found in {[str(p) for p in locations]}")


def _reason(word: str, entity_keys: set[str], name_matcher: "NearSpellingMatcher") -> str | None:
    if not is_capitalized(word):
        return None
    key = spelling_key(word)
    if key in entity_keys:
        return DETECTED_ENTITY
    if name_matcher.matches(key):
        return COMMON_FIRST_NAME
    return None


def _surname_reason(
    text: str, match: re.Match[str], previous: tuple[int, str] | None
) -> str | None:
    if previous is None or text[previous[0] : match.start()] != " ":
        return None
    word = match.group(0)
    return previous[1] if is_capitalized(word) and is_significant(word) else None


def _capitalized_reason(text: str, match: re.Match[str]) -> str | None:
    word = match.group(0)
    if not is_capitalized(word) or word.isupper() or not is_significant(word):
        return None
    if SENTENCE_START.search(text[: match.start()]):
        return None
    return CAPITALIZED_WORD


class NearSpellingMatcher:
    def __init__(self, keys: Iterable[str]) -> None:
        self._keys = set(keys)
        self._deletions: dict[str, set[int]] = {}
        for key in self._keys:
            if len(key) >= NEAR_MATCH_MIN_LENGTH:
                for position, shorter in _deletions(key):
                    self._deletions.setdefault(shorter, set()).add(position)

    def matches(self, key: str) -> bool:
        if key in self._keys:
            return True
        if len(key) < NEAR_MATCH_MIN_LENGTH:
            return False
        if key in self._deletions:
            return True
        return any(
            shorter in self._keys or position in self._deletions.get(shorter, ())
            for position, shorter in _deletions(key)
        )


def _deletions(key: str) -> Iterator[tuple[int, str]]:
    for position in range(len(key)):
        yield position, key[:position] + key[position + 1 :]
