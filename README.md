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
