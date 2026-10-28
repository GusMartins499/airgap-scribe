import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from airgap_scribe.config import Config
from airgap_scribe.naming import MeetingFile
from airgap_scribe.workdir import DRAFT, TRANSCRIPT_ANON

FRONT_MATTER_DELIMITER = "---"
ENFORCED_KEYS = ("date", "kind")
STDERR_TAIL = 500
BLOCK_SCALAR_INDICATORS = frozenset({"|", ">", "|-", ">-"})
EMPTY_SCALARS = frozenset({"", '""', "''"})

_CODE_FENCE = re.compile(r"\A```[A-Za-z]*\n(?P<body>.*)\n```\s*\Z", re.DOTALL)
_TOP_LEVEL_KEY = re.compile(r"(?P<key>[A-Za-z0-9_-]+)\s*:(?P<value>.*)")


class DraftError(Exception):
    pass


def write_draft(work_dir: Path, meeting: MeetingFile, config: Config) -> Path:
    if config.drafting is None:
        raise DraftError("the config has no [drafting] section")
    prompt_file = config.drafting.prompts.get(meeting.kind)
    if prompt_file is None:
        raise DraftError(f"no drafting prompt configured for kind '{meeting.kind}'")
    command = config.drafting.command
    ensure_tools_disabled(command)
    transcript = (work_dir / TRANSCRIPT_ANON).read_text(encoding="utf-8")
    prompt = build_prompt(prompt_file.read_text(encoding="utf-8"), transcript)
    output = run_drafting_command(command, prompt)
    draft = enforce_front_matter(output, meeting)
    target = work_dir / DRAFT
    target.write_text(draft, encoding="utf-8")
    return target


def build_prompt(instructions: str, transcript: str) -> str:
    return instructions.rstrip() + "\n\n" + transcript.strip() + "\n"


def ensure_tools_disabled(command: Sequence[str]) -> None:
    if Path(command[0]).name != "claude":
        return
    arguments = list(command[1:])
    for index, argument in enumerate(arguments):
        if argument == "--tools=":
            return
        if argument == "--tools" and index + 1 < len(arguments) and arguments[index + 1] == "":
            return
    raise DraftError('the claude drafting command must disable every tool with --tools ""')


def run_drafting_command(command: Sequence[str], prompt: str) -> str:
    with tempfile.TemporaryDirectory(prefix="airgap-scribe-draft-") as empty_dir:
        try:
            result = subprocess.run(
                list(command),
                input=prompt,
                capture_output=True,
                text=True,
                cwd=empty_dir,
                check=False,
            )
        except FileNotFoundError as error:
            raise DraftError(f"drafting command not found: {command[0]}") from error
    if result.returncode != 0:
        raise DraftError(
            f"drafting command exited with status {result.returncode}: "
            f"{result.stderr.strip()[-STDERR_TAIL:]}"
        )
    return result.stdout


def enforce_front_matter(output: str, meeting: MeetingFile) -> str:
    text = _unwrap_code_fence(output.strip())
    lines = text.split("\n")
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        raise DraftError("the drafting output has no front matter")
    closing = _closing_delimiter(lines)
    front_matter = _without_keys(lines[1:closing], ENFORCED_KEYS)
    if not _has_title(front_matter):
        raise DraftError("the drafting output front matter has no title")
    front_matter += [f"date: {meeting.date.isoformat()}", f"kind: {meeting.kind}"]
    body = "\n".join(lines[closing + 1 :]).strip("\n")
    document = [FRONT_MATTER_DELIMITER, *front_matter, FRONT_MATTER_DELIMITER, "", body]
    return "\n".join(document) + "\n"


def _unwrap_code_fence(text: str) -> str:
    match = _CODE_FENCE.match(text)
    return match["body"].strip() if match else text


def _closing_delimiter(lines: list[str]) -> int:
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONT_MATTER_DELIMITER:
            return index
    raise DraftError("the drafting output front matter is not closed")


def _without_keys(lines: list[str], keys: Sequence[str]) -> list[str]:
    kept = []
    skipping = False
    for line in lines:
        if line[:1].isspace() or line.startswith("- "):
            if not skipping:
                kept.append(line)
            continue
        match = _TOP_LEVEL_KEY.match(line)
        skipping = match is not None and match["key"] in keys
        if not skipping:
            kept.append(line)
    return kept


def _has_title(lines: list[str]) -> bool:
    for index, line in enumerate(lines):
        match = _TOP_LEVEL_KEY.match(line)
        if match is None or match["key"] != "title":
            continue
        value = match["value"].strip()
        if value not in BLOCK_SCALAR_INDICATORS:
            return value not in EMPTY_SCALARS
        following = lines[index + 1] if index + 1 < len(lines) else ""
        return following[:1].isspace() and bool(following.strip())
    return False
