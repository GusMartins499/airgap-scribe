import datetime
from pathlib import Path

import pytest

from airgap_scribe.naming import parse_meeting_file

KINDS = ("talk", "discussion")


def test_parses_date_kind_and_stem_from_a_valid_name(tmp_path: Path) -> None:
    path = tmp_path / "2026-11-09_talk.m4a"

    meeting = parse_meeting_file(path, KINDS)

    assert meeting is not None
    assert meeting.path == path
    assert meeting.date == datetime.date(2026, 11, 9)
    assert meeting.kind == "talk"
    assert meeting.stem == "2026-11-09_talk"
    assert meeting.transcript is None


@pytest.mark.parametrize("extension", ["m4a", "mp4", "mp3", "opus", "ogg", "aac", "wav"])
def test_accepts_every_supported_audio_extension(tmp_path: Path, extension: str) -> None:
    assert parse_meeting_file(tmp_path / f"2026-01-31_talk.{extension}", KINDS) is not None


def test_accepts_upper_case_extensions_and_keeps_the_stem_lowercase(tmp_path: Path) -> None:
    meeting = parse_meeting_file(tmp_path / "2026-01-31_discussion.M4A", KINDS)

    assert meeting is not None
    assert meeting.stem == "2026-01-31_discussion"


def test_accepts_kinds_with_hyphens(tmp_path: Path) -> None:
    meeting = parse_meeting_file(tmp_path / "2026-01-31_open-talk.mp3", ["open-talk"])

    assert meeting is not None
    assert meeting.kind == "open-talk"


def test_finds_the_sibling_transcript_when_present(tmp_path: Path) -> None:
    transcript = tmp_path / "2026-11-09_talk.txt"
    transcript.write_text("synthetic", encoding="utf-8")

    meeting = parse_meeting_file(tmp_path / "2026-11-09_talk.m4a", KINDS)

    assert meeting is not None
    assert meeting.transcript == transcript


def test_accepts_kinds_from_a_one_shot_iterable(tmp_path: Path) -> None:
    meeting = parse_meeting_file(tmp_path / "2026-11-09_talk.m4a", iter(KINDS))

    assert meeting is not None


@pytest.mark.parametrize(
    "name",
    [
        "2026-11-09_lecture.m4a",
        "2026-11-09_Talk.m4a",
        "2026-02-30_talk.m4a",
        "2026-13-01_talk.m4a",
        "2026-11-9_talk.m4a",
        "26-11-09_talk.m4a",
        "2026-11-09-talk.m4a",
        "2026-11-09_talk.txt",
        "2026-11-09_talk.flac",
        "2026-11-09_talk",
        "2026-11-09_talk.m4a.part",
        "2026-11-09_talk_extra.m4a",
        "x2026-11-09_talk.m4a",
        ".syncthing.2026-11-09_talk.m4a.tmp",
        "~syncthing~2026-11-09_talk.m4a.tmp",
        "2026-11-09_.m4a",
        "notes.md",
        "",
    ],
)
def test_rejects_names_that_are_not_eligible_recordings(tmp_path: Path, name: str) -> None:
    assert parse_meeting_file(tmp_path / name, KINDS) is None


def test_rejects_non_ascii_digits_in_the_date(tmp_path: Path) -> None:
    assert parse_meeting_file(tmp_path / "\uff12\uff10\uff12\uff16-11-09_talk.m4a", KINDS) is None
