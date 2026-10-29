import datetime
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS = frozenset({"m4a", "mp4", "mp3", "opus", "ogg", "aac", "wav"})
TRANSCRIPT_EXTENSION = ".txt"

_MEETING_FILE_NAME = re.compile(
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})_(?P<kind>[^._]+)\.(?P<extension>[^.]+)"
)


@dataclass(frozen=True)
class MeetingFile:
    path: Path
    date: datetime.date
    kind: str
    stem: str
    transcript: Path | None


def parse_meeting_file(path: Path, kinds: Iterable[str]) -> MeetingFile | None:
    match = _MEETING_FILE_NAME.fullmatch(path.name)
    if match is None:
        return None
    if match["extension"].lower() not in AUDIO_EXTENSIONS:
        return None
    if match["kind"] not in set(kinds):
        return None
    try:
        date = datetime.date.fromisoformat(match["date"])
    except ValueError:
        return None
    stem = f"{match['date']}_{match['kind']}"
    transcript = path.with_name(stem + TRANSCRIPT_EXTENSION)
    return MeetingFile(
        path=path,
        date=date,
        kind=match["kind"],
        stem=stem,
        transcript=transcript if transcript.is_file() else None,
    )
