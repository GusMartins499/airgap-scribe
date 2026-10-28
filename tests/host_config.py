import datetime
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from airgap_scribe.config import (
    AnonymizationSettings,
    Config,
    ContainerSettings,
    DraftingSettings,
    NotifySettings,
    PathsSettings,
    PublishSettings,
    TranscriptionSettings,
    VerifySettings,
)
from airgap_scribe.naming import MeetingFile

TOPIC_ENV = "AIRGAP_SCRIBE_TEST_TOPIC"


def make_config(
    tmp_path: Path,
    *,
    command: Sequence[str] = ("fake-drafter",),
    prompts: dict[str, Path] | None = None,
    repo_dir: Path | None = None,
    branch_prefix: str = "draft/",
    preview_url_template: str = "https://{branch_slug}.example.pages.dev",
    server: str = "http://127.0.0.1:9",
    image: str = "example.org/airgap-scribe:test",
) -> Config:
    data_dir = tmp_path / "data"
    if prompts is None:
        prompt = tmp_path / "prompts" / "talk.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("Write an article from this transcript.\n", encoding="utf-8")
        prompts = {"talk": prompt}
    return Config(
        paths=PathsSettings(data_dir=data_dir, models_dir=data_dir / "models"),
        transcription=TranscriptionSettings(
            whisper_model="ggml-test.bin",
            language="pt",
            initial_prompt="",
            threads=2,
        ),
        anonymization=AnonymizationSettings(spacy_model="pt_core_news_lg", labels=("PER",)),
        verify=VerifySettings(),
        drafting=DraftingSettings(command=tuple(command), prompts=prompts),
        publish=PublishSettings(
            repo_dir=repo_dir or tmp_path / "content",
            base_branch="main",
            content_dir="src/content/posts",
            branch_prefix=branch_prefix,
            preview_url_template=preview_url_template,
        ),
        notify=NotifySettings(server=server, topic_env=TOPIC_ENV),
        container=ContainerSettings(image=image),
    )


def make_meeting(path: Path, kind: str = "talk", day: str = "2026-11-09") -> MeetingFile:
    return MeetingFile(
        path=path,
        date=datetime.date.fromisoformat(day),
        kind=kind,
        stem=f"{day}_{kind}",
        transcript=None,
    )


def write_executable(path: Path, script: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def prepend_path(directory: Path) -> str:
    return f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"
