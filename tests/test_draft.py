import sys
from pathlib import Path

import pytest
from host_config import make_config, make_meeting, write_executable

from airgap_scribe.draft import DraftError, enforce_front_matter, write_draft
from airgap_scribe.workdir import DRAFT, TRANSCRIPT_ANON

MODEL_OUTPUT = """---
title: Lessons from a long journey
summary: What [PERSON_1] taught the audience
date: 1999-01-01
kind: wrong
---

## Opening

The speaker opened the talk.
"""


def fake_drafter(tmp_path: Path, output: str, exit_code: int = 0) -> Path:
    output_file = tmp_path / "drafter-output.md"
    output_file.write_text(output, encoding="utf-8")
    return write_executable(
        tmp_path / "bin" / "fake-drafter",
        f"""#!{sys.executable}
import os
import pathlib
import sys

base = pathlib.Path({str(tmp_path)!r})
(base / "drafter-stdin.txt").write_text(sys.stdin.read(), encoding="utf-8")
(base / "drafter-argv.txt").write_text("\\n".join(sys.argv[1:]), encoding="utf-8")
(base / "drafter-cwd.txt").write_text(os.getcwd(), encoding="utf-8")
sys.stdout.write((base / "drafter-output.md").read_text(encoding="utf-8"))
sys.stderr.write("drafter diagnostics")
sys.exit({exit_code})
""",
    )


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    (directory / TRANSCRIPT_ANON).write_text("[PERSON_1] spoke about patience.\n", encoding="utf-8")
    return directory


def test_write_draft_sends_prompt_and_transcript_on_stdin(tmp_path: Path, work_dir: Path) -> None:
    drafter = fake_drafter(tmp_path, MODEL_OUTPUT)
    config = make_config(tmp_path, command=(str(drafter), "--flag", ""))

    write_draft(work_dir, make_meeting(tmp_path / "a.m4a"), config)

    stdin = (tmp_path / "drafter-stdin.txt").read_text(encoding="utf-8")
    assert stdin == ("Write an article from this transcript.\n\n[PERSON_1] spoke about patience.\n")
    assert (tmp_path / "drafter-argv.txt").read_text(encoding="utf-8") == "--flag\n"


def test_write_draft_runs_the_command_outside_the_work_dir(tmp_path: Path, work_dir: Path) -> None:
    drafter = fake_drafter(tmp_path, MODEL_OUTPUT)
    config = make_config(tmp_path, command=(str(drafter),))

    write_draft(work_dir, make_meeting(tmp_path / "a.m4a"), config)

    cwd = Path((tmp_path / "drafter-cwd.txt").read_text(encoding="utf-8"))
    assert cwd != work_dir
    assert not cwd.exists()


def test_write_draft_enforces_date_and_kind_from_the_file_name(
    tmp_path: Path, work_dir: Path
) -> None:
    drafter = fake_drafter(tmp_path, MODEL_OUTPUT)
    config = make_config(tmp_path, command=(str(drafter),))

    path = write_draft(work_dir, make_meeting(tmp_path / "a.m4a"), config)

    assert path == work_dir / DRAFT
    assert path.read_text(encoding="utf-8") == (
        "---\n"
        "title: Lessons from a long journey\n"
        "summary: What [PERSON_1] taught the audience\n"
        "date: 2026-11-09\n"
        "kind: talk\n"
        "---\n"
        "\n"
        "## Opening\n"
        "\n"
        "The speaker opened the talk.\n"
    )


def test_write_draft_fails_when_the_command_exits_with_an_error(
    tmp_path: Path, work_dir: Path
) -> None:
    drafter = fake_drafter(tmp_path, MODEL_OUTPUT, exit_code=3)
    config = make_config(tmp_path, command=(str(drafter),))

    with pytest.raises(DraftError, match="status 3: drafter diagnostics"):
        write_draft(work_dir, make_meeting(tmp_path / "a.m4a"), config)
    assert not (work_dir / DRAFT).exists()


def test_write_draft_fails_when_the_command_is_missing(tmp_path: Path, work_dir: Path) -> None:
    config = make_config(tmp_path, command=(str(tmp_path / "missing-drafter"),))

    with pytest.raises(DraftError, match="not found"):
        write_draft(work_dir, make_meeting(tmp_path / "a.m4a"), config)


