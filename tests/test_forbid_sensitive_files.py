import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "forbid-sensitive-files.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")


def check(*files: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *files], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(
    "path",
    [
        "recording.m4a",
        "WhatsApp Audio 2026-10-28.mp4",
        "inbox/2026-11-09_talk.MP3",
        "a/b/clip.opus",
        "clip.ogg",
        "clip.aac",
        "clip.wav",
        "clip.flac",
        "models/ggml-large-v3-turbo-q5_0.bin",
        "model.gguf",
        "ggml-tiny",
        "work/x/transcript.raw.txt",
        "notes.anon.txt",
        "tests/fixtures/transcript.txt",
        "work/x/entities.json",
        "mapping.json",
        "alerts.json",
        ".env",
        "deploy/airgap-scribe.env",
        "notes.txt",
        "examples/2026-11-09_talk.md",
        "2026-11-09_talk.json",
    ],
)
def test_blocks_sensitive_files(path: str) -> None:
    result = check(path)

    assert result.returncode == 1
    assert path in result.stderr


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "src/airgap_scribe/naming.py",
        "examples/airgap-scribe.env.example",
        "examples/pipeline.example.toml",
        "tests/fixtures/sample.txt",
        "data/names/first-names.txt",
        "tests/test_naming.py",
        "tests/2026-11-09_talk_fixture.py",
    ],
)
def test_allows_regular_project_files(path: str) -> None:
    assert check(path).returncode == 0


def test_reports_every_forbidden_file_in_one_run() -> None:
    result = check("a.m4a", "README.md", "mapping.json")

    assert result.returncode == 1
    assert "a.m4a" in result.stderr
    assert "mapping.json" in result.stderr
    assert "README.md" not in result.stderr


def test_succeeds_without_arguments() -> None:
    assert check().returncode == 0
