from airgap_scribe.config import Config
from airgap_scribe.naming import MeetingFile


def run_one(meeting: MeetingFile, config: Config) -> None:
    raise NotImplementedError


def scan(config: Config) -> None:
    raise NotImplementedError
