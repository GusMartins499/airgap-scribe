from pathlib import Path

from airgap_scribe.config import Config
from airgap_scribe.naming import MeetingFile


def write_draft(work_dir: Path, meeting: MeetingFile, config: Config) -> Path:
    raise NotImplementedError
