# airgap-scribe — Implementation Plan

## Purpose

`airgap-scribe` turns a recording of a meeting (a talk or a group discussion, ~2 h, Brazilian Portuguese) into an anonymized written draft, opened as a pull request in a separate content repository for human review.

The defining constraint: **raw data (audio, raw transcript, detected names) never leaves the operator's machine.** Every step that touches raw data runs inside a Docker container started with `--network none`. Only the anonymized transcript leaves the machine (to the LLM that writes the draft).

The repository is public and generic. Nothing in it may reference a specific group, organization, place or person. Everything specific to a deployment comes from an external config file.

## End-to-end flow

```
phone ──Syncthing──▶ <data_dir>/inbox/2026-11-09_circle.m4a
                         │  systemd .path unit → `airgap-scribe scan`
                         ▼
HOST    run(): move file to processing/, create work/<stem>/
                         ▼
CONTAINER (--network none)   `airgap-scribe prepare`
        ffmpeg → whisper.cpp (or provided .txt) → anonymize
        writes: audio.wav, transcript.raw.txt, transcript.anon.txt, entities.json, mapping.json
                         ▼
HOST    draft: prompt[kind] + transcript.anon.txt → `claude -p` (no tools) → draft.md
                         ▼
CONTAINER (--network none)   `airgap-scribe verify`
        draft.md × entities.json × bundled names list × leftover markers → alerts.json
                         ▼
HOST    publish: temporary git worktree of the content repo, branch, commit, push, `gh pr create`
        notify: ntfy (title, alert count, preview URL, PR URL)
        move file to done/ with a record (PR URL)
                         ▼
HOST    daily timer `airgap-scribe cleanup`: PR merged → delete work/<stem>/ and audio
```

Any failure: move the input to `failed/`, write `failed/<stem>.error.txt`, send a high-priority ntfy message. Reprocessing = moving the file back to `inbox/`.

## Conventions (mandatory for every contributor/agent)

- Python 3.12, managed with `uv`. Lint with `ruff`. Tests with `pytest` + `pytest-cov` (branch coverage on).
- **No comments in code** — no docstrings, no inline comments. Names carry intent. An external gotcha becomes a test with a descriptive name, not a comment.
- Tests are written in English, named after the behavior they verify. Never change a test just to make it pass; investigate the cause.
- Tests use only synthetic data. No real audio or transcripts ever enter the repository.
- Conventional Commits in English (`feat(scope): ...`, `fix`, `test`, `docs`, `chore`, `refactor`). No `Co-Authored-By` trailer and no AI attribution anywhere (commits, PR titles/bodies, docs).
- Standard library first. Runtime dependency: `spacy`. Avoid adding others without a strong reason.

## Repository layout

```
pyproject.toml
uv.lock
README.md
LICENSE
Dockerfile
.dockerignore
.pre-commit-config.yaml
scripts/
  forbid-sensitive-files.sh
  download-whisper-model.sh
  check-module-coverage.py
ci/coverage-thresholds/<module>.toml      one file per module: lines = N, branches = N
.github/workflows/ci.yml
.github/workflows/image.yml
examples/
  pipeline.example.toml
  airgap-scribe.env.example
  prompts/example-talk.md
systemd/
  airgap-scribe.path
  airgap-scribe.service
  airgap-scribe-cleanup.service
  airgap-scribe-cleanup.timer
data/
  names/                                   bundled first-name list + PROVENANCE.md
docs/
  IMPLEMENTATION_PLAN.md
src/airgap_scribe/
  __init__.py
  __main__.py
  cli.py
  config.py
  naming.py
  queue.py
  workdir.py
  prepare.py
  audio.py
  transcribe.py
  anonymize/__init__.py, ner.py, mapping.py
  verify.py
  draft.py
  publish.py
  notify.py
  pipeline.py
  cleanup.py
tests/
  ...
  privacy/        synthetic corpus generator + privacy suite
```

## Interfaces (contracts between modules)

All paths are `pathlib.Path`. Work directory files are fixed names exported from `workdir.py`:

