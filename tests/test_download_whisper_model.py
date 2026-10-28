import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "download-whisper-model.sh"
MODEL = "ggml-synthetic.bin"
CONTENT = b"synthetic model weights"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None,
    reason="bash and curl are required",
)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    path = tmp_path / "repository"
    path.mkdir()
    (path / MODEL).write_bytes(CONTENT)
    return path


def download(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "WHISPER_MODEL_BASE_URL": repository.as_uri()}
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def test_model_with_matching_checksum_is_saved_in_the_target_directory(
    repository: Path, tmp_path: Path
) -> None:
    target = tmp_path / "models"

    result = download(
        repository, "-m", MODEL, "-s", hashlib.sha256(CONTENT).hexdigest(), str(target)
    )

    assert result.returncode == 0, result.stderr
    assert (target / MODEL).read_bytes() == CONTENT
    assert sorted(path.name for path in target.iterdir()) == [MODEL]


def test_checksum_mismatch_fails_and_leaves_no_file_behind(
    repository: Path, tmp_path: Path
) -> None:
    target = tmp_path / "models"

    result = download(repository, "-m", MODEL, "-s", "0" * 64, str(target))

    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert list(target.iterdir()) == []


def test_verified_existing_model_is_not_downloaded_again(repository: Path, tmp_path: Path) -> None:
    target = tmp_path / "models"
    target.mkdir()
    (target / MODEL).write_bytes(CONTENT)
    (repository / MODEL).unlink()

    result = download(
        repository, "-m", MODEL, "-s", hashlib.sha256(CONTENT).hexdigest(), str(target)
    )

    assert result.returncode == 0, result.stderr
    assert "already present" in result.stdout


@pytest.mark.parametrize("name", ["../escape.bin", "model.gguf", "ggml-tiny"])
def test_unexpected_model_names_are_rejected(repository: Path, tmp_path: Path, name: str) -> None:
    result = download(repository, "-m", name, "-s", "0" * 64, str(tmp_path / "models"))

    assert result.returncode == 1
    assert "unexpected model name" in result.stderr
    assert not (tmp_path / "models").exists()


def test_missing_target_directory_argument_prints_usage(repository: Path) -> None:
    result = download(repository)

    assert result.returncode == 2
    assert "usage:" in result.stderr
