import subprocess
from pathlib import Path
from typing import Any

import pytest

from airgap_scribe import transcribe as transcribe_module
from airgap_scribe.transcribe import TranscriptionError, transcribe


class FakeWhisper:
    def __init__(self, returncode: int = 0, stderr: str = "", writes_output: bool = True) -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.writes_output = writes_output
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if self.writes_output:
            base = command[command.index("-of") + 1]
            Path(base + ".txt").write_text("synthetic transcript\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path]:
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF")
    model = tmp_path / "models" / "model-under-test"
    model.parent.mkdir()
    model.write_bytes(b"weights")
    return wav, model


def option_value(command: list[str], option: str) -> str:
    return command[command.index(option) + 1]


def run(
    monkeypatch: pytest.MonkeyPatch,
    inputs: tuple[Path, Path],
    out_txt: Path,
    fake: FakeWhisper,
    initial_prompt: str = "Glossary hint.",
) -> list[str]:
    monkeypatch.setattr(transcribe_module.subprocess, "run", fake)
    wav, model = inputs
    transcribe(wav, out_txt, model, "pt", initial_prompt, 6)
    [command] = fake.commands
    return command


def test_whisper_cli_receives_model_audio_language_threads_and_prompt(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    command = run(monkeypatch, inputs, tmp_path / "transcript.raw.txt", FakeWhisper())

    wav, model = inputs
    assert command[0] == "whisper-cli"
    assert option_value(command, "-m") == str(model)
    assert option_value(command, "-f") == str(wav)
    assert option_value(command, "-l") == "pt"
    assert option_value(command, "-t") == "6"
    assert option_value(command, "--prompt") == "Glossary hint."


def test_output_is_plain_text_without_timestamps(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    command = run(monkeypatch, inputs, tmp_path / "transcript.raw.txt", FakeWhisper())

    assert "-otxt" in command
    assert "-nt" in command
    assert not {"-osrt", "-ovtt", "-ojf", "-oj", "-ocsv", "-olrc"} & set(command)


def test_output_base_omits_the_txt_suffix_that_whisper_cli_appends(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    out_txt = tmp_path / "transcript.raw.txt"

    command = run(monkeypatch, inputs, out_txt, FakeWhisper())

    assert option_value(command, "-of") == str(tmp_path / "transcript.raw")
    assert out_txt.read_text(encoding="utf-8") == "synthetic transcript\n"


def test_empty_initial_prompt_is_not_passed(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    command = run(
        monkeypatch, inputs, tmp_path / "transcript.raw.txt", FakeWhisper(), initial_prompt=""
    )

    assert "--prompt" not in command


def test_whisper_failure_raises_with_exit_code_and_error_output(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    fake = FakeWhisper(returncode=3, stderr="failed to load model\n", writes_output=False)

    with pytest.raises(TranscriptionError, match=r"exit code 3\): failed to load model$"):
        run(monkeypatch, inputs, tmp_path / "transcript.raw.txt", fake)


def test_whisper_failure_without_error_output_says_so(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    fake = FakeWhisper(returncode=1, writes_output=False)

    with pytest.raises(TranscriptionError, match="no error output"):
        run(monkeypatch, inputs, tmp_path / "transcript.raw.txt", fake)


def test_successful_exit_without_transcript_file_raises(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    with pytest.raises(TranscriptionError, match="did not write"):
        run(monkeypatch, inputs, tmp_path / "transcript.raw.txt", FakeWhisper(writes_output=False))


def test_missing_whisper_binary_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    def missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("whisper-cli")

    monkeypatch.setattr(transcribe_module.subprocess, "run", missing)
    wav, model = inputs

    with pytest.raises(TranscriptionError, match="not installed"):
        transcribe(wav, tmp_path / "transcript.raw.txt", model, "pt", "", 1)


@pytest.mark.parametrize("missing", ["wav", "model"])
def test_missing_audio_or_model_raises_before_running_whisper(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path, missing: str
) -> None:
    fake = FakeWhisper()
    monkeypatch.setattr(transcribe_module.subprocess, "run", fake)
    wav, model = inputs
    (wav if missing == "wav" else model).unlink()

    with pytest.raises(TranscriptionError, match="not found"):
        transcribe(wav, tmp_path / "transcript.raw.txt", model, "pt", "", 1)
    assert fake.commands == []


def test_transcript_path_without_txt_suffix_is_rejected(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    fake = FakeWhisper()
    monkeypatch.setattr(transcribe_module.subprocess, "run", fake)
    wav, model = inputs

    with pytest.raises(TranscriptionError, match=r"must end with '\.txt'"):
        transcribe(wav, tmp_path / "transcript.md", model, "pt", "", 1)
    assert fake.commands == []
