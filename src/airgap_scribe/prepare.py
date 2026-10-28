from pathlib import Path

from airgap_scribe.config import Config


def prepare(input_audio: Path, transcript: Path | None, work_dir: Path, config: Config) -> None:
    raise NotImplementedError
