from pathlib import Path


def transcribe(
    wav: Path,
    out_txt: Path,
    model: Path,
    language: str,
    initial_prompt: str,
    threads: int,
) -> None:
    raise NotImplementedError
