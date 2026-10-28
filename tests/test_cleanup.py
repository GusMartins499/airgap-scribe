import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from host_config import make_config, prepend_path, write_executable

from airgap_scribe.cleanup import CleanupError, cleanup
from airgap_scribe.config import Config
from airgap_scribe.queue import Queue

MERGED_URL = "https://github.com/example/content/pull/1"
OPEN_URL = "https://github.com/example/content/pull/2"
BROKEN_URL = "https://github.com/example/content/pull/3"
GARBLED_URL = "https://github.com/example/content/pull/4"


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "gh-calls.jsonl"
    write_executable(
        tmp_path / "gh-bin" / "gh",
        f"""#!{sys.executable}
import json
import sys

args = sys.argv[1:]
with open({str(log)!r}, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
states = {{{MERGED_URL!r}: "MERGED", {OPEN_URL!r}: "OPEN"}}
url = args[2]
if url == {GARBLED_URL!r}:
    print("not json")
elif url in states:
    print(json.dumps({{"state": states[url]}}))
else:
    sys.stderr.write("no pull request found")
    sys.exit(1)
""",
    )
    monkeypatch.setenv("PATH", prepend_path(tmp_path / "gh-bin"))
    return log


@pytest.fixture
def config(tmp_path: Path) -> Config:
    config = make_config(tmp_path)
    Queue(config.paths.data_dir).ensure_layout()
    return config


def archive(config: Config, stem: str, pr_url: str | None) -> Queue:
    queue = Queue(config.paths.data_dir)
    (queue.done / f"{stem}.m4a").write_bytes(b"synthetic audio")
    (queue.done / f"{stem}.txt").write_text("Speaker 1: hi\n")
    work_dir = queue.work / stem
    work_dir.mkdir(parents=True)
    (work_dir / "transcript.raw.txt").write_text("raw\n")
    record = {"stem": stem} if pr_url is None else {"stem": stem, "pr_url": pr_url}
    queue.done_record_path(stem).write_text(json.dumps(record))
    return queue


def test_cleanup_removes_work_and_archived_files_of_merged_drafts(
    config: Config, fake_gh: Path
) -> None:
    queue = archive(config, "2026-11-09_talk", MERGED_URL)

    assert cleanup(config) == ["2026-11-09_talk"]

    assert not (queue.work / "2026-11-09_talk").exists()
    assert sorted(path.name for path in queue.done.iterdir()) == ["2026-11-09_talk.json"]


def test_cleanup_keeps_the_record_and_marks_it_cleaned(config: Config, fake_gh: Path) -> None:
    queue = archive(config, "2026-11-09_talk", MERGED_URL)

    cleanup(config)

    record = json.loads(queue.done_record_path("2026-11-09_talk").read_text())
    assert record["pr_url"] == MERGED_URL
    assert record["cleaned_at"]


def test_cleanup_asks_gh_for_the_pull_request_state(config: Config, fake_gh: Path) -> None:
    archive(config, "2026-11-09_talk", MERGED_URL)

    cleanup(config)

    assert json.loads(fake_gh.read_text()) == ["pr", "view", MERGED_URL, "--json", "state"]


def test_cleanup_keeps_drafts_whose_pull_request_is_not_merged(
    config: Config, fake_gh: Path
) -> None:
    queue = archive(config, "2026-11-09_talk", OPEN_URL)

    assert cleanup(config) == []

    assert (queue.work / "2026-11-09_talk").is_dir()
    assert (queue.done / "2026-11-09_talk.m4a").is_file()


def test_cleanup_does_not_touch_other_recordings(config: Config, fake_gh: Path) -> None:
    archive(config, "2026-11-09_talk", MERGED_URL)
    queue = archive(config, "2026-11-16_talk", OPEN_URL)

    assert cleanup(config) == ["2026-11-09_talk"]

    assert (queue.done / "2026-11-16_talk.m4a").is_file()
    assert (queue.work / "2026-11-16_talk").is_dir()


def test_cleanup_skips_records_already_cleaned(config: Config, fake_gh: Path) -> None:
    archive(config, "2026-11-09_talk", MERGED_URL)
    cleanup(config)
    fake_gh.unlink()

    assert cleanup(config) == []
    assert not fake_gh.exists()


def test_cleanup_skips_records_without_a_pull_request(config: Config, fake_gh: Path) -> None:
    queue = archive(config, "2026-11-09_talk", None)

    assert cleanup(config) == []
    assert (queue.done / "2026-11-09_talk.m4a").is_file()


@pytest.mark.parametrize("url", [BROKEN_URL, GARBLED_URL])
def test_cleanup_reports_and_skips_unanswerable_pull_requests(
    config: Config, fake_gh: Path, url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    archive(config, "2026-11-09_talk", url)
    archive(config, "2026-11-16_talk", MERGED_URL)

    assert cleanup(config) == ["2026-11-16_talk"]
    assert "skipping 2026-11-09_talk" in capsys.readouterr().err


def test_cleanup_reports_a_missing_gh(
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive(config, "2026-11-09_talk", MERGED_URL)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    assert cleanup(config) == []
    assert "gh is not installed" in capsys.readouterr().err


def test_cleanup_with_nothing_done_returns_nothing(tmp_path: Path) -> None:
    assert cleanup(make_config(tmp_path)) == []


def test_cleanup_requires_a_data_dir(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config = replace(config, paths=replace(config.paths, data_dir=None))

    with pytest.raises(CleanupError, match="data directory"):
        cleanup(config)
