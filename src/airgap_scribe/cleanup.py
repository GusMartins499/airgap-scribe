import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from airgap_scribe.config import Config
from airgap_scribe.queue import Queue

MERGED = "MERGED"


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
    return cleaned


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
    for archived in queue.done.glob(f"{stem}.*"):
        if archived != record_path and archived.is_file():
            archived.unlink()


def _mark_cleaned(record_path: Path, record: dict[str, Any]) -> None:
    updated = {
        **record,
        "cleaned_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    temporary = record_path.with_name(f".{record_path.name}.tmp")
    temporary.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", "utf-8")
    temporary.replace(record_path)
