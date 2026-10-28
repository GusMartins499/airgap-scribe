import json
import re
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTAINER_MODELS_DIR = Path("/models")
KIND_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class PathsSettings:
    data_dir: Path | None
    models_dir: Path


@dataclass(frozen=True)
class TranscriptionSettings:
    whisper_model: str
    language: str
    initial_prompt: str
    threads: int


@dataclass(frozen=True)
class AnonymizationSettings:
    spacy_model: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class VerifySettings:
    ignore: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftingSettings:
    command: tuple[str, ...]
    prompts: Mapping[str, Path]


@dataclass(frozen=True)
class PublishSettings:
    repo_dir: Path
    base_branch: str
    content_dir: str
    branch_prefix: str
    preview_url_template: str


@dataclass(frozen=True)
class NotifySettings:
    server: str
    topic_env: str


@dataclass(frozen=True)
class ContainerSettings:
    image: str


@dataclass(frozen=True)
class Config:
    paths: PathsSettings
    transcription: TranscriptionSettings
    anonymization: AnonymizationSettings
    verify: VerifySettings
    drafting: DraftingSettings | None = None
    publish: PublishSettings | None = None
    notify: NotifySettings | None = None
    container: ContainerSettings | None = None

    @property
    def kinds(self) -> tuple[str, ...]:
        if self.drafting is None:
            return ()
        return tuple(self.drafting.prompts)

    @property
    def whisper_model_path(self) -> Path:
        return self.paths.models_dir / self.transcription.whisper_model


def load_config(path: Path) -> Config:
    return _load_host_config(path, local=False)


def load_local_config(path: Path) -> Config:
    return _load_host_config(path, local=True)


def _load_host_config(path: Path, local: bool) -> Config:
    root = _read(path)
    paths = root.table("paths")
    data_dir = paths.optional_path("data_dir") if local else paths.path("data_dir")
    models_default = _MISSING if data_dir is None else data_dir / "models"
    paths_settings = PathsSettings(
        data_dir=data_dir,
        models_dir=paths.path("models_dir", default=models_default),
    )
    paths.close()
    config = Config(
        paths=paths_settings,
        transcription=_transcription(root),
        anonymization=_anonymization(root),
        verify=_verify(root),
        drafting=_drafting(root),
        publish=_publish(root, required=not local),
        notify=_notify(root, required=not local),
        container=_container(root),
    )
    root.close()
    return config


def load_container_config(path: Path) -> Config:
    root = _read(path)
    paths = root.table("paths")
    paths_settings = PathsSettings(data_dir=None, models_dir=paths.path("models_dir"))
    paths.close()
    config = Config(
        paths=paths_settings,
        transcription=_transcription(root),
        anonymization=_anonymization(root),
        verify=_verify(root),
    )
    root.close()
    return config


def container_config_toml(config: Config, models_dir: Path = CONTAINER_MODELS_DIR) -> str:
    transcription = config.transcription
    anonymization = config.anonymization
    lines = [
        "[paths]",
        f"models_dir = {_toml_value(str(models_dir))}",
        "",
        "[transcription]",
        f"whisper_model = {_toml_value(transcription.whisper_model)}",
        f"language = {_toml_value(transcription.language)}",
        f"initial_prompt = {_toml_value(transcription.initial_prompt)}",
        f"threads = {_toml_value(transcription.threads)}",
        "",
        "[anonymization]",
        f"spacy_model = {_toml_value(anonymization.spacy_model)}",
        f"labels = {_toml_value(anonymization.labels)}",
        "",
        "[verify]",
        f"ignore = {_toml_value(config.verify.ignore)}",
    ]
    return "\n".join(lines) + "\n"


def write_container_config(
    config: Config, path: Path, models_dir: Path = CONTAINER_MODELS_DIR
) -> None:
    path.write_text(container_config_toml(config, models_dir), encoding="utf-8")


