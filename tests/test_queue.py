import json
from pathlib import Path

import pytest

from airgap_scribe.naming import MeetingFile, parse_meeting_file
from airgap_scribe.queue import Queue

KINDS = ("talk", "discussion")


@pytest.fixture
def queue(tmp_path: Path) -> Queue:
    created = Queue(tmp_path / "data")
    created.ensure_layout()
    return created


def drop(queue: Queue, name: str, content: str = "synthetic") -> Path:
    path = queue.inbox / name
    path.write_text(content, encoding="utf-8")
    return path


def meeting(queue: Queue, name: str) -> MeetingFile:
    drop(queue, name)
    parsed = parse_meeting_file(queue.inbox / name, KINDS)
    assert parsed is not None
    return parsed


def test_ensure_layout_creates_every_queue_directory(tmp_path: Path) -> None:
    queue = Queue(tmp_path / "data")

    queue.ensure_layout()
    queue.ensure_layout()

    for name in ("inbox", "processing", "done", "failed", "work", "models"):
        assert (tmp_path / "data" / name).is_dir()


def test_pending_is_empty_when_the_inbox_does_not_exist(tmp_path: Path) -> None:
    assert Queue(tmp_path / "missing").pending(KINDS) == []


def test_pending_lists_only_eligible_files_sorted_by_date(queue: Queue) -> None:
    drop(queue, "2026-11-09_talk.m4a")
    drop(queue, "2026-03-01_discussion.mp3")
    drop(queue, "2026-11-09_talk.txt")
    drop(queue, "2026-05-05_lecture.m4a")
    drop(queue, ".syncthing.2026-06-01_talk.m4a.tmp")
    drop(queue, "~syncthing~2026-06-02_talk.m4a.tmp")
    (queue.inbox / "2026-07-07_talk.m4a").mkdir()

    pending = queue.pending(KINDS)

    assert [item.stem for item in pending] == ["2026-03-01_discussion", "2026-11-09_talk"]
    assert pending[1].transcript == queue.inbox / "2026-11-09_talk.txt"


def test_pending_accepts_kinds_from_a_one_shot_iterable(queue: Queue) -> None:
    drop(queue, "2026-11-09_talk.m4a")
    drop(queue, "2026-11-10_talk.m4a")

    assert len(queue.pending(iter(KINDS))) == 2


def test_pending_skips_stems_already_recorded_as_done(queue: Queue) -> None:
    drop(queue, "2026-11-09_talk.m4a")
    (queue.done / "2026-11-09_talk.json").write_text("{}", encoding="utf-8")

    assert queue.pending(KINDS) == []
    assert queue.is_done("2026-11-09_talk")


def test_claim_moves_audio_and_transcript_to_processing(queue: Queue) -> None:
    drop(queue, "2026-11-09_talk.txt", "synthetic transcript")
    item = meeting(queue, "2026-11-09_talk.m4a")

    claimed = queue.claim(item)

    assert claimed.path == queue.processing / "2026-11-09_talk.m4a"
    assert claimed.transcript == queue.processing / "2026-11-09_talk.txt"
    assert claimed.path.read_text(encoding="utf-8") == "synthetic"
    assert claimed.transcript.read_text(encoding="utf-8") == "synthetic transcript"
    assert list(queue.inbox.iterdir()) == []
    assert claimed.stem == item.stem
    assert claimed.date == item.date
    assert claimed.kind == item.kind


def test_claim_without_transcript_keeps_transcript_empty(queue: Queue) -> None:
    claimed = queue.claim(meeting(queue, "2026-11-09_talk.m4a"))

    assert claimed.transcript is None


def test_claim_refuses_to_overwrite_a_file_already_in_processing(queue: Queue) -> None:
    item = meeting(queue, "2026-11-09_talk.m4a")
    (queue.processing / "2026-11-09_talk.m4a").write_text("other", encoding="utf-8")

    with pytest.raises(FileExistsError):
        queue.claim(item)

    assert item.path.exists()
    assert (queue.processing / "2026-11-09_talk.m4a").read_text(encoding="utf-8") == "other"


