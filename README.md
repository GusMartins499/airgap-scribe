# airgap-scribe

Turns a meeting recording into an anonymized written draft. Transcription and anonymization
run offline in a Docker container; only the anonymized transcript reaches the language model.

## Quick start

1. Prerequisites: [Docker](https://docs.docker.com/engine/install/),
   [uv](https://docs.astral.sh/uv/) and the
   [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI, signed in (`claude`).

   ```sh
   git clone https://github.com/GusMartins499/airgap-scribe
   cd airgap-scribe
   ```

2. Run the tests (same steps as CI):

   ```sh
   uv sync --locked
   mkdir -p ~/.cache/spacy-models
   curl --fail --location -o ~/.cache/spacy-models/pt_core_news_lg-3.8.0-py3-none-any.whl \
     https://github.com/explosion/spacy-models/releases/download/pt_core_news_lg-3.8.0/pt_core_news_lg-3.8.0-py3-none-any.whl
   uv pip install ~/.cache/spacy-models/pt_core_news_lg-3.8.0-py3-none-any.whl
   uv run --no-sync ruff check
   uv run --no-sync ruff format --check
   uv run --no-sync pytest
   uv run --no-sync python scripts/check-module-coverage.py --coverage coverage.json \
     --thresholds ci/coverage-thresholds --total-lines 85 --total-branches 80
   ```

3. Build the image:

   ```sh
   docker build -t airgap-scribe:local .
   ```

4. Download the Whisper model (about 550 MB):

   ```sh
   scripts/download-whisper-model.sh ~/airgap-scribe-data/models
   ```

5. Process any audio file:

   ```sh
   uv run --no-sync airgap-scribe try ~/Downloads/recording.m4a --config examples/try.example.toml
   ```

   The result lands in `./airgap-scribe-try/<file-name>-<timestamp>/`: `transcript.raw.txt`,
   `transcript.anon.txt`, `draft.md` and `alerts.json` (names that may have survived
   anonymization). `transcript.raw.txt` contains real names: delete it after reading.
   Options: `--kind <kind>` (default: the first kind in `[drafting.prompts]`) and
   `--out <dir>`. Nothing is published and no notification is sent.

## Full pipeline

An always-on Linux host receives recordings (for example through Syncthing), drafts them and
opens a pull request in a content repository for human review.

- Install: `uv tool install git+https://github.com/GusMartins499/airgap-scribe` and pull or
  build the image. Also needs `git` and the [GitHub CLI](https://cli.github.com/) signed in,
  with push access to the content repository.
- Configure: copy `examples/pipeline.example.toml` to `~/.config/airgap-scribe/pipeline.toml`
  and a prompt from `examples/prompts/`. `[paths] data_dir` holds `inbox/`, `processing/`,
  `done/`, `failed/`, `work/` and `models/`; `[publish]` points at the content repository;
  `[container] image` is the image to run.
- Recordings are named `YYYY-MM-DD_<kind>.<ext>`; a sibling `<stem>.txt` transcript, when
  present, replaces transcription.
- Notifications use [ntfy](https://ntfy.sh). The topic is the only secret:

  ```sh
  printf 'AIRGAP_SCRIBE_NTFY_TOPIC=%s\n' "$(openssl rand -hex 16)" > ~/.config/airgap-scribe/env
  chmod 600 ~/.config/airgap-scribe/env
  ```

- systemd user units watch the inbox and clean up merged drafts daily:

  ```sh
  mkdir -p ~/.config/systemd/user
  cp systemd/* ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now airgap-scribe.path airgap-scribe-cleanup.timer
  loginctl enable-linger "$USER"
  ```

  The units assume `data_dir = "~/airgap-scribe-data"`, the config in
  `~/.config/airgap-scribe/` and executables in `~/.local/bin`.
- Results: `done/<stem>.json` holds the pull request URL and alert count. A failure moves the
  recording to `failed/` with `<stem>.error.txt`; move it back to `inbox/` to retry.
  `airgap-scribe run <file> --config <config>` processes one recording by hand.

## Privacy

Raw audio and transcripts never leave the machine: every step that reads them runs with
`docker run --network none`. The model is called with every tool disabled and sees only the
anonymized transcript; a person reviews each draft before it is published.

## License

[MIT](LICENSE)
