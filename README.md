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
  `[container] image` is the image to run. `[transcription] threads` must not exceed the CPUs
  the container can use (with Docker Desktop, the CPUs given to its VM); transcription fails
  early when it does.
- Recordings are named `YYYY-MM-DD_<kind>.<ext>`; a sibling `<stem>.txt` transcript, when
  present, replaces transcription. A credit note and a meeting photo can come along with them
  (see [Credit and photo](#credit-and-photo)).
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
  recording, its credit note and photo to `failed/` with `<stem>.error.txt`; move them back to
  `inbox/` to retry.
  `airgap-scribe run <file> --config <config>` processes one recording by hand.
- Interruptions: transcription runs in 5-minute chunks saved under `work/<stem>/`. If the host
  stops mid-run, the next scan resumes recordings left in `processing/`, skips the finished
  chunks and sends a "Resuming" notification. A container step that writes nothing to its work
  directory for 30 minutes is stopped and fails the recording.

## Credit and photo

The post can credit who gave the talk or led the conversation, when that person asks for it,
and show a photo of the meeting. Both are optional and go in the inbox next to the recording:

| File | What it is |
| --- | --- |
| `2026-12-14_roda.m4a` | The recording. Only audio starts processing. |
| `2026-12-14_roda-credito.txt` | The credit note. |
| `2026-12-14_roda.jpg` | The meeting photo: `.jpg`, `.jpeg`, `.png` or `.webp`. |

The credit note is plain text, one field per line:

```text
Nome: Fulano de Tal
Instagram: @fulano
Foto: Participantes reunidos em roda na sala do encontro
```

`Nome` is required; `Instagram` (with or without `@`, or a profile link) and `Foto` are
optional. Keys ignore case and accents. `Foto` is the description of the photo for people who
cannot see it (`photoAlt`), and the website refuses a photo without it, so a photo without a
`Foto:` line stays out of the post until the line arrives.

The draft gets this front matter:

```yaml
speaker:
  name: "Fulano de Tal"
  instagram: "fulano"
photo: "./2026-12-14_roda.jpg"
photoAlt: "Participantes reunidos em roda na sala do encontro"
```

- **The name never reaches the language model.** The transcript stays anonymized as before;
  the credit goes straight into the front matter after the name check, like `date` and `kind`.
  Whatever the model writes in `speaker`, `photo` or `photoAlt` is dropped.
- **The photo keeps its format and quality.** Only its metadata is removed (location, camera,
  dates); the orientation is kept so the photo is not shown sideways. The website builds the
  WebP sizes and the link-preview JPEG itself. HEIC photos are not supported: send them as
  JPEG.
- **Files can arrive in any order.** They are read when the draft is assembled, so a note or
  photo sent before the recording or during transcription is picked up. The pull request body
  lists what was credited and what is missing (no note, a note without `Nome:`, a photo
  without `Foto:`).
- **After the pull request opened**, a new note or photo is committed to the same draft branch,
  keeping the reviewer's edits, with a comment on the pull request; the preview updates on its
  own. A corrected note replaces the previous one.
- **After it was merged or closed**, the files stay in the inbox and a notification says so,
  once per file; add them to the post by hand. `cleanup` removes credit notes and photos that
  have waited in the inbox for 7 days without their recording.
- **`<stem>.txt` still means "ready-made transcript".** Only `<stem>-credito.txt` is a credit
  note. A transcript that starts with `Nome:` is refused, so a misnamed note does not become
  the post.

From a phone: write the note in Samsung Notes (or any text editor), save it as a text file
titled `<stem>-credito` (Samsung Notes: save as text file), and move it and the
photo to the synced inbox folder, in any order.

## Releasing

Bump `version` in `pyproject.toml` in a pull request. Once it is merged and CI passes on
`main`, the image `ghcr.io/gusmartins499/airgap-scribe:<version>`, the `v<version>` tag and the
GitHub release are created automatically.

## Privacy

Raw audio and transcripts never leave the machine: every step that reads them runs with
`docker run --network none`. The model is called with every tool disabled and sees only the
anonymized transcript; a person reviews each draft before it is published. The speaker's name
from the credit note is added after drafting and never sent to the model, and the photo's
metadata is removed before it is committed.

## License

[MIT](LICENSE)
