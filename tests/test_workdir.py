from airgap_scribe import workdir

WORK_FILE_NAMES = [
    workdir.AUDIO_WAV,
    workdir.TRANSCRIPT_RAW,
    workdir.TRANSCRIPT_ANON,
    workdir.ENTITIES,
    workdir.MAPPING,
    workdir.DRAFT,
    workdir.ALERTS,
    workdir.CONTAINER_CONFIG,
]


def test_work_file_names_are_distinct_plain_file_names() -> None:
    assert len(set(WORK_FILE_NAMES)) == len(WORK_FILE_NAMES)
    assert all("/" not in name and name for name in WORK_FILE_NAMES)


def test_work_file_names_match_the_documented_contract() -> None:
    assert WORK_FILE_NAMES == [
        "audio.wav",
        "transcript.raw.txt",
        "transcript.anon.txt",
        "entities.json",
        "mapping.json",
        "draft.md",
        "alerts.json",
        "container.toml",
    ]
