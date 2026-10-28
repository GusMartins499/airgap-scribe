from collections import Counter
from dataclasses import dataclass

import pytest

from airgap_scribe.anonymize import anonymize_text
from airgap_scribe.verify import bundled_names, verify_draft

from .corpus import MISSPELLINGS, SyntheticTranscript, generate_corpus, word_occurrences

pytestmark = pytest.mark.privacy

SPACY_MODEL = "pt_core_news_lg"
LABELS = ("PER", "LOC")
MINIMUM_TRANSCRIPTS = 200
MINIMUM_ANONYMIZER_REMOVAL = 0.95


@dataclass(frozen=True)
class PrivacyMetrics:
    transcripts: int
    names: int
    removed: int
    flagged: int
    unflagged: tuple[tuple[str, int, str], ...]

    @property
    def removed_by_anonymizer(self) -> float:
        return self.removed / self.names

    @property
    def removed_or_flagged(self) -> float:
        return (self.removed + self.flagged) / self.names


def word_occurrences_by_word(transcript: SyntheticTranscript) -> Counter[str]:
    occurrences = word_occurrences(transcript.text, transcript.sensitive_words)
    totals: Counter[str] = Counter()
    for (_, word), count in occurrences.items():
        totals[word] += count
    return totals


@pytest.fixture(scope="module")
def metrics() -> PrivacyMetrics:
    names = removed = flagged = 0
    unflagged = []
    corpus = generate_corpus()
    for transcript in corpus:
        result = anonymize_text(transcript.text, SPACY_MODEL, LABELS)
        before = word_occurrences(transcript.text, transcript.sensitive_words)
        after = word_occurrences(result.text, transcript.sensitive_words)
        alerts = Counter(
            (alert.line, alert.token)
            for alert in verify_draft(result.text, result.entities, bundled_names(), ())
        )
        names += before.total()
        removed += before.total() - after.total()
        for (line, word), count in after.items():
            caught = min(count, alerts[(line, word)])
            flagged += caught
            unflagged.extend([(transcript.kind, line, word)] * (count - caught))
    return PrivacyMetrics(len(corpus), names, removed, flagged, tuple(unflagged))


def test_corpus_has_enough_transcripts_of_both_kinds() -> None:
    kinds = Counter(transcript.kind for transcript in generate_corpus())

    assert kinds.total() >= MINIMUM_TRANSCRIPTS
    assert set(kinds) == {"talk", "discussion"}


def test_corpus_is_deterministic() -> None:
    assert generate_corpus(count=20) == generate_corpus(count=20)


def test_corpus_includes_transcription_misspellings_and_repeated_first_names() -> None:
    corpus = generate_corpus()
    variants = {variant for options in MISSPELLINGS.values() for variant in options}
    used = set().union(*(transcript.sensitive_words for transcript in corpus))
    repeated = [
        transcript
        for transcript in corpus
        if any(count > 1 for count in word_occurrences_by_word(transcript).values())
    ]

    assert len(variants & used) >= len(variants) // 2
    assert len(repeated) == len(corpus)


def test_anonymizer_alone_removes_at_least_95_percent_of_names(metrics: PrivacyMetrics) -> None:
    assert metrics.transcripts >= MINIMUM_TRANSCRIPTS
    assert metrics.removed_by_anonymizer >= MINIMUM_ANONYMIZER_REMOVAL


def test_anonymizer_and_name_check_remove_or_flag_every_name(metrics: PrivacyMetrics) -> None:
    assert metrics.removed_or_flagged == 1.0


def test_no_name_is_left_in_the_anonymized_text_without_an_alert(
    metrics: PrivacyMetrics,
) -> None:
    assert metrics.unflagged == ()
