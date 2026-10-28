import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from airgap_scribe import cleanup, cli, pipeline, prepare, verify
from airgap_scribe.config import container_config_toml, load_config
from airgap_scribe.naming import MeetingFile
from airgap_scribe.verify import Alert

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "pipeline.example.toml"


@pytest.fixture
def container_config(tmp_path: Path) -> Path:
    path = tmp_path / "container.toml"
    path.write_text(container_config_toml(load_config(EXAMPLE_CONFIG)), encoding="utf-8")
    return path


class Recorder:
    def __init__(self, result: Any = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.result = result

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.result


def test_prepare_passes_paths_and_container_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, container_config: Path
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(prepare, "prepare", recorder)

    code = cli.main(
        [
            "prepare",
            "--input",
            "/input/a.m4a",
            "--workdir",
            str(tmp_path),
            "--config",
            str(container_config),
            "--transcript",
            "/input/a.txt",
        ]
    )

    assert code == 0
    [(input_audio, transcript, work_dir, config)] = recorder.calls
    assert input_audio == Path("/input/a.m4a")
    assert transcript == Path("/input/a.txt")
    assert work_dir == tmp_path
    assert config.paths.models_dir == Path("/models")
    assert config.drafting is None


def test_prepare_without_transcript_passes_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, container_config: Path
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(prepare, "prepare", recorder)

    cli.main(
        [
            "prepare",
            "--input",
            "/input/a.m4a",
            "--workdir",
            str(tmp_path),
            "--config",
            str(container_config),
        ]
    )

    assert recorder.calls[0][1] is None


def test_verify_reports_only_the_alert_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    container_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorder = Recorder([Alert(line=3, token="Synthname", reason="common-first-name")])
    monkeypatch.setattr(verify, "run_verify", recorder)

    code = cli.main(["verify", "--workdir", str(tmp_path), "--config", str(container_config)])

    assert code == 0
    assert recorder.calls[0][0] == tmp_path
    output = capsys.readouterr().out
    assert output == "1 alert(s)\n"
    assert "Synthname" not in output


def test_run_parses_the_file_and_runs_the_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(pipeline, "run_one", recorder)

    code = cli.main(["run", "/inbox/2026-11-09_talk.m4a", "--config", str(EXAMPLE_CONFIG)])

    assert code == 0
    [(meeting, config)] = recorder.calls
    assert isinstance(meeting, MeetingFile)
    assert meeting.stem == "2026-11-09_talk"
    assert config.kinds == ("talk",)


def test_run_rejects_a_file_that_is_not_a_meeting_recording(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(pipeline, "run_one", recorder)

    code = cli.main(["run", "/inbox/notes.m4a", "--config", str(EXAMPLE_CONFIG)])

    assert code == cli.EXIT_USAGE
    assert recorder.calls == []
    assert "notes.m4a" in capsys.readouterr().err


def test_scan_runs_the_pipeline_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(pipeline, "scan", recorder)

    assert cli.main(["scan", "--config", str(EXAMPLE_CONFIG)]) == 0
    assert recorder.calls[0][0].kinds == ("talk",)


def test_cleanup_prints_each_cleaned_stem(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cleanup, "cleanup", Recorder(["2026-11-09_talk", "2026-11-16_talk"]))

    assert cli.main(["cleanup", "--config", str(EXAMPLE_CONFIG)]) == 0
    assert capsys.readouterr().out == "2026-11-09_talk\n2026-11-16_talk\n"


def test_configuration_errors_exit_with_usage_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["scan", "--config", str(tmp_path / "absent.toml")])

    assert code == cli.EXIT_USAGE
    assert "configuration error" in capsys.readouterr().err


def test_host_commands_reject_a_container_config(
    container_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["scan", "--config", str(container_config)]) == cli.EXIT_USAGE


def test_a_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])

    assert exit_info.value.code == cli.EXIT_USAGE


