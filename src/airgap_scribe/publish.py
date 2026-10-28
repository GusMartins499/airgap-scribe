from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from airgap_scribe.config import Config
from airgap_scribe.naming import MeetingFile
from airgap_scribe.verify import Alert


@dataclass(frozen=True)
class Publication:
    branch: str
    pr_url: str
    preview_url: str


def publish(
    work_dir: Path,
    meeting: MeetingFile,
    alerts: Sequence[Alert],
    config: Config,
) -> Publication:
    raise NotImplementedError
