import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from airgap_scribe.cleanup import pull_request_state
from airgap_scribe.config import Config, write_container_config
from airgap_scribe.credit import assemble, attachment_files, looks_like_credit_note, write_credit
from airgap_scribe.draft import write_draft
from airgap_scribe.naming import MeetingFile
from airgap_scribe.notify import (
    notify_failure,
    notify_late_attachments,
    notify_resumed,
    notify_success,
)
from airgap_scribe.publish import publish, publish_attachments
from airgap_scribe.queue import Queue
from airgap_scribe.verify import Alert
from airgap_scribe.workdir import ALERTS, CONTAINER_CONFIG, DRAFT, TRANSCRIPT_ANON, TRANSCRIPT_RAW

CONTAINER_WORK = "/work"
CONTAINER_INPUT = "/input"
CONTAINER_MODELS = "/models"
CONTAINER_STEPS = ("prepare", "verify")
OUTPUT_TAIL = 2000
TRY_INPUT_DIR = "input"
STALL_SECONDS = 30 * 60
POLL_SECONDS = 5
REMOVE_TIMEOUT_SECONDS = 60
OPEN = "OPEN"

_INVALID_NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9_.-]+")


class PipelineError(Exception):
    pass


class ContainerError(Exception):
    pass


class StageFailedError(Exception):
    def __init__(self, stem: str, stage: str) -> None:
        super().__init__(f"{stem} failed at stage '{stage}'")
        self.stem = stem
        self.stage = stage


@dataclass(frozen=True)
class TryResult:
    work_dir: Path
    transcript_raw: Path
    transcript_anon: Path
    draft: Path
    alerts_file: Path
    alerts: list[Alert]


def run_one(meeting: MeetingFile, config: Config) -> None:
    queue = _queue(config)
    queue.ensure_layout()
    if queue.is_done(meeting.stem):
        return
    try:
        claimed = meeting if meeting.path.parent == queue.processing else queue.claim(meeting)
    except Exception as error:
        _notify_failure_safely(meeting.stem, "claim", type(error).__name__, config)
        raise StageFailedError(meeting.stem, "claim") from error
    stage = "prepare"
    try:
        work_dir = queue.work_dir(claimed)
        ensure_transcript_is_not_a_credit_note(claimed)
        run_prepare(claimed, queue.processing, work_dir, config)
        stage = "draft"
        write_draft(work_dir, claimed, config)
        stage = "verify"
        alerts = run_verify(work_dir, config)
        stage = "credit"
        queue.move_attachments(claimed.stem, queue.inbox, queue.processing)
        write_credit(work_dir, claimed.stem, queue.processing)
        stage = "publish"
        publication = publish(work_dir, claimed, alerts, config)
        stage = "notify"
        notify_success(publication, claimed, len(alerts), config)
        stage = "complete"
        record = {
            "stem": claimed.stem,
            "kind": claimed.kind,
            "date": claimed.date.isoformat(),
            "recording": claimed.path.name,
            "branch": publication.branch,
            "pr_url": publication.pr_url,
            "preview_url": publication.preview_url,
            "alert_count": len(alerts),
            "completed_at": _now(),
        }
        queue.complete(claimed, record)
    except Exception as error:
        _handle_failure(queue, claimed, stage, error, config)
        raise StageFailedError(claimed.stem, stage) from error


def scan(config: Config) -> None:
    queue = _queue(config)
    queue.ensure_layout()
    attempted: set[str] = set()
    failed: list[str] = []
    with queue.lock():
        for item in queue.interrupted(config.kinds):
            attempted.add(item.stem)
            _resume(queue, item, config, failed)
        while True:
            remaining = [item for item in queue.pending(config.kinds) if item.stem not in attempted]
            if not remaining:
                break
            item = remaining[0]
            attempted.add(item.stem)
            _run_and_collect(item, config, failed)
        for stem in queue.late_attachments():
            _attach_late(queue, stem, config, failed)
    if failed:
        raise PipelineError(f"{len(failed)} recording(s) failed: {', '.join(failed)}")


