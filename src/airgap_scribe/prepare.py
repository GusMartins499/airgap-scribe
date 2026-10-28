import json
import shutil
from pathlib import Path

from airgap_scribe import anonymize, audio, transcribe, workdir
from airgap_scribe.config import Config


class PrepareError(Exception):
    pass


def prepare(input_audio: Path, transcript: Path | None, work_dir: Path, config: Config) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_transcript = work_dir / workdir.TRANSCRIPT_RAW
    if transcript is not None:
        if not transcript.is_file():
            raise PrepareError(f"provided transcript not found: {transcript}")
        shutil.copyfile(transcript, raw_transcript)
    else:
        wav = work_dir / workdir.AUDIO_WAV
        audio.to_wav(input_audio, wav)
        settings = config.transcription
        transcribe.transcribe(
            wav,
            raw_transcript,
            config.whisper_model_path,
            settings.language,
            settings.initial_prompt,
            settings.threads,
        )
    result = anonymize.anonymize_text(
        raw_transcript.read_text(encoding="utf-8"),
        config.anonymization.spacy_model,
        config.anonymization.labels,
    )
    (work_dir / workdir.TRANSCRIPT_ANON).write_text(result.text, encoding="utf-8")
    _write_json(work_dir / workdir.ENTITIES, result.entities)
    _write_json(work_dir / workdir.MAPPING, result.mapping)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
