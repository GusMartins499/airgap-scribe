# airgap-scribe

> **Status: under construction.** The project scaffold is in place; the processing steps are
> being implemented. See the [implementation plan](docs/IMPLEMENTATION_PLAN.md).

`airgap-scribe` turns the recording of a meeting (a talk or a group discussion) into an
anonymized written draft, and opens it as a pull request in a content repository for human
review.

## Privacy model

Raw data never leaves the operator's machine.

- Every step that touches raw data (audio conversion, transcription, anonymization and the
  final name check) runs inside a Docker container started with `--network none`, so it has
  no network access at all.
- People and places are replaced by markers such as `[PERSON_1]` before anything else
  happens. Only this anonymized transcript leaves the machine, to the language model that
  writes the draft.
- The draft is checked again for names before it is published, and every alert is listed in
  the pull request. A person reviews and edits the draft before it is merged.
- Everything specific to a deployment (paths, prompts, target repository, notification topic)
  lives in an external config file. See [`examples/`](examples/).
- The language model is called through the Claude Code CLI with every tool disabled
  (`--tools ""`), from an empty temporary directory, so it can only read the prompt and the
  anonymized transcript it receives on stdin.
- Notifications carry only the recording name, the processing stage, the alert count and
  links. They never include transcript text, alert tokens or error details; those stay in
  local files.

## Setup

The host is an always-on Linux machine that receives the recordings (for example through
Syncthing) and runs the pipeline as a set of systemd user units.

### Requirements

- Docker, with the operator's user allowed to run containers.
- [uv](https://docs.astral.sh/uv/), `git` and the [GitHub CLI](https://cli.github.com/)
  (`gh auth login`), with push access to the content repository.
- The [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI, signed in.
- A local clone of the content repository. The pipeline never touches it: every draft is
  committed from a temporary `git worktree` and pushed as a new branch.

### Install

```sh
uv tool install git+https://github.com/GusMartins499/airgap-scribe
docker pull ghcr.io/gusmartins499/airgap-scribe:<version>
```

### Configure

```sh
mkdir -p ~/.config/airgap-scribe/prompts
cp examples/pipeline.example.toml ~/.config/airgap-scribe/pipeline.toml
cp examples/prompts/example-talk.md ~/.config/airgap-scribe/prompts/talk.md
```

Edit `pipeline.toml`:

- `[paths] data_dir` holds `inbox/`, `processing/`, `done/`, `failed/`, `work/` and `models/`.
- `[drafting.prompts]` maps each meeting kind to its prompt file. Recordings must be named
  `YYYY-MM-DD_<kind>.<ext>` (for example `2026-11-09_talk.m4a`); a sibling `<stem>.txt`
  transcript, when present, is used instead of transcribing the audio.
- `[drafting] command` must disable every tool. The recommended command is:

  ```toml
  command = ["claude", "-p", "--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
  ```

- `[publish]` points at the content repository, the base branch, the content directory and
  the preview URL template (`{branch_slug}` follows the Cloudflare Pages branch alias rules).
- `[container] image` is the image pulled above.

Download the whisper model into `<data_dir>/models` with
`scripts/download-whisper-model.sh`.

### Notifications

Notifications go through [ntfy](https://ntfy.sh). The topic name is the only secret: anyone
who knows it can read the messages, so generate a long random one and keep it out of the
config file:

```sh
printf 'AIRGAP_SCRIBE_NTFY_TOPIC=%s\n' "$(openssl rand -hex 16)" > ~/.config/airgap-scribe/env
chmod 600 ~/.config/airgap-scribe/env
```

Subscribe to the same topic in the ntfy app on the phone.

### systemd user units

```sh
mkdir -p ~/.config/systemd/user
cp systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now airgap-scribe.path airgap-scribe-cleanup.timer
loginctl enable-linger "$USER"
```

- `airgap-scribe.path` watches the inbox and starts `airgap-scribe.service`, which processes
  every pending recording one after the other.
- `airgap-scribe-cleanup.timer` runs `airgap-scribe-cleanup.service` daily. Once a draft's
  pull request is merged, it deletes the work directory and the archived recording, keeping
  only the small JSON record in `done/`.

The units assume `data_dir = "~/airgap-scribe-data"`, the config in
`~/.config/airgap-scribe/` and the executables in `~/.local/bin`; adjust `PathChanged=`,
`ExecStart=` and `PATH` if yours differ.

### Operating

- A processed recording ends in `done/`, with `done/<stem>.json` holding the pull request
  URL, branch and alert count.
- A failure moves the recording to `failed/`, writes `failed/<stem>.error.txt` and sends a
  high-priority notification naming the failing stage. To retry, move the recording back to
  `inbox/`.
- `airgap-scribe run <file> --config <config>` processes a single recording by hand.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```sh
uv sync
uv run ruff check
uv run pytest
uv run python scripts/check-module-coverage.py --total-lines 85 --total-branches 80
```

Optionally install the guard that blocks committing audio, models and transcripts:

```sh
uvx pre-commit install
```

## License

[MIT](LICENSE)
