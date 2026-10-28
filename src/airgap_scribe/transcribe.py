import subprocess
from pathlib import Path

WHISPER_CLI = "whisper-cli"
TEXT_SUFFIX = ".txt"


class TranscriptionError(Exception):
    pass


def whisper_command(
    wav: Path,
    output_base: Path,
    model: Path,
    language: str,
    initial_prompt: str,
    threads: int,
) -> list[str]:
    command = [
        WHISPER_CLI,
        "-m",
        str(model),
        "-f",
        str(wav),
        "-l",
        language,
        "-t",
        str(threads),
        "-nt",
        "-np",
        "-otxt",
        "-of",
        str(output_base),
    ]
    if initial_prompt:
        command += ["--prompt", initial_prompt]
    return command


def transcribe(
    wav: Path,
    out_txt: Path,
    model: Path,
    language: str,
    initial_prompt: str,
    threads: int,
) -> None:
    if out_txt.suffix != TEXT_SUFFIX:
        raise TranscriptionError(f"transcript path must end with '{TEXT_SUFFIX}': {out_txt}")
    if not wav.is_file():
        raise TranscriptionError(f"audio file not found: {wav}")
    if not model.is_file():
        raise TranscriptionError(f"whisper model not found: {model}")
    command = whisper_command(
        wav, out_txt.with_suffix(""), model, language, initial_prompt, threads
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise TranscriptionError(f"'{WHISPER_CLI}' is not installed or not on PATH") from error
    if result.returncode != 0:
        raise TranscriptionError(
            f"whisper-cli failed on {wav.name} (exit code {result.returncode}): "
            f"{result.stderr.strip() or 'no error output'}"
        )
    if not out_txt.is_file():
        raise TranscriptionError(f"whisper-cli finished but did not write {out_txt}")
