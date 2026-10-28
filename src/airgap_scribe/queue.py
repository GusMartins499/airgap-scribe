import json
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from airgap_scribe.naming import MeetingFile, parse_meeting_file

INBOX = "inbox"
PROCESSING = "processing"
DONE = "done"
FAILED = "failed"
WORK = "work"
MODELS = "models"


class Queue:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.inbox = data_dir / INBOX
        self.processing = data_dir / PROCESSING
        self.done = data_dir / DONE
        self.failed = data_dir / FAILED
        self.work = data_dir / WORK
        self.models = data_dir / MODELS

    def ensure_layout(self) -> None:
        for directory in (
            self.inbox,
            self.processing,
            self.done,
            self.failed,
            self.work,
            self.models,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def pending(self, kinds: Iterable[str]) -> list[MeetingFile]:
        if not self.inbox.is_dir():
            return []
        allowed_kinds = tuple(kinds)
        items = []
        for entry in self.inbox.iterdir():
            if not entry.is_file():
                continue
            item = parse_meeting_file(entry, allowed_kinds)
            if item is None or self.is_done(item.stem):
                continue
            items.append(item)
        return sorted(items, key=lambda item: (item.date, item.stem, item.path.name))

    def is_done(self, stem: str) -> bool:
        return self.done_record_path(stem).is_file()

    def done_record_path(self, stem: str) -> Path:
        return self.done / f"{stem}.json"

    def error_path(self, stem: str) -> Path:
        return self.failed / f"{stem}.error.txt"

    def claim(self, item: MeetingFile) -> MeetingFile:
        return self._move_item(item, self.processing)

    def complete(self, item: MeetingFile, record: dict[str, Any]) -> None:
        _write_atomically(
            self.done_record_path(item.stem),
            json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        )
        self._move_item(item, self.done)

    def fail(self, item: MeetingFile, error: str) -> None:
        self._move_item(item, self.failed)
        _write_atomically(self.error_path(item.stem), error)

    def work_dir(self, item: MeetingFile) -> Path:
        directory = self.work / item.stem
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def done_records(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.done.is_dir():
            return []
        return [
            (path, json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(self.done.glob("*.json"))
        ]

    def _move_item(self, item: MeetingFile, target_dir: Path) -> MeetingFile:
        path = _move(item.path, target_dir)
        transcript = None if item.transcript is None else _move(item.transcript, target_dir)
        return replace(item, path=path, transcript=transcript)


def _move(source: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if target.exists():
        raise FileExistsError(f"refusing to overwrite {target}")
    return source.rename(target)


def _write_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
