import subprocess
import wave
from pathlib import Path
from typing import Any

import pytest

from airgap_scribe import transcribe as transcribe_module
from airgap_scribe.transcribe import (
    TranscriptionError,
    available_cpus,
    chunk_windows,
    transcribe,
)

SAMPLE_RATE = 16000


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


def write_silence(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(b"\x00\x00" * int(seconds * SAMPLE_RATE))


@pytest.fixture(autouse=True)
def plenty_of_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcribe_module, "available_cpus", lambda: 64)


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path]:
    wav = tmp_path / "audio.wav"
    write_silence(wav, 1)
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

    assert option_value(command, "-of") == str(tmp_path / "transcript.raw.chunks" / "0000.partial")
    assert out_txt.read_text(encoding="utf-8") == "synthetic transcript\n"
    assert not (tmp_path / "transcript.raw.chunks").exists()
    assert not (tmp_path / "transcript.raw.txt.partial").exists()


def test_chunk_windows_cover_the_audio_without_overlap() -> None:
    assert chunk_windows(650_000, 300_000) == [(0, 300_000), (300_000, 300_000), (600_000, 50_000)]
    assert chunk_windows(0, 300_000) == []


def test_long_audio_is_transcribed_in_chunks_joined_in_order(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    monkeypatch.setattr(transcribe_module, "CHUNK_SECONDS", 1)
    wav, model = inputs
    write_silence(wav, 2.5)
    written: list[str] = []

    def numbered(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        written.append(option_value(command, "-ot") + "+" + option_value(command, "-d"))
        base = command[command.index("-of") + 1]
        Path(base + ".txt").write_text(f"part {len(written)}\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(transcribe_module.subprocess, "run", numbered)
    out_txt = tmp_path / "transcript.raw.txt"

    transcribe(wav, out_txt, model, "pt", "", 2)

    assert written == ["0+1000", "1000+1000", "2000+500"]
    assert out_txt.read_text(encoding="utf-8") == "part 1\npart 2\npart 3\n"


def test_chunks_finished_by_an_interrupted_run_are_not_transcribed_again(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    monkeypatch.setattr(transcribe_module, "CHUNK_SECONDS", 1)
    wav, model = inputs
    write_silence(wav, 2)
    chunks = tmp_path / "transcript.raw.chunks"
    chunks.mkdir()
    (chunks / "0000.txt").write_text("kept from the first run\n", encoding="utf-8")
    fake = FakeWhisper()
    monkeypatch.setattr(transcribe_module.subprocess, "run", fake)
    out_txt = tmp_path / "transcript.raw.txt"

    transcribe(wav, out_txt, model, "pt", "", 2)

    [command] = fake.commands
    assert option_value(command, "-ot") == "1000"
    assert out_txt.read_text(encoding="utf-8") == (
        "kept from the first run\nsynthetic transcript\n"
    )


def test_a_failed_chunk_keeps_the_finished_ones_and_writes_no_transcript(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    monkeypatch.setattr(transcribe_module, "CHUNK_SECONDS", 1)
    wav, model = inputs
    write_silence(wav, 2)
    calls: list[str] = []

    def second_fails(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command[command.index("-of") + 1])
        if len(calls) == 2:
            return subprocess.CompletedProcess(command, 1, "", "killed")
        Path(calls[-1] + ".txt").write_text("first\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(transcribe_module.subprocess, "run", second_fails)
    out_txt = tmp_path / "transcript.raw.txt"

    with pytest.raises(TranscriptionError, match="killed"):
        transcribe(wav, out_txt, model, "pt", "", 2)
    assert (tmp_path / "transcript.raw.chunks" / "0000.txt").read_text() == "first\n"
    assert not out_txt.exists()


def test_more_threads_than_container_cpus_fails_before_running_whisper(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    monkeypatch.setattr(transcribe_module, "available_cpus", lambda: 6)
    fake = FakeWhisper()
    monkeypatch.setattr(transcribe_module.subprocess, "run", fake)
    wav, model = inputs

    with pytest.raises(TranscriptionError, match=r"threads is 8, but the container has only 6"):
        transcribe(wav, tmp_path / "transcript.raw.txt", model, "pt", "", 8)
    assert fake.commands == []


def test_available_cpus_counts_the_cpus_this_process_may_use(tmp_path: Path) -> None:
    assert available_cpus(tmp_path / "no-cgroup") == len(transcribe_module.os.sched_getaffinity(0))


@pytest.mark.parametrize(
    ("cpu_max", "expected"),
    [
        ("1000000 100000\n", 10),
        ("250000 100000\n", 2),
        ("50000 100000\n", 1),
        ("max 100000\n", 64),
        ("garbage\n", 64),
        ("abc 100000\n", 64),
        ("0 100000\n", 64),
    ],
)
def test_available_cpus_honors_the_cgroup_cpu_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cpu_max: str, expected: int
) -> None:
    monkeypatch.setattr(transcribe_module.os, "sched_getaffinity", lambda pid: set(range(64)))
    path = tmp_path / "cpu.max"
    path.write_text(cpu_max, encoding="utf-8")

    assert available_cpus(path) == expected


def test_audio_that_is_not_a_wav_file_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    monkeypatch.setattr(transcribe_module.subprocess, "run", FakeWhisper())
    wav, model = inputs
    wav.write_bytes(b"RIFF")

    with pytest.raises(TranscriptionError, match=r"could not read the duration of audio\.wav"):
        transcribe(wav, tmp_path / "transcript.raw.txt", model, "pt", "", 1)


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
