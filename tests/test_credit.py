import json
import struct
from pathlib import Path

import pytest

from airgap_scribe.credit import (
    Credit,
    CreditError,
    apply,
    assemble,
    attachment_files,
    attachment_stem,
    instagram_handle,
    looks_like_credit_note,
    parse_credit_note,
    read_credit,
    write_credit,
)
from airgap_scribe.workdir import CREDIT, DRAFT

STEM = "2026-12-14_roda"
NOTE = "Nome: Fulano de Tal\nInstagram: @fulano\nFoto: Participantes reunidos em roda\n"
DRAFT_TEXT = "---\ntitle: A title\ndate: 2026-12-14\nkind: roda\n---\n\nBody.\n"


def photo_bytes() -> bytes:
    def segment(marker: int, payload: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload

    return (
        b"\xff\xd8"
        + segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        + segment(0xFE, b"taken at a private address")
        + segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
        + b"\x12\x34\xff\xd9"
    )


def test_parse_credit_note_reads_every_field() -> None:
    credit, warnings = parse_credit_note(NOTE)

    assert credit == Credit(
        name="Fulano de Tal", instagram="fulano", photo_alt="Participantes reunidos em roda"
    )
    assert warnings == []


def test_parse_credit_note_is_lenient_about_keys_spacing_and_line_endings() -> None:
    text = "﻿  NOME :  Fulana   da Silva \r\nINSTAGRÃM: fulana\r\nfôto:\r\nnotes without colon\r\n"

    credit, warnings = parse_credit_note(text)

    assert credit == Credit(name="Fulana da Silva", instagram="fulana", photo_alt=None)
    assert warnings == []


def test_parse_credit_note_keeps_the_first_value_of_a_repeated_key() -> None:
    credit, _ = parse_credit_note("Nome: Primeira\nNome: Segunda\n")

    assert credit.name == "Primeira"


@pytest.mark.parametrize("text", ["", "Instagram: @fulano\n", "Nome:   \n"])
def test_parse_credit_note_without_a_name_is_an_error(text: str) -> None:
    with pytest.raises(CreditError, match="no `Nome:` line"):
        parse_credit_note(text)


def test_parse_credit_note_drops_an_invalid_instagram_with_a_warning() -> None:
    credit, warnings = parse_credit_note("Nome: Fulano\nInstagram: not a handle!\n")

    assert credit.instagram is None
    assert warnings == [
        "`not a handle!` in the credit note is not an Instagram handle; left it out."
    ]


@pytest.mark.parametrize(
    ("value", "handle"),
    [
        ("@fulano", "fulano"),
        ("fulano.de_tal", "fulano.de_tal"),
        ("https://www.instagram.com/fulano/", "fulano"),
        ("instagram.com/fulano?igsh=abc", "fulano"),
        ("two words", None),
        ("@" + "a" * 31, None),
    ],
)
def test_instagram_handle(value: str, handle: str | None) -> None:
    assert instagram_handle(value) == handle


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (NOTE, True),
        ("\n\n  nome: Fulano\n", True),
        ("[PERSON_1]: Nome: is mentioned later\n", False),
        ("Speaker 1: hello\nNome: Fulano\n", False),
        ("Nome sem dois pontos\n", False),
        ("", False),
    ],
)
def test_looks_like_credit_note(text: str, expected: bool) -> None:
    assert looks_like_credit_note(text) is expected


@pytest.mark.parametrize(
    ("name", "stem"),
    [
        (f"{STEM}-credito.txt", STEM),
        (f"{STEM}-Crédito.TXT", STEM),
        (f"{STEM}.jpg", STEM),
        (f"{STEM}.JPEG", STEM),
        (f"{STEM}.png", STEM),
        (f"{STEM}.webp", STEM),
        (f"{STEM}.heic", STEM),
        ("2026-12-14_roda-de-conversa.jpg", "2026-12-14_roda-de-conversa"),
        (f"{STEM}.txt", None),
        (f"{STEM}.m4a", None),
        (f"{STEM}-notes.txt", None),
        ("photo.jpg", None),
    ],
)
def test_attachment_stem(name: str, stem: str | None) -> None:
    assert attachment_stem(name) == stem


