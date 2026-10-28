import datetime
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

from airgap_scribe.config import Config, write_container_config
from airgap_scribe.draft import write_draft
from airgap_scribe.naming import MeetingFile
from airgap_scribe.notify import notify_failure, notify_success
from airgap_scribe.publish import publish
from airgap_scribe.queue import Queue
from airgap_scribe.verify import Alert
from airgap_scribe.workdir import ALERTS, CONTAINER_CONFIG

CONTAINER_WORK = "/work"
CONTAINER_INPUT = "/input"
CONTAINER_MODELS = "/models"
OUTPUT_TAIL = 2000


class PipelineError(Exception):
    pass


class ContainerError(Exception):
    pass


class StageFailedError(Exception):
    def __init__(self, stem: str, stage: str) -> None:
        super().__init__(f"{stem} failed at stage '{stage}'")
        self.stem = stem
        self.stage = stage


def run_one(meeting: MeetingFile, config: Config) -> None:
    queue = _queue(config)
    queue.ensure_layout()
    if queue.is_done(meeting.stem):
        return
    try:
        claimed = queue.claim(meeting)
    except Exception as error:
        _notify_failure_safely(meeting.stem, "claim", type(error).__name__, config)
        raise StageFailedError(meeting.stem, "claim") from error
    stage = "prepare"
    try:
        work_dir = queue.work_dir(claimed)
        write_container_config(config, work_dir / CONTAINER_CONFIG)
        _run_container("prepare", prepare_command(claimed, work_dir, queue, config))
        stage = "draft"
        write_draft(work_dir, claimed, config)
        stage = "verify"
        _run_container("verify", verify_command(work_dir, config))
        alerts = read_alerts(work_dir / ALERTS)
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
    while True:
        remaining = [item for item in queue.pending(config.kinds) if item.stem not in attempted]
        if not remaining:
            break
        item = remaining[0]
        attempted.add(item.stem)
        try:
            run_one(item, config)
        except StageFailedError as error:
            failed.append(f"{error.stem} ({error.stage})")
    if failed:
        raise PipelineError(f"{len(failed)} recording(s) failed: {', '.join(failed)}")


def container_base_command() -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]


def prepare_command(
    meeting: MeetingFile, work_dir: Path, queue: Queue, config: Config
) -> list[str]:
    command = [
        *container_base_command(),
        "-v",
        f"{work_dir}:{CONTAINER_WORK}",
        "-v",
        f"{queue.processing}:{CONTAINER_INPUT}:ro",
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
        *container_base_command(),
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


def _run_container(step: str, command: list[str]) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise ContainerError("docker is not installed or not on PATH") from error
    if result.returncode != 0:
        raise ContainerError(
            f"container step '{step}' exited with status {result.returncode}\n"
            f"{result.stderr[-OUTPUT_TAIL:]}"
        )


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