def test_write_draft_fails_for_a_kind_without_prompt(tmp_path: Path, work_dir: Path) -> None:
    config = make_config(tmp_path)

    with pytest.raises(DraftError, match="kind 'circle'"):
        write_draft(work_dir, make_meeting(tmp_path / "a.m4a", kind="circle"), config)


def test_write_draft_requires_a_drafting_section(tmp_path: Path, work_dir: Path) -> None:
    from dataclasses import replace

    config = replace(make_config(tmp_path), drafting=None)

    with pytest.raises(DraftError, match=r"\[drafting\]"):
        write_draft(work_dir, make_meeting(tmp_path / "a.m4a"), config)


@pytest.mark.parametrize(
    "command",
    [
        ("claude", "-p"),
        ("/usr/local/bin/claude", "-p", "--tools", "default"),
        ("claude", "-p", "--tools"),
    ],
)
def test_claude_commands_that_keep_tools_enabled_are_refused(
    tmp_path: Path, work_dir: Path, command: tuple[str, ...]
) -> None:
    config = make_config(tmp_path, command=command)

    with pytest.raises(DraftError, match="disable every tool"):
        write_draft(work_dir, make_meeting(tmp_path / "a.m4a"), config)


@pytest.mark.parametrize(
    "command",
    [
        ("claude", "-p", "--tools", "", "--strict-mcp-config"),
        ("claude", "-p", "--tools="),
    ],
)
def test_claude_commands_with_every_tool_disabled_are_run(
    tmp_path: Path,
    work_dir: Path,
    command: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_drafter(tmp_path, MODEL_OUTPUT)
    write_executable(tmp_path / "bin" / "claude", (tmp_path / "bin" / "fake-drafter").read_text())
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    config = make_config(tmp_path, command=command)

    write_draft(work_dir, make_meeting(tmp_path / "a.m4a"), config)

    assert (work_dir / DRAFT).is_file()


def test_front_matter_wrapped_in_a_code_fence_is_unwrapped() -> None:
    output = "```markdown\n---\ntitle: A title\n---\n\nBody.\n```\n"

    draft = enforce_front_matter(output, make_meeting(Path("a.m4a")))

    assert draft == "---\ntitle: A title\ndate: 2026-11-09\nkind: talk\n---\n\nBody.\n"


def test_nested_values_of_replaced_keys_are_dropped() -> None:
    output = "---\ntitle: A title\nkind:\n  - one\n  - two\ntags:\n  - kept\n---\nBody.\n"

    draft = enforce_front_matter(output, make_meeting(Path("a.m4a")))

    assert draft == (
        "---\ntitle: A title\ntags:\n  - kept\ndate: 2026-11-09\nkind: talk\n---\n\nBody.\n"
    )


def test_a_block_scalar_title_is_accepted() -> None:
    output = "---\ntitle: >-\n  A folded title\n---\nBody.\n"

    draft = enforce_front_matter(output, make_meeting(Path("a.m4a")))

    assert draft.startswith("---\ntitle: >-\n  A folded title\ndate: 2026-11-09\n")


def test_output_without_front_matter_is_rejected() -> None:
    with pytest.raises(DraftError, match="no front matter"):
        enforce_front_matter("# Title\n\nBody.\n", make_meeting(Path("a.m4a")))


def test_unclosed_front_matter_is_rejected() -> None:
    with pytest.raises(DraftError, match="not closed"):
        enforce_front_matter("---\ntitle: A title\n\nBody.\n", make_meeting(Path("a.m4a")))


@pytest.mark.parametrize(
    "front_matter",
    [
        "summary: No title here",
        'title: ""',
        "title:",
        "title: |\nsummary: block without content",
        "subtitle: Not a title",
    ],
)
def test_front_matter_without_title_is_rejected(front_matter: str) -> None:
    with pytest.raises(DraftError, match="no title"):
        enforce_front_matter(f"---\n{front_matter}\n---\nBody.\n", make_meeting(Path("a.m4a")))