@pytest.mark.parametrize("command", ["prepare", "verify", "run", "scan", "cleanup"])
def test_every_command_requires_a_config(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main([command])

    assert "--config" in capsys.readouterr().err


def test_module_entry_point_runs_the_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["airgap-scribe", "--help"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("airgap_scribe", run_name="__main__")

    assert exit_info.value.code == 0
    assert "airgap-scribe" in capsys.readouterr().out


TRY_EXAMPLE_CONFIG = REPO_ROOT / "examples" / "try.example.toml"


def fake_try_result(work_dir: Path, alert_count: int) -> pipeline.TryResult:
    return pipeline.TryResult(
        work_dir=work_dir,
        transcript_raw=work_dir / "transcript.raw.txt",
        transcript_anon=work_dir / "transcript.anon.txt",
        draft=work_dir / "draft.md",
        alerts_file=work_dir / "alerts.json",
        alerts=[Alert(line=1, token="Synthname", reason="common-first-name")] * alert_count,
    )


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "Voice memo 12.m4a"
    path.write_bytes(b"synthetic audio")
    return path


def test_try_prints_every_output_path_the_alert_count_and_a_raw_warning(
    tmp_path: Path,
    audio_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out_dir = tmp_path / "out"
    recorder = Recorder(fake_try_result(out_dir, 2))
    monkeypatch.setattr(pipeline, "try_one", recorder)

    code = cli.main(
        ["try", str(audio_file), "--config", str(TRY_EXAMPLE_CONFIG), "--out", str(out_dir)]
    )

    assert code == 0
    [(audio, kind, work_dir, config)] = recorder.calls
    assert (audio, kind, work_dir) == (audio_file, "talk", out_dir)
    assert config.publish is None
    output = capsys.readouterr().out
    for name in ("transcript.raw.txt", "transcript.anon.txt", "draft.md", "alerts.json"):
        assert str(out_dir / name) in output
    assert "(2 alert(s))" in output
    assert "warning: transcript.raw.txt contains real names" in output
    assert "Synthname" not in output


def test_try_defaults_to_a_timestamped_dir_under_the_current_directory(
    tmp_path: Path, audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(fake_try_result(tmp_path, 0))
    monkeypatch.setattr(pipeline, "try_one", recorder)
    monkeypatch.chdir(tmp_path)

    cli.main(["try", str(audio_file), "--config", str(TRY_EXAMPLE_CONFIG)])

    work_dir = recorder.calls[0][2]
    assert work_dir.parent == tmp_path / "airgap-scribe-try"
    assert work_dir.name.startswith("Voice memo 12-")


def test_try_passes_the_requested_kind(
    tmp_path: Path, audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(fake_try_result(tmp_path, 0))
    monkeypatch.setattr(pipeline, "try_one", recorder)

    cli.main(["try", str(audio_file), "--config", str(EXAMPLE_CONFIG), "--kind", "other"])

    assert recorder.calls[0][1] == "other"


def test_try_rejects_a_missing_audio_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(pipeline, "try_one", recorder)

    code = cli.main(["try", str(tmp_path / "absent.m4a"), "--config", str(TRY_EXAMPLE_CONFIG)])

    assert code == cli.EXIT_USAGE
    assert recorder.calls == []
    assert "audio file not found" in capsys.readouterr().err


def test_try_reports_a_processing_failure_with_a_failure_code(
    tmp_path: Path,
    audio_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failing_try(*args: Any) -> None:
        raise pipeline.ContainerError("container step 'prepare' exited with status 4")

    monkeypatch.setattr(pipeline, "try_one", failing_try)

    code = cli.main(
        ["try", str(audio_file), "--config", str(TRY_EXAMPLE_CONFIG), "--out", str(tmp_path)]
    )

    assert code == cli.EXIT_FAILURE
    assert "try failed: container step 'prepare'" in capsys.readouterr().err


def test_try_requires_a_config(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["try", "recording.m4a"])

    assert "--config" in capsys.readouterr().err


def test_host_pipeline_commands_reject_the_try_only_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["scan", "--config", str(TRY_EXAMPLE_CONFIG)]) == cli.EXIT_USAGE
