from pathlib import Path

import pytest

from airgap_scribe.config import (
    CONTAINER_MODELS_DIR,
    Config,
    ConfigError,
    VerifySettings,
    container_config_toml,
    load_config,
    load_container_config,
    load_local_config,
    write_container_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SECTIONS = {
    "paths": 'data_dir = "data"',
    "transcription": "\n".join(
        [
            'whisper_model = "model.bin"',
            'language = "pt"',
            'initial_prompt = "Hints."',
            "threads = 4",
        ]
    ),
    "anonymization": 'spacy_model = "pt_core_news_lg"\nlabels = ["PER", "LOC"]',
    "drafting": 'command = ["drafter", "-p", "--tools", ""]',
    "drafting.prompts": 'talk = "prompts/talk.md"\ndiscussion = "/abs/discussion.md"',
    "publish": "\n".join(
        [
            'repo_dir = "~/content"',
            'base_branch = "main"',
            'content_dir = "src/content/posts"',
            'branch_prefix = "draft/"',
            'preview_url_template = "https://{branch_slug}.example.test"',
        ]
    ),
    "notify": 'server = "https://ntfy.example.test"\ntopic_env = "TOPIC_VAR"',
    "container": 'image = "registry.example.test/airgap-scribe:1"',
}


def render(sections: dict[str, str]) -> str:
    return "\n".join(f"[{name}]\n{body}\n" for name, body in sections.items())


def write_config(tmp_path: Path, sections: dict[str, str] | None = None, **changes: str) -> Path:
    merged = dict(SECTIONS if sections is None else sections)
    for name, body in changes.items():
        merged[name.replace("__", ".")] = body
    path = tmp_path / "pipeline.toml"
    path.write_text(render(merged), encoding="utf-8")
    return path


def without(*names: str) -> dict[str, str]:
    return {name: body for name, body in SECTIONS.items() if name not in names}


def test_loads_every_section_of_a_complete_config(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))

    assert config.paths.data_dir == tmp_path / "data"
    assert config.paths.models_dir == tmp_path / "data" / "models"
    assert config.transcription.whisper_model == "model.bin"
    assert config.transcription.language == "pt"
    assert config.transcription.initial_prompt == "Hints."
    assert config.transcription.threads == 4
    assert config.anonymization.spacy_model == "pt_core_news_lg"
    assert config.anonymization.labels == ("PER", "LOC")
    assert config.verify == VerifySettings(ignore=())
    assert config.drafting is not None
    assert config.drafting.command == ("drafter", "-p", "--tools", "")
    assert dict(config.drafting.prompts) == {
        "talk": tmp_path / "prompts" / "talk.md",
        "discussion": Path("/abs/discussion.md"),
    }
    assert config.publish is not None
    assert config.publish.repo_dir == Path.home() / "content"
    assert config.publish.base_branch == "main"
    assert config.publish.content_dir == "src/content/posts"
    assert config.publish.branch_prefix == "draft/"
    assert config.publish.preview_url_template == "https://{branch_slug}.example.test"
    assert config.notify is not None
    assert config.notify.server == "https://ntfy.example.test"
    assert config.notify.topic_env == "TOPIC_VAR"
    assert config.container is not None
    assert config.container.image == "registry.example.test/airgap-scribe:1"


def test_kinds_are_the_prompt_keys_in_declaration_order(tmp_path: Path) -> None:
    assert load_config(write_config(tmp_path)).kinds == ("talk", "discussion")


def test_whisper_model_path_lives_in_the_models_dir(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path))

    assert config.whisper_model_path == tmp_path / "data" / "models" / "model.bin"


def test_relative_paths_resolve_against_the_config_directory_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "etc"
    config_dir.mkdir()
    path = write_config(config_dir)
    monkeypatch.chdir(tmp_path)

    config = load_config(Path("etc") / path.name)

    assert config.paths.data_dir == config_dir / "data"


def test_home_is_expanded_in_paths(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, paths='data_dir = "~/scribe"'))

    assert config.paths.data_dir == Path.home() / "scribe"


def test_models_dir_can_be_overridden(tmp_path: Path) -> None:
    path = write_config(tmp_path, paths='data_dir = "data"\nmodels_dir = "/srv/models"')

    assert load_config(path).paths.models_dir == Path("/srv/models")


def test_optional_keys_take_their_defaults(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        transcription='whisper_model = "m.bin"\nlanguage = "pt"\nthreads = 1',
        publish="\n".join(
            [
                'repo_dir = "repo"',
                'base_branch = "main"',
                'content_dir = "posts"',
                'preview_url_template = "https://{branch_slug}.example.test"',
            ]
        ),
    )

    config = load_config(path)

    assert config.transcription.initial_prompt == ""
    assert config.publish is not None
    assert config.publish.branch_prefix == ""


