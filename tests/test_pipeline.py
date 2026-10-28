import json
import os
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from host_config import make_config, prepend_path, write_executable

from airgap_scribe import pipeline
from airgap_scribe.config import Config
from airgap_scribe.naming import MeetingFile, parse_meeting_file
from airgap_scribe.pipeline import (
    ContainerError,
    PipelineError,
    StageFailedError,
    run_one,
    scan,
    try_one,
)
from airgap_scribe.publish import Publication
from airgap_scribe.queue import Queue
from airgap_scribe.verify import Alert
from airgap_scribe.workdir import ALERTS, CONTAINER_CONFIG, DRAFT, TRANSCRIPT_ANON

PUBLICATION = Publication(
    branch="draft/2026-11-09_talk",
    pr_url="https://github.com/example/content/pull/7",
    preview_url="https://draft-2026-11-09-talk.example.pages.dev",
)
ALERT_RECORDS = [{"line": 4, "token": "Example", "reason": "common-first-name"}]


class FakeDocker:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.log = tmp_path / "docker-calls.jsonl"
        self.fail_on = tmp_path / "docker-fail-on.txt"
        self.alerts = tmp_path / "docker-alerts.json"
        self.alerts.write_text(json.dumps(ALERT_RECORDS), encoding="utf-8")
        bin_dir = tmp_path / "docker-bin"
        write_executable(
            bin_dir / "docker",
            f"""#!{sys.executable}
import json
import pathlib
import sys

base = pathlib.Path({str(tmp_path)!r})
args = sys.argv[1:]
with (base / "docker-calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
mounts = dict(
    args[i + 1].split(":")[1::-1] for i, arg in enumerate(args) if arg == "-v"
)
work = pathlib.Path(mounts["/work"])
step = "prepare" if "prepare" in args else "verify"
fail_on = base / "docker-fail-on.txt"
if fail_on.exists() and fail_on.read_text().strip() == step:
    sys.stderr.write("container stderr with raw details")
    sys.exit(4)
if step == "prepare":
    (work / "transcript.anon.txt").write_text("[PERSON_1] spoke.\\n", encoding="utf-8")
elif (base / "docker-alerts.json").exists():
    (work / "alerts.json").write_text((base / "docker-alerts.json").read_text())
""",
        )
        monkeypatch.setenv("PATH", prepend_path(bin_dir))

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


class Recorder:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.result = result
        self.error = error

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return self.result


class HostDoubles:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.draft = Recorder()
        self.publish = Recorder(PUBLICATION)
        self.notify_success = Recorder()
        self.notify_failure = Recorder()

        def write_draft(work_dir: Path, meeting: MeetingFile, config: Config) -> Path:
            self.draft(work_dir, meeting, config)
            if self.draft.error is not None:
                raise self.draft.error
            (work_dir / DRAFT).write_text("---\ntitle: T\n---\nBody.\n", encoding="utf-8")
            return work_dir / DRAFT

        monkeypatch.setattr(pipeline, "write_draft", write_draft)
        monkeypatch.setattr(pipeline, "publish", self.publish)
        monkeypatch.setattr(pipeline, "notify_success", self.notify_success)
        monkeypatch.setattr(pipeline, "notify_failure", self.notify_failure)


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    return FakeDocker(tmp_path, monkeypatch)


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> HostDoubles:
    return HostDoubles(monkeypatch)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    config = make_config(tmp_path)
    Queue(config.paths.data_dir).ensure_layout()
    return config


def drop(config: Config, name: str, with_transcript: bool = False) -> MeetingFile:
    inbox = Queue(config.paths.data_dir).inbox
    (inbox / name).write_bytes(b"synthetic audio")
    if with_transcript:
        (inbox / (name.rsplit(".", 1)[0] + ".txt")).write_text("Speaker 1: hi\n")
    meeting = parse_meeting_file(inbox / name, config.kinds)
    assert meeting is not None
    return meeting


