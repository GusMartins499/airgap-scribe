import os
import urllib.request
from collections.abc import Sequence

from airgap_scribe.config import Config, NotifySettings
from airgap_scribe.naming import MeetingFile
from airgap_scribe.publish import Publication

TIMEOUT_SECONDS = 30
ERROR_SUMMARY_MAX_LENGTH = 300


class NotifyError(Exception):
    pass


def notify_success(
    pub: Publication,
    meeting: MeetingFile,
    alert_count: int,
    config: Config,
) -> None:
    tags = "memo,warning" if alert_count else "memo"
    message = (
        f"{alert_count} name-check alert(s). Review the pull request before merging.\n"
        f"Branch: {pub.branch}"
    )
    actions = "; ".join(
        [
            f"view, Preview, {pub.preview_url}",
            f"view, Pull request, {pub.pr_url}",
        ]
    )
    _post(
        _settings(config),
        message,
        {
            "Title": f"Draft ready: {meeting.stem}",
            "Tags": tags,
            "Priority": "default",
            "Actions": actions,
        },
    )


def notify_failure(meeting_stem: str, stage: str, error: str, config: Config) -> None:
    message = f"Stage: {stage}\n{summarize_error(error)}"
    _post(
        _settings(config),
        message,
        {
            "Title": f"Processing failed: {meeting_stem}",
            "Tags": "rotating_light",
            "Priority": "high",
        },
    )


def notify_resumed(meeting_stem: str, config: Config) -> None:
    _post(
        _settings(config),
        "A previous run stopped before finishing. Processing resumes from the saved work.",
        {
            "Title": f"Resuming: {meeting_stem}",
            "Tags": "repeat",
            "Priority": "default",
        },
    )


def summarize_error(error: str) -> str:
    lines = error.strip().splitlines()
    first_line = lines[0].strip() if lines else "unknown error"
    if len(first_line) > ERROR_SUMMARY_MAX_LENGTH:
        return first_line[: ERROR_SUMMARY_MAX_LENGTH - 3] + "..."
    return first_line


def _settings(config: Config) -> NotifySettings:
    if config.notify is None:
        raise NotifyError("the config has no [notify] section")
    return config.notify


def _post(settings: NotifySettings, message: str, headers: dict[str, str]) -> None:
    topic = os.environ.get(settings.topic_env, "").strip()
    if not topic:
        raise NotifyError(f"the environment variable {settings.topic_env} is not set")
    request = urllib.request.Request(
        f"{settings.server.rstrip('/')}/{topic}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        response.read()


def notify_late_attachments(
    meeting_stem: str, names: Sequence[str], state: str, config: Config
) -> None:
    message = (
        f"The pull request is {state.lower()}, so these files stayed in the inbox: "
        f"{', '.join(names)}. Add them to the post by hand; cleanup removes them in a week."
    )
    _post(
        _settings(config),
        message,
        {
            "Title": f"Files arrived late: {meeting_stem}",
            "Tags": "paperclip",
            "Priority": "default",
        },
    )
