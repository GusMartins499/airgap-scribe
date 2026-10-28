import threading
import urllib.error
from collections.abc import Iterator
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from host_config import TOPIC_ENV, make_config, make_meeting

from airgap_scribe.notify import NotifyError, notify_failure, notify_success, summarize_error
from airgap_scribe.publish import Publication

TOPIC = "0123456789abcdef0123456789abcdef"
PUBLICATION = Publication(
    branch="draft/2026-11-09_talk",
    pr_url="https://github.com/example/content/pull/7",
    preview_url="https://draft-2026-11-09-talk.example.pages.dev",
)


@dataclass
class ReceivedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: str


class NtfyStub:
    def __init__(self) -> None:
        self.requests: list[ReceivedRequest] = []
        self.status = 200
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                stub.requests.append(
                    ReceivedRequest(
                        method="POST",
                        path=self.path,
                        headers=dict(self.headers.items()),
                        body=self.rfile.read(length).decode("utf-8"),
                    )
                )
                self.send_response(stub.status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ntfy() -> Iterator[NtfyStub]:
    stub = NtfyStub()
    yield stub
    stub.close()


@pytest.fixture(autouse=True)
def topic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOPIC_ENV, TOPIC)


def test_success_posts_to_the_topic_with_title_tags_and_actions(
    tmp_path: Path, ntfy: NtfyStub
) -> None:
    config = make_config(tmp_path, server=ntfy.url)

    notify_success(PUBLICATION, make_meeting(tmp_path / "a.m4a"), 2, config)

    [request] = ntfy.requests
    assert request.path == f"/{TOPIC}"
    assert request.headers["Title"] == "Draft ready: 2026-11-09_talk"
    assert request.headers["Tags"] == "memo,warning"
    assert request.headers["Priority"] == "default"
    assert request.headers["Actions"] == (
        "view, Preview, https://draft-2026-11-09-talk.example.pages.dev; "
        "view, Pull request, https://github.com/example/content/pull/7"
    )
    assert request.body.startswith("2 name-check alert(s).")


def test_success_without_alerts_has_no_warning_tag(tmp_path: Path, ntfy: NtfyStub) -> None:
    config = make_config(tmp_path, server=ntfy.url)

    notify_success(PUBLICATION, make_meeting(tmp_path / "a.m4a"), 0, config)

    assert ntfy.requests[0].headers["Tags"] == "memo"


def test_failure_is_high_priority_and_names_the_stage(tmp_path: Path, ntfy: NtfyStub) -> None:
    config = make_config(tmp_path, server=ntfy.url.rstrip("/"))

    notify_failure("2026-11-09_talk", "draft", "DraftError\nsecond line", config)

    [request] = ntfy.requests
    assert request.path == f"/{TOPIC}"
    assert request.headers["Title"] == "Processing failed: 2026-11-09_talk"
    assert request.headers["Priority"] == "high"
    assert "Actions" not in request.headers
    assert request.body == "Stage: draft\nDraftError"


def test_failure_message_keeps_only_a_short_first_line_of_the_error(
    tmp_path: Path, ntfy: NtfyStub
) -> None:
    config = make_config(tmp_path, server=ntfy.url)

    notify_failure("2026-11-09_talk", "prepare", "x" * 1000 + "\ndetails", config)

    body = ntfy.requests[0].body
    assert body == "Stage: prepare\n" + "x" * 297 + "..."


def test_empty_error_is_summarized_as_unknown() -> None:
    assert summarize_error("  \n") == "unknown error"


def test_a_missing_topic_raises_without_sending(
    tmp_path: Path, ntfy: NtfyStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOPIC_ENV)
    config = make_config(tmp_path, server=ntfy.url)

    with pytest.raises(NotifyError, match=TOPIC_ENV):
        notify_failure("2026-11-09_talk", "draft", "DraftError", config)
    assert ntfy.requests == []


def test_an_error_response_from_the_server_raises(tmp_path: Path, ntfy: NtfyStub) -> None:
    ntfy.status = 500
    config = make_config(tmp_path, server=ntfy.url)

    with pytest.raises(urllib.error.HTTPError):
        notify_success(PUBLICATION, make_meeting(tmp_path / "a.m4a"), 0, config)


def test_a_config_without_notify_section_is_rejected(tmp_path: Path) -> None:
    config = replace(make_config(tmp_path), notify=None)

    with pytest.raises(NotifyError, match=r"\[notify\]"):
        notify_failure("2026-11-09_talk", "draft", "DraftError", config)