def test_attachment_files_finds_only_the_meeting_attachments(tmp_path: Path) -> None:
    for name in (f"{STEM}-credito.txt", f"{STEM}.jpg", f"{STEM}.m4a", "2026-12-15_roda.jpg"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / f"{STEM}.png").mkdir()

    assert [path.name for path in attachment_files(tmp_path, STEM)] == [
        f"{STEM}-credito.txt",
        f"{STEM}.jpg",
    ]
    assert attachment_files(tmp_path / "missing", STEM) == []


def test_assemble_with_note_and_photo_builds_the_front_matter(tmp_path: Path) -> None:
    (tmp_path / f"{STEM}-credito.txt").write_text(NOTE, encoding="utf-8")
    (tmp_path / f"{STEM}.JPG").write_bytes(photo_bytes())

    assembly = assemble(STEM, tmp_path)

    assert assembly.keys == ("speaker", "photo", "photoAlt")
    assert assembly.front_matter == (
        "speaker:",
        '  name: "Fulano de Tal"',
        '  instagram: "fulano"',
        f'photo: "./{STEM}.jpg"',
        'photoAlt: "Participantes reunidos em roda"',
    )
    assert assembly.photo_name == f"{STEM}.jpg"
    assert assembly.photo_data is not None
    assert b"private address" not in assembly.photo_data
    assert assembly.summary == (
        "Credit: Fulano de Tal (@fulano).",
        f"Photo: `{STEM}.jpg`, without its metadata.",
    )


def test_assemble_without_attachments_says_what_is_missing(tmp_path: Path) -> None:
    assembly = assemble(STEM, tmp_path)

    assert assembly.keys == ()
    assert assembly.front_matter == ()
    assert assembly.photo_name is None
    assert assembly.summary == (
        f"No credit note yet: send `{STEM}-credito.txt` to credit the speaker.",
        f"No meeting photo yet: send `{STEM}.jpg`.",
    )


def test_assemble_leaves_out_a_photo_without_alt_text(tmp_path: Path) -> None:
    (tmp_path / f"{STEM}-credito.txt").write_text('Nome: Ana "Aninha" Souza\n', encoding="utf-8")
    (tmp_path / f"{STEM}.jpg").write_bytes(photo_bytes())

    assembly = assemble(STEM, tmp_path)

    assert assembly.keys == ("speaker",)
    assert assembly.front_matter == ("speaker:", '  name: "Ana \\"Aninha\\" Souza"')
    assert assembly.photo_name is None
    assert assembly.photo_data is None
    assert assembly.summary[1] == (
        f"`{STEM}.jpg` is not in the post: the credit note needs a `Foto:` line "
        "describing the photo (`photoAlt`)."
    )


def test_assemble_without_a_note_leaves_the_photo_out(tmp_path: Path) -> None:
    (tmp_path / f"{STEM}.jpg").write_bytes(photo_bytes())

    assembly = assemble(STEM, tmp_path)

    assert assembly.keys == ()
    assert "is not in the post" in assembly.summary[1]


def test_assemble_reports_a_note_without_name(tmp_path: Path) -> None:
    (tmp_path / f"{STEM}-credito.txt").write_text("Foto: Uma sala\n", encoding="utf-8")

    assembly = assemble(STEM, tmp_path)

    assert assembly.keys == ()
    assert assembly.summary[0] == (
        "The credit note has no `Nome:` line, so the post has no credit."
    )


def test_assemble_reports_a_note_that_is_not_utf8(tmp_path: Path) -> None:
    (tmp_path / f"{STEM}-credito.txt").write_bytes("Nome: Jo\xe3o".encode("utf-16"))

    assembly = assemble(STEM, tmp_path)

    assert assembly.summary[0] == f"The credit note `{STEM}-credito.txt` is not UTF-8 text."


