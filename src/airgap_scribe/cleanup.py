import datetime
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from airgap_scribe.config import Config
from airgap_scribe.credit import attachment_files, attachment_stem
from airgap_scribe.naming import AUDIO_EXTENSIONS
from airgap_scribe.queue import Queue

MERGED = "MERGED"
ORPHAN_SECONDS = 7 * 24 * 60 * 60


class CleanupError(Exception):
    pass


def cleanup(config: Config) -> list[str]:
    if config.paths.data_dir is None:
        raise CleanupError("the config has no data directory")
    queue = Queue(config.paths.data_dir)
    cleaned = []
    for record_path, record in queue.done_records():
        if record.get("cleaned_at") or not record.get("pr_url"):
            continue
        stem = record_path.stem
        try:
            state = pull_request_state(record["pr_url"])
        except CleanupError as error:
            print(f"airgap-scribe: skipping {stem}: {error}", file=sys.stderr)
            continue
        if state != MERGED:
            continue
        _remove_data(queue, stem, record_path)
        _mark_cleaned(record_path, record)
        cleaned.append(stem)
    cleaned += remove_orphan_attachments(queue)
    return cleaned


def remove_orphan_attachments(queue: Queue, now: float | None = None) -> list[str]:
    if not queue.inbox.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - ORPHAN_SECONDS
    removed = []
    for entry in sorted(queue.inbox.iterdir()):
        stem = attachment_stem(entry.name)
        if stem is None or not entry.is_file() or entry.stat().st_mtime > cutoff:
            continue
        if _has_recording(queue.inbox, stem) or _has_recording(queue.processing, stem):
            continue
        entry.unlink()
        removed.append(entry.name)
    return removed


def _has_recording(directory: Path, stem: str) -> bool:
    return any(
        (directory / f"{stem}.{extension}").is_file()
        for extension in (*AUDIO_EXTENSIONS, *(e.upper() for e in AUDIO_EXTENSIONS))
    )


def pull_request_state(pr_url: str) -> str:
    try:
        result = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "state"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise CleanupError("gh is not installed or not on PATH") from error
    if result.returncode != 0:
        raise CleanupError(f"gh pr view exited with status {result.returncode}")
    try:
        return str(json.loads(result.stdout)["state"])
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise CleanupError("gh pr view returned an unexpected answer") from error


def _remove_data(queue: Queue, stem: str, record_path: Path) -> None:
    shutil.rmtree(queue.work / stem, ignore_errors=True)
    for archived in [*queue.done.glob(f"{stem}.*"), *attachment_files(queue.done, stem)]:
        if archived != record_path and archived.is_file():
            archived.unlink(missing_ok=True)


def _mark_cleaned(record_path: Path, record: dict[str, Any]) -> None:
    updated = {
        **record,
        "cleaned_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    temporary = record_path.with_name(f".{record_path.name}.tmp")
    temporary.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", "utf-8")
    temporary.replace(record_path)