def test_run_one_runs_prepare_in_an_isolated_container(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    run_one(drop(config, "2026-11-09_talk.m4a"), config)

    queue = Queue(config.paths.data_dir)
    work_dir = queue.work / "2026-11-09_talk"
    prepare_call = docker.calls()[0]
    assert prepare_call == [
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{work_dir}:/work",
        "-v",
        f"{queue.processing}:/input:ro",
        "-v",
        f"{config.paths.models_dir}:/models:ro",
        "example.org/airgap-scribe:test",
        "prepare",
        "--input",
        "/input/2026-11-09_talk.m4a",
        "--workdir",
        "/work",
        "--config",
        "/work/container.toml",
    ]


def test_run_one_passes_a_provided_transcript_to_prepare(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    run_one(drop(config, "2026-11-09_talk.m4a", with_transcript=True), config)

    prepare_call = docker.calls()[0]
    index = prepare_call.index("--transcript")
    assert prepare_call[index + 1] == "/input/2026-11-09_talk.txt"


def test_run_one_runs_verify_with_only_the_work_dir_mounted(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    run_one(drop(config, "2026-11-09_talk.m4a"), config)

    work_dir = Queue(config.paths.data_dir).work / "2026-11-09_talk"
    verify_call = docker.calls()[1]
    assert verify_call == [
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{work_dir}:/work",
        "example.org/airgap-scribe:test",
        "verify",
        "--workdir",
        "/work",
        "--config",
        "/work/container.toml",
    ]


def test_run_one_writes_a_minimal_container_config(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    run_one(drop(config, "2026-11-09_talk.m4a"), config)

    container_toml = Queue(config.paths.data_dir).work / "2026-11-09_talk" / CONTAINER_CONFIG
    values = tomllib.loads(container_toml.read_text(encoding="utf-8"))
    assert set(values) == {"paths", "transcription", "anonymization", "verify"}
    assert values["paths"]["models_dir"] == "/models"


def test_run_one_drafts_publishes_notifies_and_completes(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    run_one(drop(config, "2026-11-09_talk.m4a"), config)

    queue = Queue(config.paths.data_dir)
    work_dir = queue.work / "2026-11-09_talk"
    [(draft_work_dir, drafted, _)] = host.draft.calls
    assert draft_work_dir == work_dir
    assert drafted.path == queue.processing / "2026-11-09_talk.m4a"
    assert (work_dir / TRANSCRIPT_ANON).is_file()
    [(_, published, alerts, _)] = host.publish.calls
    assert published.stem == "2026-11-09_talk"
    assert alerts == [Alert(line=4, token="Example", reason="common-first-name")]
    [(publication, notified, alert_count, _)] = host.notify_success.calls
    assert (publication, notified.stem, alert_count) == (PUBLICATION, "2026-11-09_talk", 1)
    assert host.notify_failure.calls == []
    assert (queue.done / "2026-11-09_talk.m4a").is_file()
    record = json.loads(queue.done_record_path("2026-11-09_talk").read_text())
    assert record["pr_url"] == PUBLICATION.pr_url
    assert record["branch"] == PUBLICATION.branch
    assert record["preview_url"] == PUBLICATION.preview_url
    assert record["alert_count"] == 1
    assert record["recording"] == "2026-11-09_talk.m4a"


def test_run_one_skips_a_recording_that_is_already_done(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    meeting = drop(config, "2026-11-09_talk.m4a")
    Queue(config.paths.data_dir).done_record_path(meeting.stem).write_text("{}")

    run_one(meeting, config)

    assert docker.calls() == []
    assert meeting.path.is_file()


@pytest.mark.parametrize("step", ["prepare", "verify"])
def test_a_failing_container_step_fails_the_item_at_that_stage(
    config: Config, docker: FakeDocker, host: HostDoubles, step: str
) -> None:
    docker.fail_on.write_text(step)

    with pytest.raises(StageFailedError) as raised:
        run_one(drop(config, "2026-11-09_talk.m4a"), config)

    assert raised.value.stage == step
    queue = Queue(config.paths.data_dir)
    assert (queue.failed / "2026-11-09_talk.m4a").is_file()
    error_text = queue.error_path("2026-11-09_talk").read_text()
    assert error_text.startswith(f"stage: {step}\n")
    assert "container stderr with raw details" in error_text
    assert host.notify_success.calls == []


def test_failure_notifications_never_carry_the_error_details(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    docker.fail_on.write_text("prepare")

    with pytest.raises(StageFailedError):
        run_one(drop(config, "2026-11-09_talk.m4a"), config)

    [(stem, stage, summary, _)] = host.notify_failure.calls
    assert (stem, stage) == ("2026-11-09_talk", "prepare")
    assert summary == "ContainerError; details in failed/2026-11-09_talk.error.txt"


@pytest.mark.parametrize(
    ("double", "stage"),
    [("draft", "draft"), ("publish", "publish"), ("notify_success", "notify")],
)
def test_a_failing_host_step_fails_the_item_at_that_stage(
    config: Config, docker: FakeDocker, host: HostDoubles, double: str, stage: str
) -> None:
    getattr(host, double).error = RuntimeError("synthetic failure")

    with pytest.raises(StageFailedError) as raised:
        run_one(drop(config, "2026-11-09_talk.m4a"), config)

    assert raised.value.stage == stage
    queue = Queue(config.paths.data_dir)
    assert (queue.failed / "2026-11-09_talk.m4a").is_file()
    assert not queue.is_done("2026-11-09_talk")
    assert host.notify_failure.calls[0][1] == stage


def test_missing_alerts_file_fails_the_verify_stage(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    docker.alerts.unlink()

    with pytest.raises(StageFailedError) as raised:
        run_one(drop(config, "2026-11-09_talk.m4a"), config)

    assert raised.value.stage == "verify"
    error_text = Queue(config.paths.data_dir).error_path("2026-11-09_talk").read_text()
    assert ALERTS in error_text


def test_missing_docker_fails_the_prepare_stage(
    config: Config, host: HostDoubles, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    with pytest.raises(StageFailedError) as raised:
        run_one(drop(config, "2026-11-09_talk.m4a"), config)

    assert raised.value.stage == "prepare"


def test_a_failing_failure_notification_does_not_hide_the_failure(
    config: Config,
    docker: FakeDocker,
    host: HostDoubles,
    capsys: pytest.CaptureFixture[str],
) -> None:
    docker.fail_on.write_text("prepare")
    host.notify_failure.error = OSError("network unreachable")

    with pytest.raises(StageFailedError):
        run_one(drop(config, "2026-11-09_talk.m4a"), config)

    assert (Queue(config.paths.data_dir).failed / "2026-11-09_talk.m4a").is_file()
    assert "could not send the failure notification" in capsys.readouterr().err


def test_a_recording_that_cannot_be_claimed_is_reported(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    meeting = drop(config, "2026-11-09_talk.m4a")
    (Queue(config.paths.data_dir).processing / "2026-11-09_talk.m4a").write_bytes(b"leftover")

    with pytest.raises(StageFailedError) as raised:
        run_one(meeting, config)

    assert raised.value.stage == "claim"
    assert meeting.path.is_file()
    assert host.notify_failure.calls[0][:3] == ("2026-11-09_talk", "claim", "FileExistsError")


def test_scan_processes_every_pending_recording_in_order(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    drop(config, "2026-11-16_talk.m4a")
    drop(config, "2026-11-09_talk.m4a")
    (Queue(config.paths.data_dir).inbox / "notes.m4a").write_bytes(b"ignored")

    scan(config)

    assert [call[1].stem for call in host.draft.calls] == ["2026-11-09_talk", "2026-11-16_talk"]


def test_scan_continues_after_a_failure_and_reports_it(
    config: Config, docker: FakeDocker, host: HostDoubles, monkeypatch: pytest.MonkeyPatch
) -> None:
    drop(config, "2026-11-09_talk.m4a")
    drop(config, "2026-11-16_talk.m4a")
    real_publish = host.publish

    def publish_failing_first(*args: Any) -> Publication:
        if args[1].stem == "2026-11-09_talk":
            raise RuntimeError("synthetic failure")
        return real_publish(*args)

    monkeypatch.setattr(pipeline, "publish", publish_failing_first)

    with pytest.raises(
        PipelineError, match=r"1 recording\(s\) failed: 2026-11-09_talk \(publish\)"
    ):
        scan(config)

    assert Queue(config.paths.data_dir).is_done("2026-11-16_talk")


def test_scan_does_not_retry_a_recording_that_stays_in_the_inbox(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    drop(config, "2026-11-09_talk.m4a")
    (Queue(config.paths.data_dir).processing / "2026-11-09_talk.m4a").write_bytes(b"leftover")

    with pytest.raises(PipelineError, match=r"\(claim\)"):
        scan(config)

    assert len(host.notify_failure.calls) == 1


def test_scan_with_an_empty_inbox_does_nothing(
    config: Config, docker: FakeDocker, host: HostDoubles
) -> None:
    scan(config)

    assert docker.calls() == []


def test_a_config_without_data_dir_is_rejected(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config = replace(config, paths=replace(config.paths, data_dir=None))

    with pytest.raises(PipelineError, match="data directory"):
        scan(config)


def test_a_config_without_container_section_fails_the_prepare_stage(
    config: Config, host: HostDoubles
) -> None:
    config = replace(config, container=None)

    with pytest.raises(StageFailedError) as raised:
        run_one(drop(config, "2026-11-09_talk.m4a"), config)

    assert raised.value.stage == "prepare"


def try_audio(tmp_path: Path, name: str = "any recording.mp3") -> Path:
    audio = tmp_path / "downloads" / name
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"synthetic audio")
    return audio


def test_try_one_runs_prepare_on_a_copy_of_any_audio_file(
    config: Config, docker: FakeDocker, host: HostDoubles, tmp_path: Path
) -> None:
    audio = try_audio(tmp_path)
    work_dir = tmp_path / "try-out"

    try_one(audio, "talk", work_dir, config)

    assert audio.is_file()
    assert (work_dir / "input" / "any recording.mp3").read_bytes() == b"synthetic audio"
    prepare_call = docker.calls()[0]
    assert prepare_call[:6] == [
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]
    assert f"{work_dir}:/work" in prepare_call
    assert f"{work_dir / 'input'}:/input:ro" in prepare_call
    assert f"{config.paths.models_dir}:/models:ro" in prepare_call
    assert prepare_call[prepare_call.index("--input") + 1] == "/input/any recording.mp3"
    assert "--transcript" not in prepare_call
    assert (work_dir / CONTAINER_CONFIG).is_file()


def test_try_one_drafts_and_verifies_in_the_output_dir(
    config: Config, docker: FakeDocker, host: HostDoubles, tmp_path: Path
) -> None:
    work_dir = tmp_path / "try-out"

    result = try_one(try_audio(tmp_path), "talk", work_dir, config)

    [(draft_work_dir, meeting, _)] = host.draft.calls
    assert draft_work_dir == work_dir
    assert (meeting.kind, meeting.stem) == ("talk", "any recording")
    verify_call = docker.calls()[1]
    assert "verify" in verify_call
    assert verify_call.count("-v") == 1
    assert result.work_dir == work_dir
    assert result.transcript_raw == work_dir / "transcript.raw.txt"
    assert result.transcript_anon == work_dir / TRANSCRIPT_ANON
    assert result.draft == work_dir / DRAFT
    assert result.alerts_file == work_dir / ALERTS
    assert result.alerts == [Alert(line=4, token="Example", reason="common-first-name")]


def test_try_one_never_touches_the_queue_publish_or_notify(
    config: Config, docker: FakeDocker, host: HostDoubles, tmp_path: Path
) -> None:
    local = replace(config, publish=None, notify=None, paths=replace(config.paths, data_dir=None))

    try_one(try_audio(tmp_path), "talk", tmp_path / "try-out", local)

    assert host.publish.calls == []
    assert host.notify_success.calls == []
    assert host.notify_failure.calls == []
    queue = Queue(config.paths.data_dir)
    assert list(queue.work.iterdir()) == []
    assert list(queue.done.iterdir()) == []


def test_try_one_rejects_an_unknown_kind_before_running_anything(
    config: Config, docker: FakeDocker, host: HostDoubles, tmp_path: Path
) -> None:
    with pytest.raises(PipelineError, match="unknown meeting kind 'lecture'"):
        try_one(try_audio(tmp_path), "lecture", tmp_path / "try-out", config)

    assert docker.calls() == []


def test_try_one_reports_a_failing_container_step(
    config: Config, docker: FakeDocker, host: HostDoubles, tmp_path: Path
) -> None:
    docker.fail_on.write_text("verify")

    with pytest.raises(ContainerError, match="container step 'verify'"):
        try_one(try_audio(tmp_path), "talk", tmp_path / "try-out", config)
