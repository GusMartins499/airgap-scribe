import json
from pathlib import Path
from typing import Any

import pytest

from airgap_scribe import anonymize, audio, transcribe, workdir
from airgap_scribe.anonymize import AnonymizationResult
from airgap_scribe.config import (
    AnonymizationSettings,
    Config,
    PathsSettings,
    TranscriptionSettings,
    VerifySettings,
)
from airgap_scribe.prepare import PrepareError, prepare

RAW_TEXT = "Speaker 1: Ana Souza falou em Lisboa.\n"
ANON_TEXT = "Speaker 1: [PERSON_1] falou em [LOCATION_1].\n"


@pytest.fixture
def config() -> Config:
    return Config(
        paths=PathsSettings(data_dir=None, models_dir=Path("/models")),
        transcription=TranscriptionSettings(
            whisper_model="model-under-test",
            language="pt",
            initial_prompt="Glossary hint.",
            threads=4,
        ),
        anonymization=AnonymizationSettings(spacy_model="pt_test_model", labels=("PER", "LOC")),
        verify=VerifySettings(),
    )


class Calls:
    def __init__(self) -> None:
        self.log: list[tuple[str, tuple[Any, ...]]] = []


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    recorded = Calls()

    def fake_to_wav(src: Path, dst: Path) -> None:
        recorded.log.append(("to_wav", (src, dst)))
        dst.write_bytes(b"RIFF")

    def fake_transcribe(*args: Any) -> None:
        recorded.log.append(("transcribe", args))
        args[1].write_text(RAW_TEXT, encoding="utf-8")

    def fake_anonymize(*args: Any) -> AnonymizationResult:
        recorded.log.append(("anonymize_text", args))
        return AnonymizationResult(
            text=ANON_TEXT,
            mapping={"[PERSON_1]": "Ana Souza", "[LOCATION_1]": "Lisboa"},
            entities=["Ana Souza", "Lisboa"],
        )

    monkeypatch.setattr(audio, "to_wav", fake_to_wav)
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(anonymize, "anonymize_text", fake_anonymize)
    return recorded


def names(calls: Calls) -> list[str]:
    return [name for name, _ in calls.log]


def test_recording_is_converted_transcribed_and_anonymized_in_order(
    calls: Calls, config: Config, tmp_path: Path
) -> None:
    source = tmp_path / "input" / "meeting.m4a"
    work = tmp_path / "work"

    prepare(source, None, work, config)

    assert names(calls) == ["to_wav", "transcribe", "anonymize_text"]
    assert calls.log[0][1] == (source, work / workdir.AUDIO_WAV)


def test_transcription_uses_the_configured_model_and_settings(
    calls: Calls, config: Config, tmp_path: Path
) -> None:
    work = tmp_path / "work"

    prepare(tmp_path / "meeting.m4a", None, work, config)

    _, args = calls.log[1]
    assert args == (
        work / workdir.AUDIO_WAV,
        work / workdir.TRANSCRIPT_RAW,
        Path("/models/model-under-test"),
        "pt",
        "Glossary hint.",
        4,
    )


def test_anonymizer_receives_raw_text_model_and_labels(
    calls: Calls, config: Config, tmp_path: Path
) -> None:
    prepare(tmp_path / "meeting.m4a", None, tmp_path / "work", config)

    _, args = calls.log[2]
    assert args == (RAW_TEXT, "pt_test_model", ("PER", "LOC"))


def test_anonymized_outputs_are_written_to_the_work_directory(
    calls: Calls, config: Config, tmp_path: Path
) -> None:
    work = tmp_path / "work"

    prepare(tmp_path / "meeting.m4a", None, work, config)

    assert (work / workdir.TRANSCRIPT_ANON).read_text(encoding="utf-8") == ANON_TEXT
    assert json.loads((work / workdir.ENTITIES).read_text(encoding="utf-8")) == [
        "Ana Souza",
        "Lisboa",
    ]
    assert json.loads((work / workdir.MAPPING).read_text(encoding="utf-8")) == {
        "[PERSON_1]": "Ana Souza",
        "[LOCATION_1]": "Lisboa",
    }


def test_json_outputs_keep_accented_characters_readable(
    monkeypatch: pytest.MonkeyPatch, calls: Calls, config: Config, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        anonymize,
        "anonymize_text",
        lambda *args: AnonymizationResult("x", {"[LOCATION_1]": "São Tomé"}, ["São Tomé"]),
    )
    work = tmp_path / "work"

    prepare(tmp_path / "meeting.m4a", None, work, config)

    assert "São Tomé" in (work / workdir.MAPPING).read_text(encoding="utf-8")


def test_provided_transcript_skips_audio_conversion_and_transcription(
    calls: Calls, config: Config, tmp_path: Path
) -> None:
    provided = tmp_path / "input" / "provided.txt"
    provided.parent.mkdir()
    provided.write_text(RAW_TEXT, encoding="utf-8")
    work = tmp_path / "work"

    prepare(tmp_path / "input" / "meeting.m4a", provided, work, config)

    assert names(calls) == ["anonymize_text"]
    assert (work / workdir.TRANSCRIPT_RAW).read_text(encoding="utf-8") == RAW_TEXT
    assert not (work / workdir.AUDIO_WAV).exists()
    assert provided.read_text(encoding="utf-8") == RAW_TEXT


def test_missing_provided_transcript_raises_before_anonymizing(
    calls: Calls, config: Config, tmp_path: Path
) -> None:
    with pytest.raises(PrepareError, match="provided transcript not found"):
        prepare(tmp_path / "meeting.m4a", tmp_path / "absent.txt", tmp_path / "work", config)
    assert calls.log == []


def test_audio_failure_stops_before_transcription(
    monkeypatch: pytest.MonkeyPatch, calls: Calls, config: Config, tmp_path: Path
) -> None:
    def failing_to_wav(src: Path, dst: Path) -> None:
        raise audio.AudioError("conversion failed")

    monkeypatch.setattr(audio, "to_wav", failing_to_wav)

    with pytest.raises(audio.AudioError):
        prepare(tmp_path / "meeting.m4a", None, tmp_path / "work", config)
    assert calls.log == []
    assert not (tmp_path / "work" / workdir.TRANSCRIPT_ANON).exists()
