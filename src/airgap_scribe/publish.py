import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from airgap_scribe.config import Config, PublishSettings
from airgap_scribe.naming import MeetingFile
from airgap_scribe.verify import Alert
from airgap_scribe.workdir import DRAFT

BRANCH_SLUG_MAX_LENGTH = 28
STDERR_TAIL = 500

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


class PublishError(Exception):
    pass


@dataclass(frozen=True)
class Publication:
    branch: str
    pr_url: str
    preview_url: str


def publish(
    work_dir: Path,
    meeting: MeetingFile,
    alerts: Sequence[Alert],
    config: Config,
) -> Publication:
    if config.publish is None:
        raise PublishError("the config has no [publish] section")
    settings = config.publish
    branch = f"{settings.branch_prefix}{meeting.stem}"
    draft = (work_dir / DRAFT).read_text(encoding="utf-8")
    _commit_and_push(settings, branch, meeting.stem, draft)
    pr_url = _open_or_reuse_pull_request(settings, branch, meeting.stem, alerts)
    return Publication(
        branch=branch,
        pr_url=pr_url,
        preview_url=preview_url(settings.preview_url_template, branch),
    )


def branch_slug(branch: str) -> str:
    slug = _NON_ALPHANUMERIC.sub("-", branch.lower())[:BRANCH_SLUG_MAX_LENGTH]
    return slug.strip("-")


def preview_url(template: str, branch: str) -> str:
    return template.replace("{branch_slug}", branch_slug(branch))


def pull_request_body(stem: str, alerts: Sequence[Alert]) -> str:
    lines = [
        f"Draft generated from the recording `{stem}`.",
        "",
        f"Name-check alerts: {len(alerts)}",
    ]
    if alerts:
        lines += ["", "| Line | Token | Reason |", "| --- | --- | --- |"]
        lines += [f"| {a.line} | `{a.token}` | {a.reason} |" for a in alerts]
    lines += ["", "Review and edit the draft before merging."]
    return "\n".join(lines) + "\n"


def _commit_and_push(settings: PublishSettings, branch: str, stem: str, draft: str) -> None:
    repo = settings.repo_dir
    base = settings.base_branch
    _run(["git", "-C", str(repo), "fetch", "--quiet", "origin", base])
    temporary = Path(tempfile.mkdtemp(prefix="airgap-scribe-publish-"))
    worktree = temporary / "worktree"
    try:
        _run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), f"origin/{base}"]
        )
        target = worktree / settings.content_dir / f"{stem}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(draft, encoding="utf-8")
        _run(["git", "-C", str(worktree), "add", "--", str(target.relative_to(worktree))])
        _run(
            [
                "git",
                "-C",
                str(worktree),
                "commit",
                "--quiet",
                "-m",
                f"feat(content): add draft for {stem}",
            ]
        )
        _run(
            [
                "git",
                "-C",
                str(worktree),
                "push",
                "--quiet",
                "--force",
                "origin",
                f"HEAD:refs/heads/{branch}",
            ]
        )
    finally:
        _run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)], check=False)
        shutil.rmtree(temporary, ignore_errors=True)
        _run(["git", "-C", str(repo), "worktree", "prune"], check=False)


def _open_or_reuse_pull_request(
    settings: PublishSettings, branch: str, stem: str, alerts: Sequence[Alert]
) -> str:
    repo = settings.repo_dir
    body = pull_request_body(stem, alerts)
    listed = _run(
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url"],
        cwd=repo,
    )
    existing = json.loads(listed or "[]")
    if existing:
        url = existing[0]["url"]
        _run(["gh", "pr", "edit", url, "--body-file", "-"], cwd=repo, stdin=body)
        return url
    created = _run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            settings.base_branch,
            "--head",
            branch,
            "--title",
            f"Add draft for {stem}",
            "--body-file",
            "-",
        ],
        cwd=repo,
        stdin=body,
    )
    lines = created.strip().splitlines()
    if not lines:
        raise PublishError("gh pr create did not print the pull request URL")
    return lines[-1].strip()


def _run(
    command: list[str],
    cwd: Path | None = None,
    stdin: str | None = None,
    check: bool = True,
) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise PublishError(f"command not found: {command[0]}") from error
    if check and result.returncode != 0:
        raise PublishError(
            f"'{_describe(command)}' exited with status {result.returncode}: "
            f"{result.stderr.strip()[-STDERR_TAIL:]}"
        )
    return result.stdout


def _describe(command: list[str]) -> str:
    words = [
        word
        for index, word in enumerate(command)
        if word != "-C" and (index == 0 or command[index - 1] != "-C")
    ]
    return " ".join(words[:3])