def _toml_value(value: str | int | tuple[str, ...]) -> str:
    if isinstance(value, tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _transcription(root: "_Table") -> TranscriptionSettings:
    table = root.table("transcription")
    settings = TranscriptionSettings(
        whisper_model=table.string("whisper_model", non_empty=True),
        language=table.string("language", non_empty=True),
        initial_prompt=table.string("initial_prompt", default=""),
        threads=table.positive_integer("threads"),
    )
    table.close()
    return settings


def _anonymization(root: "_Table") -> AnonymizationSettings:
    table = root.table("anonymization")
    settings = AnonymizationSettings(
        spacy_model=table.string("spacy_model", non_empty=True),
        labels=table.strings("labels", non_empty=True),
    )
    table.close()
    return settings


def _verify(root: "_Table") -> VerifySettings:
    table = root.table("verify", required=False)
    if table is None:
        return VerifySettings()
    settings = VerifySettings(ignore=table.strings("ignore", default=()))
    table.close()
    return settings


def _drafting(root: "_Table") -> DraftingSettings:
    table = root.table("drafting")
    command = table.strings("command", non_empty=True)
    prompts_table = table.table("prompts")
    prompts = {kind: prompts_table.path(kind) for kind in prompts_table}
    if not prompts:
        raise ConfigError("'drafting.prompts' must define at least one meeting kind")
    for kind in prompts:
        if KIND_PATTERN.fullmatch(kind) is None:
            raise ConfigError(
                f"meeting kind '{kind}' in 'drafting.prompts' must use only "
                "lowercase letters, digits and hyphens"
            )
    prompts_table.close()
    table.close()
    return DraftingSettings(command=command, prompts=prompts)


def _publish(root: "_Table", required: bool) -> PublishSettings | None:
    table = root.table("publish", required=required)
    if table is None:
        return None
    settings = PublishSettings(
        repo_dir=table.path("repo_dir"),
        base_branch=table.string("base_branch", non_empty=True),
        content_dir=table.string("content_dir", non_empty=True),
        branch_prefix=table.string("branch_prefix", default=""),
        preview_url_template=table.string("preview_url_template", non_empty=True),
    )
    table.close()
    return settings


def _notify(root: "_Table", required: bool) -> NotifySettings | None:
    table = root.table("notify", required=required)
    if table is None:
        return None
    settings = NotifySettings(
        server=table.string("server", non_empty=True),
        topic_env=table.string("topic_env", non_empty=True),
    )
    table.close()
    return settings


def _container(root: "_Table") -> ContainerSettings:
    table = root.table("container")
    settings = ContainerSettings(image=table.string("image", non_empty=True))
    table.close()
    return settings


def _read(path: Path) -> "_Table":
    try:
        with path.open("rb") as handle:
            values = tomllib.load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"config file not found: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {path}: {error}") from error
    return _Table(values, "", path.absolute().parent)


_MISSING: Any = object()


class _Table:
    def __init__(self, values: dict[str, Any], name: str, base_dir: Path) -> None:
        self._values = values
        self._name = name
        self._base_dir = base_dir
        self._seen: set[str] = set()

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._values))

    def table(self, key: str, required: bool = True) -> "_Table | None":
        value = self._get(key, _MISSING if required else None)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ConfigError(f"'{self._qualified(key)}' must be a table")
        return _Table(value, self._qualified(key), self._base_dir)

    def string(self, key: str, default: str = _MISSING, non_empty: bool = False) -> str:
        value = self._get(key, default)
        if not isinstance(value, str):
            raise ConfigError(f"'{self._qualified(key)}' must be a string")
        if non_empty and not value:
            raise ConfigError(f"'{self._qualified(key)}' must not be empty")
        return value

    def positive_integer(self, key: str) -> int:
        value = self._get(key, _MISSING)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(f"'{self._qualified(key)}' must be a positive integer")
        return value

    def strings(
        self, key: str, default: tuple[str, ...] = _MISSING, non_empty: bool = False
    ) -> tuple[str, ...]:
        value = self._get(key, default)
        if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"'{self._qualified(key)}' must be a list of strings")
        if non_empty and not value:
            raise ConfigError(f"'{self._qualified(key)}' must not be empty")
        return tuple(value)

    def optional_path(self, key: str) -> Path | None:
        if key not in self._values:
            self._seen.add(key)
            return None
        return self.path(key)

    def path(self, key: str, default: Path = _MISSING) -> Path:
        if key not in self._values and default is not _MISSING:
            self._seen.add(key)
            return default
        raw = self.string(key, non_empty=True)
        candidate = Path(raw).expanduser()
        return candidate if candidate.is_absolute() else self._base_dir / candidate

    def close(self) -> None:
        unknown = sorted(set(self._values) - self._seen)
        if unknown:
            names = ", ".join(f"'{self._qualified(key)}'" for key in unknown)
            raise ConfigError(f"unknown config key(s): {names}")

    def _get(self, key: str, default: Any) -> Any:
        self._seen.add(key)
        if key in self._values:
            return self._values[key]
        if default is _MISSING:
            raise ConfigError(f"missing required config key '{self._qualified(key)}'")
        return default

    def _qualified(self, key: str) -> str:
        return f"{self._name}.{key}" if self._name else key