```python
AUDIO_WAV = "audio.wav"
TRANSCRIPT_RAW = "transcript.raw.txt"
TRANSCRIPT_ANON = "transcript.anon.txt"
ENTITIES = "entities.json"
MAPPING = "mapping.json"
DRAFT = "draft.md"
ALERTS = "alerts.json"
```

### config.py
```python
@dataclass(frozen=True)
class Config: ...            # nested frozen dataclasses mirroring the TOML sections below
def load_config(path: Path) -> Config
```
Relative paths inside the TOML resolve against the TOML file's directory; `~` is expanded. Unknown keys and missing required keys raise `ConfigError` with a clear message.

```toml
[paths]
data_dir = "~/scribe-data"            # inbox/ processing/ done/ failed/ work/ models/

[transcription]
whisper_model = "ggml-large-v3-turbo-q5_0.bin"   # file inside <data_dir>/models
language = "pt"
initial_prompt = "Vocabulary hints for the domain."
threads = 8

[anonymization]
spacy_model = "pt_core_news_lg"
labels = ["PER", "LOC"]

[drafting]
command = ["claude", "-p", "--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
[drafting.prompts]                                # keys are the allowed meeting kinds
talk = "prompts/talk.md"
circle = "prompts/circle.md"

[publish]
repo_dir = "~/www/content-site"
base_branch = "main"
content_dir = "src/content/posts"
branch_prefix = "draft/"
preview_url_template = "https://{branch_slug}.example.pages.dev"

[notify]
server = "https://ntfy.sh"
topic_env = "AIRGAP_SCRIBE_NTFY_TOPIC"

[container]
image = "ghcr.io/gusmartins499/airgap-scribe:0.1.1"
```
Secrets (the ntfy topic) come from environment variables, loaded by systemd from `~/.config/airgap-scribe/env`.

### naming.py
```python
@dataclass(frozen=True)
class MeetingFile:
    path: Path
    date: datetime.date
    kind: str
    stem: str                 # e.g. "2026-11-09_circle"
    transcript: Path | None   # sibling "<stem>.txt" when present
def parse_meeting_file(path: Path, kinds: Iterable[str]) -> MeetingFile | None
```
Accepted: `YYYY-MM-DD_<kind>.<ext>`, ext in m4a, mp4, mp3, opus, ogg, aac, wav (case-insensitive), kind in config. Invalid dates, unknown kinds, Syncthing temp files (`.syncthing.*.tmp`, `~syncthing~*`) and anything else return `None`.

### queue.py
```python
class Queue:
    def __init__(self, data_dir: Path)
    def ensure_layout(self) -> None
    def pending(self, kinds) -> list[MeetingFile]          # eligible files in inbox/, sorted
    def claim(self, item: MeetingFile) -> MeetingFile        # atomic move inbox → processing (with sibling .txt)
    def complete(self, item, record: dict) -> None           # processing → done, writes done/<stem>.json
    def fail(self, item, error: str) -> None                 # processing → failed, writes failed/<stem>.error.txt
    def work_dir(self, item) -> Path                         # work/<stem>/ (created)
    def done_records(self) -> list[tuple[Path, dict]]
```
Idempotency: a stem already present in `done/` is never processed again.

### prepare.py (runs in the container)
```python
def prepare(input_audio: Path, transcript: Path | None, work_dir: Path, config: Config) -> None
```
If `transcript` is given, copy it to `transcript.raw.txt` and skip audio/transcribe. Otherwise `audio.to_wav` then `transcribe.transcribe`. Then `anonymize.anonymize_text` and write `transcript.anon.txt`, `entities.json`, `mapping.json`.

### audio.py
```python
def to_wav(src: Path, dst: Path) -> None   # ffmpeg: mono, 16 kHz, pcm_s16le, loudnorm
```

### transcribe.py
```python
def transcribe(wav: Path, out_txt: Path, model: Path, language: str, initial_prompt: str, threads: int) -> None
```
Calls the whisper.cpp CLI binary (`whisper-cli`), plain-text output without timestamps.

