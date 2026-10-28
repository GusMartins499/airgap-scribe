import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"
SAMPLE_RATE = 16000


class AudioError(Exception):
    pass


def ffmpeg_command(src: Path, dst: Path) -> list[str]:
    return [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-af",
        "loudnorm",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]


def to_wav(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise AudioError(f"input audio not found: {src}")
    try:
        result = subprocess.run(
            ffmpeg_command(src, dst), capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise AudioError(f"'{FFMPEG}' is not installed or not on PATH") from error
    if result.returncode != 0:
        raise AudioError(
            f"ffmpeg failed to convert {src.name} (exit code {result.returncode}): "
            f"{result.stderr.strip() or 'no error output'}"
        )