def test_verify_ignore_list_is_loaded(tmp_path: Path) -> None:
    path = write_config(tmp_path, verify='ignore = ["Maio", "Natal"]')

    assert load_config(path).verify.ignore == ("Maio", "Natal")


def test_empty_verify_section_means_nothing_ignored(tmp_path: Path) -> None:
    assert load_config(write_config(tmp_path, verify="")).verify.ignore == ()


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.toml")


def test_invalid_toml_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("[paths\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)


@pytest.mark.parametrize(
    "section",
    ["paths", "transcription", "anonymization", "drafting", "publish", "notify", "container"],
)
def test_missing_required_section_is_reported_by_name(tmp_path: Path, section: str) -> None:
    sections = without(section, "drafting.prompts") if section == "drafting" else without(section)

    with pytest.raises(ConfigError, match=f"missing required config key '{section}'"):
        load_config(write_config(tmp_path, sections))


def test_missing_required_key_is_reported_with_its_section(tmp_path: Path) -> None:
    path = write_config(tmp_path, notify='server = "https://ntfy.example.test"')

    with pytest.raises(ConfigError, match=r"'notify.topic_env'"):
        load_config(path)


def test_missing_prompts_table_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"'drafting.prompts'"):
        load_config(write_config(tmp_path, without("drafting.prompts")))


def test_unknown_top_level_section_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"unknown config key.*'extras'"):
        load_config(write_config(tmp_path, extras='a = "b"'))


def test_unknown_key_inside_a_section_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, container='image = "i"\ntag = "t"')

    with pytest.raises(ConfigError, match=r"'container.tag'"):
        load_config(path)


def test_unknown_key_inside_paths_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"'paths.inbox'"):
        load_config(write_config(tmp_path, paths='data_dir = "d"\ninbox = "i"'))


def test_unknown_key_inside_verify_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"'verify.allow'"):
        load_config(write_config(tmp_path, verify="allow = []"))


@pytest.mark.parametrize(
    ("section", "body", "message"),
    [
        ("paths", "data_dir = 3", "'paths.data_dir' must be a string"),
        ("paths", 'data_dir = ""', "'paths.data_dir' must not be empty"),
        (
            "transcription",
            'whisper_model = "m"\nlanguage = "pt"\nthreads = 0',
            "'transcription.threads' must be a positive integer",
        ),
        (
            "transcription",
            'whisper_model = "m"\nlanguage = "pt"\nthreads = "8"',
            "'transcription.threads' must be a positive integer",
        ),
        (
            "transcription",
            'whisper_model = "m"\nlanguage = "pt"\nthreads = true',
            "'transcription.threads' must be a positive integer",
        ),
        (
            "transcription",
            'whisper_model = "m"\nlanguage = ""\nthreads = 1',
            "'transcription.language' must not be empty",
        ),
        (
            "anonymization",
            'spacy_model = "m"\nlabels = "PER"',
            "'anonymization.labels' must be a list of strings",
        ),
        (
            "anonymization",
            'spacy_model = "m"\nlabels = ["PER", 1]',
            "'anonymization.labels' must be a list of strings",
        ),
        (
            "anonymization",
            'spacy_model = "m"\nlabels = []',
            "'anonymization.labels' must not be empty",
        ),
        ("drafting", "command = []", "'drafting.command' must not be empty"),
        ("drafting.prompts", "talk = 1", "'drafting.prompts.talk' must be a string"),
        ("drafting.prompts", "", "at least one meeting kind"),
        ("drafting.prompts", 'Talk = "p.md"', "meeting kind 'Talk'"),
        ("drafting.prompts", 'my_talk = "p.md"', "meeting kind 'my_talk'"),
        ("verify", 'ignore = "x"', "'verify.ignore' must be a list of strings"),
    ],
)
def test_invalid_values_are_reported_clearly(
    tmp_path: Path, section: str, body: str, message: str
) -> None:
    path = write_config(tmp_path, **{section.replace(".", "__"): body})

    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_a_section_that_is_not_a_table_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.toml"
    path.write_text('container = "image"\n' + render(without("container")), encoding="utf-8")

    with pytest.raises(ConfigError, match=r"'container' must be a table"):
        load_config(path)


def test_the_shipped_example_config_is_valid() -> None:
    config = load_config(REPO_ROOT / "examples" / "pipeline.example.toml")

    assert config.kinds == ("talk",)
    assert config.drafting is not None
    assert config.drafting.prompts["talk"].is_file()


