import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from host_config import make_config, make_meeting, prepend_path, write_executable

from airgap_scribe.publish import (
    PublishError,
    branch_slug,
    preview_url,
    publish,
    pull_request_body,
)
from airgap_scribe.verify import Alert
from airgap_scribe.workdir import DRAFT

DRAFT_TEXT = "---\ntitle: A title\ndate: 2026-11-09\nkind: talk\n---\n\nBody.\n"
NEW_PR_URL = "https://github.com/example/content/pull/7"


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def worktrees(repo: Path) -> list[Path]:
    listing = git("worktree", "list", "--porcelain", cwd=repo)
    return [
        Path(line.removeprefix("worktree "))
        for line in listing.splitlines()
        if line.startswith("worktree ")
    ]


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_config = tmp_path / "gitconfig"
    global_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test Operator")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "operator@example.org")


class FakeGh:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.log = tmp_path / "gh-calls.jsonl"
        self.existing = tmp_path / "gh-existing.json"
        self.existing.write_text("[]", encoding="utf-8")
        self.fail_on = tmp_path / "gh-fail-on.txt"
        self.create_output = tmp_path / "gh-create-output.txt"
        self.create_output.write_text(f"Creating pull request\n{NEW_PR_URL}\n", encoding="utf-8")
        bin_dir = tmp_path / "gh-bin"
        write_executable(
            bin_dir / "gh",
            f"""#!{sys.executable}
import json
import os
import pathlib
import sys

base = pathlib.Path({str(tmp_path)!r})
args = sys.argv[1:]
stdin = sys.stdin.read() if "--body-file" in args else ""
with (base / "gh-calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({{"args": args, "stdin": stdin, "cwd": os.getcwd()}}) + "\\n")
fail_on = base / "gh-fail-on.txt"
if fail_on.exists() and fail_on.read_text().strip() == " ".join(args[:2]):
    sys.stderr.write("gh refused")
    sys.exit(1)
if args[:2] == ["pr", "list"]:
    sys.stdout.write((base / "gh-existing.json").read_text())
elif args[:2] == ["pr", "create"]:
    sys.stdout.write((base / "gh-create-output.txt").read_text())
""",
        )
        monkeypatch.setenv("PATH", prepend_path(bin_dir))

    def calls(self) -> list[dict[str, object]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    return FakeGh(tmp_path, monkeypatch)


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    git("init", "--quiet", "--bare", "--initial-branch=main", str(bare))
    seed = tmp_path / "seed"
    git("clone", "--quiet", str(bare), str(seed))
    (seed / "README.md").write_text("content site\n", encoding="utf-8")
    git("add", "README.md", cwd=seed)
    git("commit", "--quiet", "-m", "chore: initial commit", cwd=seed)
    git("push", "--quiet", "origin", "HEAD:main", cwd=seed)
    return bare


@pytest.fixture
def checkout(tmp_path: Path, origin: Path) -> Path:
    repo = tmp_path / "operator-checkout"
    git("clone", "--quiet", str(origin), str(repo))
    (repo / "README.md").write_text("operator edit in progress\n", encoding="utf-8")
    return repo


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    (directory / DRAFT).write_text(DRAFT_TEXT, encoding="utf-8")
    return directory


ALERTS = [Alert(line=3, token="Example", reason="common-first-name")]


def test_publish_pushes_a_branch_with_the_draft_from_the_base_branch(
    tmp_path: Path, origin: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    config = make_config(tmp_path, repo_dir=checkout)

    publication = publish(work_dir, make_meeting(tmp_path / "a.m4a"), ALERTS, config)

    assert publication.branch == "draft/2026-11-09_talk"
    content = git("show", "draft/2026-11-09_talk:src/content/posts/2026-11-09_talk.md", cwd=origin)
    assert content == DRAFT_TEXT
    assert git("log", "-1", "--format=%s", "draft/2026-11-09_talk", cwd=origin).strip() == (
        "feat(content): add draft for 2026-11-09_talk"
    )
    assert git("rev-parse", "draft/2026-11-09_talk~1", cwd=origin) == git(
        "rev-parse", "main", cwd=origin
    )


def test_commit_message_has_no_trailers(
    tmp_path: Path, origin: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    publish(
        work_dir, make_meeting(tmp_path / "a.m4a"), [], make_config(tmp_path, repo_dir=checkout)
    )

    message = git("log", "-1", "--format=%B", "draft/2026-11-09_talk", cwd=origin)
    assert message.strip() == "feat(content): add draft for 2026-11-09_talk"


def test_publish_leaves_the_operator_checkout_untouched(
    tmp_path: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    head_before = git("rev-parse", "HEAD", cwd=checkout)

    publish(
        work_dir, make_meeting(tmp_path / "a.m4a"), [], make_config(tmp_path, repo_dir=checkout)
    )

    assert git("rev-parse", "HEAD", cwd=checkout) == head_before
    assert git("branch", "--format=%(refname:short)", cwd=checkout).split() == ["main"]
    assert git("status", "--porcelain", cwd=checkout).strip() == "M README.md"
    assert worktrees(checkout) == [checkout]


def test_publish_opens_a_pull_request_listing_the_alerts(
    tmp_path: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    publication = publish(
        work_dir, make_meeting(tmp_path / "a.m4a"), ALERTS, make_config(tmp_path, repo_dir=checkout)
    )

    assert publication.pr_url == NEW_PR_URL
    list_call, create_call = fake_gh.calls()
    assert list_call["args"][:4] == ["pr", "list", "--head", "draft/2026-11-09_talk"]
    assert create_call["args"] == [
        "pr",
        "create",
        "--base",
        "main",
        "--head",
        "draft/2026-11-09_talk",
        "--title",
        "Add draft for 2026-11-09_talk",
        "--body-file",
        "-",
    ]
    assert create_call["stdin"] == pull_request_body("2026-11-09_talk", ALERTS)
    assert Path(str(create_call["cwd"])) == checkout


def test_publish_builds_the_preview_url_from_the_branch_slug(
    tmp_path: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    config = make_config(tmp_path, repo_dir=checkout)

    publication = publish(work_dir, make_meeting(tmp_path / "a.m4a"), [], config)

    assert publication.preview_url == "https://draft-2026-11-09-talk.example.pages.dev"


def test_publish_reuses_an_existing_pull_request_and_updates_the_branch(
    tmp_path: Path, origin: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    config = make_config(tmp_path, repo_dir=checkout)
    meeting = make_meeting(tmp_path / "a.m4a")
    publish(work_dir, meeting, [], config)
    existing_url = "https://github.com/example/content/pull/3"
    fake_gh.existing.write_text(json.dumps([{"url": existing_url}]), encoding="utf-8")
    (work_dir / DRAFT).write_text(DRAFT_TEXT + "\nMore.\n", encoding="utf-8")

    publication = publish(work_dir, meeting, ALERTS, config)

    assert publication.pr_url == existing_url
    edit_call = fake_gh.calls()[-1]
    assert edit_call["args"] == ["pr", "edit", existing_url, "--body-file", "-"]
    assert edit_call["stdin"] == pull_request_body("2026-11-09_talk", ALERTS)
    content = git("show", "draft/2026-11-09_talk:src/content/posts/2026-11-09_talk.md", cwd=origin)
    assert content.endswith("More.\n")
    assert git("rev-list", "--count", "draft/2026-11-09_talk", cwd=origin).strip() == "2"


def test_publish_removes_the_temporary_worktree_when_push_fails(
    tmp_path: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    hook = checkout.parent / "origin.git" / "hooks" / "pre-receive"
    write_executable(hook, "#!/bin/sh\necho rejected >&2\nexit 1\n")

    with pytest.raises(PublishError, match="git push"):
        publish(
            work_dir, make_meeting(tmp_path / "a.m4a"), [], make_config(tmp_path, repo_dir=checkout)
        )

    assert worktrees(checkout) == [checkout]
    assert fake_gh.calls() == []


def test_publish_fails_clearly_when_gh_fails(
    tmp_path: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    fake_gh.fail_on.write_text("pr create", encoding="utf-8")

    with pytest.raises(PublishError, match="'gh pr create' exited with status 1: gh refused"):
        publish(
            work_dir, make_meeting(tmp_path / "a.m4a"), [], make_config(tmp_path, repo_dir=checkout)
        )


def test_publish_fails_when_gh_prints_no_url(
    tmp_path: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    fake_gh.create_output.write_text("", encoding="utf-8")

    with pytest.raises(PublishError, match="did not print"):
        publish(
            work_dir, make_meeting(tmp_path / "a.m4a"), [], make_config(tmp_path, repo_dir=checkout)
        )


def test_publish_fails_when_gh_is_not_installed(
    tmp_path: Path, checkout: Path, work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "git-only"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(bin_dir))

    with pytest.raises(PublishError, match="command not found: gh"):
        publish(
            work_dir, make_meeting(tmp_path / "a.m4a"), [], make_config(tmp_path, repo_dir=checkout)
        )


def test_publish_fails_when_the_base_branch_cannot_be_fetched(
    tmp_path: Path, checkout: Path, work_dir: Path, fake_gh: FakeGh
) -> None:
    config = make_config(tmp_path, repo_dir=checkout)
    config = replace(config, publish=replace(config.publish, base_branch="missing"))

    with pytest.raises(PublishError, match="git fetch"):
        publish(work_dir, make_meeting(tmp_path / "a.m4a"), [], config)


def test_publish_requires_a_publish_section(tmp_path: Path, work_dir: Path) -> None:
    config = replace(make_config(tmp_path), publish=None)

    with pytest.raises(PublishError, match=r"\[publish\]"):
        publish(work_dir, make_meeting(tmp_path / "a.m4a"), [], config)


@pytest.mark.parametrize(
    ("branch", "slug"),
    [
        ("draft/2026-11-09_talk", "draft-2026-11-09-talk"),
        ("Drafts/Weekly_Circle", "drafts-weekly-circle"),
        ("draft/2026-11-09_a-very-long-kind-name", "draft-2026-11-09-a-very-long"),
        ("draft/2026-11-09_abcdefghij_k", "draft-2026-11-09-abcdefghij"),
    ],
)
def test_branch_slug_follows_cloudflare_pages_aliases(branch: str, slug: str) -> None:
    assert branch_slug(branch) == slug


def test_preview_url_template_may_contain_other_braces() -> None:
    url = preview_url("https://{branch_slug}.example.pages.dev/{path}", "draft/x")

    assert url == "https://draft-x.example.pages.dev/{path}"


def test_pull_request_body_without_alerts_has_no_table() -> None:
    body = pull_request_body("2026-11-09_talk", [])

    assert "Name-check alerts: 0" in body
    assert "| Line |" not in body