def run_file(meeting: MeetingFile, config: Config) -> None:
    queue = _queue(config)
    queue.ensure_layout()
    with queue.lock():
        run_one(meeting, config)


def _resume(queue: Queue, item: MeetingFile, config: Config, failed: list[str]) -> None:
    work_dir = queue.work / item.stem
    for step in CONTAINER_STEPS:
        _remove_container(container_name(step, work_dir))
    try:
        notify_resumed(item.stem, config)
    except Exception as notify_error:
        print(
            f"airgap-scribe: could not send the resume notification: {notify_error}",
            file=sys.stderr,
        )
    _run_and_collect(item, config, failed)


def _run_and_collect(item: MeetingFile, config: Config, failed: list[str]) -> None:
    try:
        run_one(item, config)
    except StageFailedError as error:
        failed.append(f"{error.stem} ({error.stage})")


def ensure_transcript_is_not_a_credit_note(meeting: MeetingFile) -> None:
    if meeting.transcript is None:
        return
    if looks_like_credit_note(meeting.transcript.read_text(encoding="utf-8-sig")):
        raise PipelineError(
            f"{meeting.transcript.name} looks like a credit note (it starts with 'Nome:'); "
            f"rename it to {meeting.stem}-credito.txt"
        )


def _attach_late(queue: Queue, stem: str, config: Config, failed: list[str]) -> None:
    stage = "attach"
    try:
        record = queue.done_record(stem)
        state = pull_request_state(record["pr_url"])
        if state != OPEN:
            stage = "notify"
            _notify_stuck_attachments(queue, stem, record, state, config)
            return
        queue.move_attachments(stem, queue.inbox, queue.done)
        assembly = assemble(stem, queue.done)
        publish_attachments(stem, record["branch"], record["pr_url"], assembly, config)
    except Exception as error:
        _notify_failure_safely(stem, stage, type(error).__name__, config)
        failed.append(f"{stem} ({stage})")


def _notify_stuck_attachments(
    queue: Queue, stem: str, record: dict[str, Any], state: str, config: Config
) -> None:
    notified = set(record.get("late_notified", []))
    names = [path.name for path in attachment_files(queue.inbox, stem)]
    new = [name for name in names if name not in notified]
    if not new:
        return
    notify_late_attachments(stem, new, state, config)
    queue.update_done_record(stem, {**record, "late_notified": sorted(notified | set(new))})


def try_one(audio: Path, kind: str, work_dir: Path, config: Config) -> TryResult:
    if kind not in config.kinds:
        raise PipelineError(f"unknown meeting kind '{kind}'")
    input_dir = work_dir / TRY_INPUT_DIR
    input_dir.mkdir(parents=True, exist_ok=True)
    copied = input_dir / audio.name
    shutil.copyfile(audio, copied)
    meeting = MeetingFile(
        path=copied,
        date=datetime.date.today(),
        kind=kind,
        stem=audio.stem,
        transcript=None,
    )
    run_prepare(meeting, input_dir, work_dir, config)
    write_draft(work_dir, meeting, config)
    alerts = run_verify(work_dir, config)
    return TryResult(
        work_dir=work_dir,
        transcript_raw=work_dir / TRANSCRIPT_RAW,
        transcript_anon=work_dir / TRANSCRIPT_ANON,
        draft=work_dir / DRAFT,
        alerts_file=work_dir / ALERTS,
        alerts=alerts,
    )


def run_prepare(meeting: MeetingFile, input_dir: Path, work_dir: Path, config: Config) -> None:
    write_container_config(config, work_dir / CONTAINER_CONFIG)
    _run_container("prepare", prepare_command(meeting, work_dir, input_dir, config), work_dir)


def run_verify(work_dir: Path, config: Config) -> list[Alert]:
    _run_container("verify", verify_command(work_dir, config), work_dir)
    return read_alerts(work_dir / ALERTS)