def host_config(tmp_path: Path) -> Config:
    return load_config(
        write_config(
            tmp_path,
            transcription="\n".join(
                [
                    'whisper_model = "model.bin"',
                    'language = "pt"',
                    'initial_prompt = "Termos: \\"aspas\\", barra \\\\ e acentuação."',
                    "threads = 6",
                ]
            ),
            verify='ignore = ["Maio"]',
        )
    )


def test_container_config_keeps_only_what_the_container_needs(tmp_path: Path) -> None:
    text = container_config_toml(host_config(tmp_path))

    assert "[drafting]" not in text
    assert "[publish]" not in text
    assert "[notify]" not in text
    assert "[container]" not in text
    assert "data_dir" not in text
    assert "TOPIC_VAR" not in text


def test_container_config_round_trips_through_the_container_loader(tmp_path: Path) -> None:
    host = host_config(tmp_path)
    path = tmp_path / "work" / "container.toml"
    path.parent.mkdir()

    write_container_config(host, path)
    loaded = load_container_config(path)

    assert loaded.paths.data_dir is None
    assert loaded.paths.models_dir == CONTAINER_MODELS_DIR
    assert loaded.transcription == host.transcription
    assert loaded.anonymization == host.anonymization
    assert loaded.verify == host.verify
    assert loaded.drafting is None
    assert loaded.publish is None
    assert loaded.notify is None
    assert loaded.container is None
    assert loaded.kinds == ()
    assert loaded.whisper_model_path == Path("/models/model.bin")


def test_container_config_accepts_a_custom_models_dir(tmp_path: Path) -> None:
    path = tmp_path / "container.toml"

    write_container_config(host_config(tmp_path), path, models_dir=tmp_path / "models")

    assert load_container_config(path).paths.models_dir == tmp_path / "models"


def test_container_loader_rejects_host_only_sections(tmp_path: Path) -> None:
    path = tmp_path / "container.toml"
    text = container_config_toml(host_config(tmp_path))
    path.write_text(text + '\n[notify]\nserver = "s"\ntopic_env = "t"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match=r"'notify'"):
        load_container_config(path)


def test_container_loader_requires_models_dir(tmp_path: Path) -> None:
    path = tmp_path / "container.toml"
    text = container_config_toml(host_config(tmp_path))
    path.write_text(text.replace('models_dir = "/models"', 'data_dir = "/data"'), encoding="utf-8")

    with pytest.raises(ConfigError, match=r"'paths.models_dir'"):
        load_container_config(path)


LOCAL_SECTIONS = {
    **without("publish", "notify"),
    "paths": 'models_dir = "/srv/models"',
}


def test_local_config_needs_neither_publish_nor_notify_nor_a_data_dir(tmp_path: Path) -> None:
    config = load_local_config(write_config(tmp_path, LOCAL_SECTIONS))

    assert config.publish is None
    assert config.notify is None
    assert config.paths.data_dir is None
    assert config.paths.models_dir == Path("/srv/models")
    assert config.kinds == ("talk", "discussion")


def test_local_config_still_loads_publish_and_notify_when_present(tmp_path: Path) -> None:
    config = load_local_config(write_config(tmp_path))

    assert config.publish is not None
    assert config.notify is not None
    assert config.paths.models_dir == tmp_path / "data" / "models"


def test_local_config_requires_models_dir_without_a_data_dir(tmp_path: Path) -> None:
    sections = {**LOCAL_SECTIONS, "paths": ""}

    with pytest.raises(ConfigError, match=r"'paths.models_dir'"):
        load_local_config(write_config(tmp_path, sections))


@pytest.mark.parametrize("section", ["transcription", "anonymization", "drafting", "container"])
def test_local_config_still_requires_the_processing_sections(tmp_path: Path, section: str) -> None:
    names = (section, "drafting.prompts") if section == "drafting" else (section,)
    sections = {name: body for name, body in LOCAL_SECTIONS.items() if name not in names}

    with pytest.raises(ConfigError, match=f"missing required config key '{section}'"):
        load_local_config(write_config(tmp_path, sections))


def test_local_config_rejects_unknown_sections(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"unknown config key.*'extras'"):
        load_local_config(write_config(tmp_path, LOCAL_SECTIONS, extras='a = "b"'))


def test_host_config_rejects_a_config_meant_only_for_try(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"missing required config key 'paths.data_dir'"):
        load_config(write_config(tmp_path, LOCAL_SECTIONS))


def test_the_shipped_try_example_config_is_valid_for_try_only() -> None:
    path = REPO_ROOT / "examples" / "try.example.toml"

    config = load_local_config(path)

    assert config.kinds == ("talk",)
    assert config.drafting is not None
    assert config.drafting.prompts["talk"].is_file()
    with pytest.raises(ConfigError):
        load_config(path)
