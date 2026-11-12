import os
import shutil
import subprocess
import wave
from pathlib import Path

WHISPER_CLI = "whisper-cli"
TEXT_SUFFIX = ".txt"
CHUNK_SECONDS = 300
CGROUP_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
CHUNKS_SUFFIX = ".chunks"
PARTIAL_SUFFIX = ".partial"


class TranscriptionError(Exception):
    pass


def whisper_command(
    wav: Path,
    output_base: Path,
    model: Path,
    language: str,
    initial_prompt: str,
    threads: int,
    offset_ms: int = 0,
    duration_ms: int = 0,
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
    if duration_ms:
        command += ["-ot", str(offset_ms), "-d", str(duration_ms)]
    if initial_prompt:
        command += ["--prompt", initial_prompt]
    return command


def available_cpus(cpu_max: Path = CGROUP_CPU_MAX) -> int:
    cpus = len(os.sched_getaffinity(0))
    quota = cgroup_cpu_quota(cpu_max)
    return cpus if quota is None else max(1, min(cpus, int(quota)))


def cgroup_cpu_quota(cpu_max: Path = CGROUP_CPU_MAX) -> float | None:
    try:
        fields = cpu_max.read_text(encoding="utf-8").split()
    except OSError:
        return None
    if len(fields) != 2 or fields[0] == "max":
        return None
    try:
        quota, period = int(fields[0]), int(fields[1])
    except ValueError:
        return None
    return quota / period if quota > 0 and period > 0 else None


def chunk_windows(total_ms: int, chunk_ms: int) -> list[tuple[int, int]]:
    return [(start, min(chunk_ms, total_ms - start)) for start in range(0, total_ms, chunk_ms)]


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
    cpus = available_cpus()
    if threads > cpus:
        raise TranscriptionError(
            f"[transcription] threads is {threads}, but the container has only {cpus} CPU(s); "
            f"set threads to {cpus} or less"
        )
    if not wav.is_file():
        raise TranscriptionError(f"audio file not found: {wav}")
    if not model.is_file():
        raise TranscriptionError(f"whisper model not found: {model}")
    chunks_dir = out_txt.with_suffix(CHUNKS_SUFFIX)
    chunks_dir.mkdir(exist_ok=True)
    parts = []
    for index, (offset_ms, duration_ms) in enumerate(
        chunk_windows(_duration_ms(wav), CHUNK_SECONDS * 1000)
    ):
        part = chunks_dir / f"{index:04d}{TEXT_SUFFIX}"
        if not part.is_file():
            _transcribe_window(
                wav, part, model, language, initial_prompt, threads, offset_ms, duration_ms
            )
        parts.append(part)
    partial = out_txt.with_name(out_txt.name + PARTIAL_SUFFIX)
    partial.write_text(
        "".join(part.read_text(encoding="utf-8") for part in parts), encoding="utf-8"
    )
    partial.replace(out_txt)
    shutil.rmtree(chunks_dir)


def _transcribe_window(
    wav: Path,
    part: Path,
    model: Path,
    language: str,
    initial_prompt: str,
    threads: int,
    offset_ms: int,
    duration_ms: int,
) -> None:
    output_base = part.with_suffix(PARTIAL_SUFFIX)
    command = whisper_command(
        wav, output_base, model, language, initial_prompt, threads, offset_ms, duration_ms
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
    written = output_base.with_name(output_base.name + TEXT_SUFFIX)
    if not written.is_file():
        raise TranscriptionError(f"whisper-cli finished but did not write {written}")
    written.replace(part)


def _duration_ms(wav: Path) -> int:
    try:
        with wave.open(str(wav), "rb") as audio:
            return audio.getnframes() * 1000 // audio.getframerate()
    except (wave.Error, EOFError) as error:
        raise TranscriptionError(f"could not read the duration of {wav.name}: {error}") from error
