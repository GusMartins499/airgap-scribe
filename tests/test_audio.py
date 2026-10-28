import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

import pytest

from airgap_scribe import audio
from airgap_scribe.audio import AudioError, to_wav


class FakeRun:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "input.m4a"
    path.write_bytes(b"synthetic")
    return path


def option_value(command: list[str], option: str) -> str:
    return command[command.index(option) + 1]


def test_conversion_requests_mono_16khz_pcm_with_loudness_normalization(
    monkeypatch: pytest.MonkeyPatch, source: Path, tmp_path: Path
) -> None:
    fake = FakeRun()
    monkeypatch.setattr(audio.subprocess, "run", fake)
    destination = tmp_path / "audio.wav"

    to_wav(source, destination)

    [command] = fake.commands
    assert command[0] == "ffmpeg"
    assert option_value(command, "-i") == str(source)
    assert option_value(command, "-ac") == "1"
    assert option_value(command, "-ar") == "16000"
    assert option_value(command, "-c:a") == "pcm_s16le"
    assert option_value(command, "-af") == "loudnorm"
    assert "-y" in command
    assert "-nostdin" in command
    assert command[-1] == str(destination)


def test_ffmpeg_failure_raises_with_exit_code_and_error_output(
    monkeypatch: pytest.MonkeyPatch, source: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(audio.subprocess, "run", FakeRun(1, "Invalid data found\n"))

    with pytest.raises(AudioError, match=r"exit code 1\): Invalid data found$"):
        to_wav(source, tmp_path / "audio.wav")


def test_ffmpeg_failure_without_error_output_says_so(
    monkeypatch: pytest.MonkeyPatch, source: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(audio.subprocess, "run", FakeRun(183))

    with pytest.raises(AudioError, match="no error output"):
        to_wav(source, tmp_path / "audio.wav")


def test_missing_ffmpeg_binary_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, source: Path, tmp_path: Path
) -> None:
    def missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(audio.subprocess, "run", missing)

    with pytest.raises(AudioError, match="not installed"):
        to_wav(source, tmp_path / "audio.wav")


def test_missing_input_file_raises_before_running_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeRun()
    monkeypatch.setattr(audio.subprocess, "run", fake)

    with pytest.raises(AudioError, match="input audio not found"):
        to_wav(tmp_path / "absent.m4a", tmp_path / "audio.wav")
    assert fake.commands == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_real_ffmpeg_turns_a_stereo_tone_into_mono_16khz_16bit_wav(tmp_path: Path) -> None:
    tone = tmp_path / "tone.ogg"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=2",
            "-ac",
            "2",
            "-c:a",
            "libvorbis",
            str(tone),
        ],
        check=True,
    )
    destination = tmp_path / "audio.wav"

    to_wav(tone, destination)

    with wave.open(str(destination), "rb") as result:
        assert result.getnchannels() == 1
        assert result.getframerate() == 16000
        assert result.getsampwidth() == 2
        assert result.getnframes() / result.getframerate() == pytest.approx(2, abs=0.1)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_real_ffmpeg_rejects_a_file_that_is_not_audio(tmp_path: Path) -> None:
    garbage = tmp_path / "broken.m4a"
    garbage.write_bytes(b"this is not audio")

    with pytest.raises(AudioError, match=r"broken\.m4a"):
        to_wav(garbage, tmp_path / "audio.wav")