def container_name(step: str, work_dir: Path) -> str:
    return f"airgap-scribe-{step}-" + _INVALID_NAME_CHARACTERS.sub("-", work_dir.name)


def container_base_command(name: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]


def prepare_command(
    meeting: MeetingFile, work_dir: Path, input_dir: Path, config: Config
) -> list[str]:
    command = [
        *container_base_command(container_name("prepare", work_dir)),
        "-v",
        f"{work_dir}:{CONTAINER_WORK}",
        "-v",
        f"{input_dir}:{CONTAINER_INPUT}:ro",
        "-v",
        f"{config.paths.models_dir}:{CONTAINER_MODELS}:ro",
        _image(config),
        "prepare",
        "--input",
        f"{CONTAINER_INPUT}/{meeting.path.name}",
    ]
    if meeting.transcript is not None:
        command += ["--transcript", f"{CONTAINER_INPUT}/{meeting.transcript.name}"]
    command += ["--workdir", CONTAINER_WORK, "--config", f"{CONTAINER_WORK}/{CONTAINER_CONFIG}"]
    return command


def verify_command(work_dir: Path, config: Config) -> list[str]:
    return [
        *container_base_command(container_name("verify", work_dir)),
        "-v",
        f"{work_dir}:{CONTAINER_WORK}",
        _image(config),
        "verify",
        "--workdir",
        CONTAINER_WORK,
        "--config",
        f"{CONTAINER_WORK}/{CONTAINER_CONFIG}",
    ]


def read_alerts(path: Path) -> list[Alert]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PipelineError(f"the verify step did not write {path.name}") from error
    return [
        Alert(line=int(item["line"]), token=str(item["token"]), reason=str(item["reason"]))
        for item in raw
    ]


def _run_container(step: str, command: list[str], work_dir: Path) -> None:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr, text=True)
        except FileNotFoundError as error:
            raise ContainerError("docker is not installed or not on PATH") from error
        last_progress = time.time()
        while True:
            try:
                returncode = process.wait(timeout=POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                last_progress = max(last_progress, _latest_change(work_dir))
                if time.time() - last_progress >= STALL_SECONDS:
                    _remove_container(container_name(step, work_dir))
                    process.kill()
                    process.wait()
                    raise ContainerError(
                        f"container step '{step}' made no progress for "
                        f"{STALL_SECONDS // 60} minute(s) and was stopped"
                    ) from None
        if returncode != 0:
            stderr.seek(0)
            raise ContainerError(
                f"container step '{step}' exited with status {returncode}\n"
                f"{stderr.read()[-OUTPUT_TAIL:]}"
            )


def _latest_change(work_dir: Path) -> float:
    latest = 0.0
    for path in work_dir.rglob("*"):
        try:
            latest = max(latest, path.stat().st_mtime)
        except FileNotFoundError:
            continue
    return latest


def _remove_container(name: str) -> None:
    try:
        subprocess.run(
            ["docker", "rm", "--force", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=REMOVE_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return


def _handle_failure(
    queue: Queue, item: MeetingFile, stage: str, error: Exception, config: Config
) -> None:
    details = "".join(traceback.format_exception(error))
    queue.fail(item, f"stage: {stage}\n{details}")
    summary = f"{type(error).__name__}; details in failed/{queue.error_path(item.stem).name}"
    _notify_failure_safely(item.stem, stage, summary, config)


def _notify_failure_safely(stem: str, stage: str, summary: str, config: Config) -> None:
    try:
        notify_failure(stem, stage, summary, config)
    except Exception as notify_error:
        print(
            f"airgap-scribe: could not send the failure notification: {notify_error}",
            file=sys.stderr,
        )


def _queue(config: Config) -> Queue:
    if config.paths.data_dir is None:
        raise PipelineError("the config has no data directory")
    return Queue(config.paths.data_dir)


def _image(config: Config) -> str:
    if config.container is None:
        raise PipelineError("the config has no [container] section")
    return config.container.image


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
