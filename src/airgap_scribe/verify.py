from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from airgap_scribe.config import Config


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
    raise NotImplementedError


def run_verify(work_dir: Path, config: Config) -> list[Alert]:
    raise NotImplementedError