def test_claim_fails_when_the_file_was_already_taken(queue: Queue) -> None:
    item = meeting(queue, "2026-11-09_talk.m4a")
    item.path.unlink()

    with pytest.raises(FileNotFoundError):
        queue.claim(item)


def test_claim_creates_the_processing_directory_when_missing(tmp_path: Path) -> None:
    queue = Queue(tmp_path / "data")
    queue.inbox.mkdir(parents=True)
    item = meeting(queue, "2026-11-09_talk.m4a")

    claimed = queue.claim(item)

    assert claimed.path.is_file()


def test_complete_archives_files_and_writes_the_record(queue: Queue) -> None:
    drop(queue, "2026-11-09_talk.txt")
    claimed = queue.claim(meeting(queue, "2026-11-09_talk.m4a"))
    record = {"pr_url": "https://example.test/pr/1", "note": "ação"}

    queue.complete(claimed, record)

    assert (queue.done / "2026-11-09_talk.m4a").is_file()
    assert (queue.done / "2026-11-09_talk.txt").is_file()
    assert list(queue.processing.iterdir()) == []
    stored = json.loads((queue.done / "2026-11-09_talk.json").read_text(encoding="utf-8"))
    assert stored == record
    assert queue.is_done("2026-11-09_talk")
    assert not list(queue.done.glob(".*.tmp"))


def test_completed_stem_is_never_pending_again(queue: Queue) -> None:
    queue.complete(queue.claim(meeting(queue, "2026-11-09_talk.m4a")), {"pr_url": "u"})

    drop(queue, "2026-11-09_talk.mp3")

    assert queue.pending(KINDS) == []


def test_fail_moves_files_to_failed_and_writes_the_error(queue: Queue) -> None:
    drop(queue, "2026-11-09_talk.txt")
    claimed = queue.claim(meeting(queue, "2026-11-09_talk.m4a"))

    queue.fail(claimed, "prepare failed: exit status 1")

    assert (queue.failed / "2026-11-09_talk.m4a").is_file()
    assert (queue.failed / "2026-11-09_talk.txt").is_file()
    assert (queue.failed / "2026-11-09_talk.error.txt").read_text(
        encoding="utf-8"
    ) == "prepare failed: exit status 1"
    assert list(queue.processing.iterdir()) == []
    assert not queue.is_done("2026-11-09_talk")


def test_failed_file_moved_back_to_inbox_is_pending_again(queue: Queue) -> None:
    queue.fail(queue.claim(meeting(queue, "2026-11-09_talk.m4a")), "boom")

    (queue.failed / "2026-11-09_talk.m4a").rename(queue.inbox / "2026-11-09_talk.m4a")

    assert [item.stem for item in queue.pending(KINDS)] == ["2026-11-09_talk"]


def test_work_dir_is_created_per_stem(queue: Queue) -> None:
    item = meeting(queue, "2026-11-09_talk.m4a")

    first = queue.work_dir(item)
    second = queue.work_dir(item)

    assert first == second == queue.work / "2026-11-09_talk"
    assert first.is_dir()


def test_done_records_are_returned_sorted_with_their_paths(queue: Queue) -> None:
    queue.complete(queue.claim(meeting(queue, "2026-11-10_talk.m4a")), {"pr_url": "b"})
    queue.complete(queue.claim(meeting(queue, "2026-11-09_talk.m4a")), {"pr_url": "a"})

    records = queue.done_records()

    assert records == [
        (queue.done / "2026-11-09_talk.json", {"pr_url": "a"}),
        (queue.done / "2026-11-10_talk.json", {"pr_url": "b"}),
    ]


def test_done_records_is_empty_without_a_done_directory(tmp_path: Path) -> None:
    assert Queue(tmp_path / "missing").done_records() == []