### anonymize
```python
@dataclass(frozen=True)
class AnonymizationResult:
    text: str
    mapping: dict[str, str]     # marker -> original surface form
    entities: list[str]         # every distinct original entity string detected
def anonymize_text(text: str, model: str, labels: Sequence[str]) -> AnonymizationResult
```
Markers: `[PERSON_1]`, `[PERSON_2]`, `[LOCATION_1]`… The same entity (case- and accent-insensitive) always gets the same marker. Also replace standalone later occurrences of the tokens of detected person names (first names repeated without the NER catching them). Speaker labels from external transcripts (e.g. `Speaker 1:`) are preserved.

### verify.py (runs in the container)
```python
@dataclass(frozen=True)
class Alert:
    line: int
    token: str
    reason: str     # "detected-entity" | "common-first-name" | "leftover-marker"
def verify_draft(draft: str, entities: Sequence[str], names: Collection[str], ignore: Collection[str]) -> list[Alert]
def run_verify(work_dir: Path, config: Config) -> list[Alert]    # reads draft/entities, writes alerts.json
```
Matching is accent- and case-insensitive on word tokens. `names` is the bundled list (`data/names/`). A configurable `ignore` list (`[verify] ignore = [...]`, optional) suppresses known false positives. Alerts never block; they are reported.

### draft.py (host)
```python
def write_draft(work_dir: Path, meeting: MeetingFile, config: Config) -> Path
```
Builds the prompt from the kind's prompt file plus `transcript.anon.txt`, runs the configured command with the prompt on stdin, **with all tools disabled**, and writes `draft.md`. The pipeline then enforces front-matter fields `date` (from the file name) and `kind`, keeping the rest as returned by the model. Fails if the output has no front matter or no `title`.

