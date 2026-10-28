import dataclasses
import importlib
import inspect

import pytest

PLANNED_FUNCTIONS = {
    "airgap_scribe.prepare.prepare": ["input_audio", "transcript", "work_dir", "config"],
    "airgap_scribe.audio.to_wav": ["src", "dst"],
    "airgap_scribe.transcribe.transcribe": [
        "wav",
        "out_txt",
        "model",
        "language",
        "initial_prompt",
        "threads",
    ],
    "airgap_scribe.anonymize.anonymize_text": ["text", "model", "labels"],
    "airgap_scribe.verify.verify_draft": ["draft", "entities", "names", "ignore"],
    "airgap_scribe.verify.run_verify": ["work_dir", "config"],
    "airgap_scribe.draft.write_draft": ["work_dir", "meeting", "config"],
    "airgap_scribe.publish.publish": ["work_dir", "meeting", "alerts", "config"],
    "airgap_scribe.notify.notify_success": ["pub", "meeting", "alert_count", "config"],
    "airgap_scribe.notify.notify_failure": ["meeting_stem", "stage", "error", "config"],
    "airgap_scribe.pipeline.run_one": ["meeting", "config"],
    "airgap_scribe.pipeline.scan": ["config"],
    "airgap_scribe.cleanup.cleanup": ["config"],
    "airgap_scribe.config.load_config": ["path"],
    "airgap_scribe.naming.parse_meeting_file": ["path", "kinds"],
}

PLANNED_DATACLASSES = {
    "airgap_scribe.anonymize.AnonymizationResult": ["text", "mapping", "entities"],
    "airgap_scribe.verify.Alert": ["line", "token", "reason"],
    "airgap_scribe.publish.Publication": ["branch", "pr_url", "preview_url"],
    "airgap_scribe.naming.MeetingFile": ["path", "date", "kind", "stem", "transcript"],
}


def resolve(qualified_name: str) -> object:
    module_name, attribute = qualified_name.rsplit(".", 1)
    return getattr(importlib.import_module(module_name), attribute)


@pytest.mark.parametrize(("name", "parameters"), PLANNED_FUNCTIONS.items())
def test_functions_keep_the_planned_parameters(name: str, parameters: list[str]) -> None:
    assert list(inspect.signature(resolve(name)).parameters) == parameters


@pytest.mark.parametrize(("name", "fields"), PLANNED_DATACLASSES.items())
def test_shared_records_are_frozen_dataclasses_with_the_planned_fields(
    name: str, fields: list[str]
) -> None:
    record = resolve(name)

    assert dataclasses.is_dataclass(record)
    assert record.__dataclass_params__.frozen
    assert [field.name for field in dataclasses.fields(record)] == fields


def test_config_is_a_frozen_dataclass() -> None:
    from airgap_scribe.config import Config

    assert dataclasses.is_dataclass(Config)
    assert Config.__dataclass_params__.frozen


@pytest.mark.parametrize(
    "module", ["airgap_scribe.anonymize.ner", "airgap_scribe.anonymize.mapping"]
)
def test_anonymize_submodules_are_importable(module: str) -> None:
    assert importlib.import_module(module)
