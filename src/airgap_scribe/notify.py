from airgap_scribe.config import Config
from airgap_scribe.naming import MeetingFile
from airgap_scribe.publish import Publication


def notify_success(
    pub: Publication,
    meeting: MeetingFile,
    alert_count: int,
    config: Config,
) -> None:
    raise NotImplementedError


def notify_failure(meeting_stem: str, stage: str, error: str, config: Config) -> None:
    raise NotImplementedError