### publish.py (host)
```python
@dataclass(frozen=True)
class Publication:
    branch: str
    pr_url: str
    preview_url: str
def publish(work_dir: Path, meeting: MeetingFile, alerts: Sequence[Alert], config: Config) -> Publication
```
Uses a temporary `git worktree` of `repo_dir` (never touches the operator's checkout), branch `<branch_prefix><stem>` from `origin/<base_branch>`, writes `<content_dir>/<stem>.md`, commits `feat(content): add draft for <stem>`, pushes and opens a PR with `gh pr create`. PR body lists alert count and each alert (line, token, reason). If a PR for the branch already exists, update the branch and reuse the PR. Preview URL built from the template with a Cloudflare-style branch slug (lowercase, non-alphanumerics → `-`).

### notify.py (host)
```python
def notify_success(pub: Publication, meeting: MeetingFile, alert_count: int, config: Config) -> None
def notify_failure(meeting_stem: str, stage: str, error: str, config: Config) -> None
```
HTTP POST to `<server>/<topic>` with `Title`, `Tags`, `Priority` (high on failure) and `Actions` (view preview, view PR). Standard library only. Messages contain no transcript text.

### pipeline.py (host)
```python
def run_one(meeting: MeetingFile, config: Config) -> None
def scan(config: Config) -> None      # processes every pending file sequentially
```
`run_one`: claim → work dir → container `prepare` → draft → container `verify` → publish → notify → complete. Any exception → `queue.fail` + `notify_failure` with the failing stage name. Container invocation:

```
docker run --rm --network none --user <uid>:<gid>
  -v <work_dir>:/work -v <processing_dir>:/input:ro -v <data_dir>/models:/models:ro
  <image> prepare --input /input/<file> [--transcript /input/<stem>.txt] --workdir /work --config /work/container.toml
```
The host writes a minimal `container.toml` into the work dir with only what the container needs (model file, language, prompt hints, spaCy model, labels, verify ignore list) so the container never sees the full host config.

### cleanup.py (host)
```python
def cleanup(config: Config) -> list[str]   # stems cleaned
```
For each done record whose PR is merged (`gh pr view <url> --json state`), delete `work/<stem>/` and the archived audio/transcript in `done/`, keep the small JSON record.

### cli.py
`airgap-scribe <command>` with argparse:
- `prepare --input --workdir --config [--transcript]` (container)
- `verify --workdir --config` (container)
- `run <file> --config` (host)
- `scan --config` (host)
- `cleanup --config` (host)

## Container

`Dockerfile`, multi-stage:
1. Build stage: build a pinned release of whisper.cpp (`whisper-cli`) with CMake, CPU only.
2. Runtime: `python:3.12-slim`, `ffmpeg`, the `whisper-cli` binary, the package installed with its dependencies, the spaCy model baked in. Non-root user. `ENTRYPOINT ["airgap-scribe"]`.
The whisper model is NOT in the image; it is mounted read-only at `/models`. `scripts/download-whisper-model.sh` downloads it on the host.

`.github/workflows/image.yml`: on tags `v*`, build and push to `ghcr.io/<owner>/airgap-scribe:<version>`.

## Testing and coverage

CI (`.github/workflows/ci.yml`, on push and pull_request): ruff, pytest with branch coverage, global gate **≥ 85% lines / ≥ 80% branches**, plus per-module gates read from `ci/coverage-thresholds/*.toml` by `scripts/check-module-coverage.py`:

| Module | Lines | Branches |
|---|---|---|
| anonymize | 95 | 90 |
| verify | 95 | 90 |
| naming, queue | 95 | 90 |
| audio, transcribe, prepare | 80 | 70 |
| draft, publish, notify, cleanup, pipeline | 80 | 70 |

Code whose only statement is `raise NotImplementedError` is excluded from coverage (`exclude_also` in `pyproject.toml`).

External binaries (ffmpeg, whisper-cli, docker, git, gh, claude) are replaced by test doubles in unit tests. A real ffmpeg conversion test generates a short synthetic tone with `-f lavfi`; it is skipped when ffmpeg is missing.

### Privacy suite (blocking, in CI)
A deterministic generator produces at least 200 synthetic Portuguese transcripts with known person and place names, including misspellings typical of speech-to-text. Targets:
- names removed by the anonymizer alone: ≥ 95%
- names removed by the anonymizer or flagged by `verify`: 100%
- names present in `transcript.anon.txt` that are not flagged: 0

## Repository safety

- `.gitignore` excludes audio extensions, `*.txt` transcripts outside `tests/` and `data/`, `work/`, `.env`, model files.
- `scripts/forbid-sensitive-files.sh` wired via `.pre-commit-config.yaml` (local hook) blocks committing audio files, model files and files named like transcripts or mappings.

## Delivery slices

| Slice | Branch | Owns | Depends on |
|---|---|---|---|
| 1. Scaffold | `feat/project-scaffold` | pyproject, uv.lock, ruff/pytest/coverage config, CI workflow, coverage scripts and thresholds for naming/queue, pre-commit guard, .gitignore additions, `config`, `naming`, `queue`, `workdir`, full `cli.py` wiring, stubs (`raise NotImplementedError`) with final signatures for every other module, examples/, docs/IMPLEMENTATION_PLAN.md, README skeleton | — |
| 2A. Privacy | `feat/anonymizer-and-name-check` | `anonymize/`, `verify.py`, `data/names/`, privacy suite, their thresholds files | 1 |
| 2B. Media and container | `feat/media-prep-and-container` | `audio.py`, `transcribe.py`, `prepare.py`, Dockerfile, .dockerignore, image workflow, download script, their thresholds files | 1 |
| 2C. Host orchestration | `feat/host-orchestration` | `draft.py`, `publish.py`, `notify.py`, `pipeline.py`, `cleanup.py`, `systemd/`, README setup guide, their thresholds files | 1 |

Slices 2A, 2B and 2C run in parallel. Each touches only the files it owns; shared files (`cli.py`, `pyproject.toml`, `uv.lock`, CI workflow) are final after slice 1. If a slice truly needs a shared-file change, it makes the smallest possible edit and says so in its PR body.