def test_assemble_reports_heic_damaged_and_extra_photos(tmp_path: Path) -> None:
    (tmp_path / f"{STEM}-credito.txt").write_text(NOTE, encoding="utf-8")
    (tmp_path / f"{STEM}.heic").write_bytes(b"heic")
    (tmp_path / f"{STEM}.jpeg").write_bytes(b"broken")
    (tmp_path / f"{STEM}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND" + b"0000")
    (tmp_path / f"{STEM}.webp").write_bytes(b"webp")

    assembly = assemble(STEM, tmp_path)

    assert assembly.photo_name == f"{STEM}.png"
    assert assembly.summary == (
        "Credit: Fulano de Tal (@fulano).",
        f"`{STEM}.heic` was ignored: HEIC photos are not supported; send it as JPEG.",
        f"`{STEM}.jpeg` could not be read: not a JPEG file.",
        f"`{STEM}.webp` was ignored: a post has only one photo.",
        f"Photo: `{STEM}.png`, without its metadata.",
    )


def test_assemble_with_only_heic_does_not_ask_for_a_photo_again(tmp_path: Path) -> None:
    (tmp_path / f"{STEM}.heic").write_bytes(b"heic")

    assembly = assemble(STEM, tmp_path)

    assert assembly.summary[-1] == (
        f"`{STEM}.heic` was ignored: HEIC photos are not supported; send it as JPEG."
    )


def test_apply_replaces_only_the_produced_keys(tmp_path: Path) -> None:
    (tmp_path / f"{STEM}-credito.txt").write_text("Nome: Fulano\n", encoding="utf-8")
    document = (
        "---\ntitle: Edited by the reviewer\nspeaker:\n  name: Old\n"
        'photo: "./x.jpg"\nphotoAlt: "Kept"\n---\n\nBody.\n'
    )

    updated = apply(document, assemble(STEM, tmp_path))

    assert updated == (
        "---\ntitle: Edited by the reviewer\n"
        'photo: "./x.jpg"\nphotoAlt: "Kept"\nspeaker:\n  name: "Fulano"\n---\n\nBody.\n'
    )


def test_apply_without_keys_returns_the_document_unchanged(tmp_path: Path) -> None:
    assert apply("not a draft", assemble(STEM, tmp_path)) == "not a draft"


def test_write_credit_updates_the_draft_and_keeps_the_photo(tmp_path: Path) -> None:
    sources = tmp_path / "processing"
    sources.mkdir()
    (sources / f"{STEM}-credito.txt").write_text(NOTE, encoding="utf-8")
    (sources / f"{STEM}.jpg").write_bytes(photo_bytes())
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / DRAFT).write_text(DRAFT_TEXT, encoding="utf-8")

    write_credit(work_dir, STEM, sources)

    draft = (work_dir / DRAFT).read_text(encoding="utf-8")
    assert draft.startswith(
        "---\ntitle: A title\ndate: 2026-12-14\nkind: roda\nspeaker:\n"
        '  name: "Fulano de Tal"\n  instagram: "fulano"\n'
        f'photo: "./{STEM}.jpg"\nphotoAlt: "Participantes reunidos em roda"\n---\n'
    )
    photo, summary = read_credit(work_dir)
    assert photo == work_dir / f"{STEM}.jpg"
    assert b"private address" not in photo.read_bytes()
    assert summary == [
        "Credit: Fulano de Tal (@fulano).",
        f"Photo: `{STEM}.jpg`, without its metadata.",
    ]


def test_write_credit_without_attachments_records_what_is_missing(tmp_path: Path) -> None:
    (tmp_path / DRAFT).write_text(DRAFT_TEXT, encoding="utf-8")

    write_credit(tmp_path, STEM, tmp_path / "processing")

    assert (tmp_path / DRAFT).read_text(encoding="utf-8") == DRAFT_TEXT
    record = json.loads((tmp_path / CREDIT).read_text(encoding="utf-8"))
    assert record["photo"] is None
    assert len(record["summary"]) == 2


def test_read_credit_without_a_record(tmp_path: Path) -> None:
    assert read_credit(tmp_path) == (None, [])
