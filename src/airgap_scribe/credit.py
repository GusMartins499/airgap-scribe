import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from airgap_scribe.draft import set_front_matter
from airgap_scribe.photo import PhotoError, strip_metadata
from airgap_scribe.workdir import CREDIT, DRAFT

CREDIT_NOTE_SUFFIX = "-credito.txt"
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
UNSUPPORTED_PHOTO_EXTENSIONS = (".heic", ".heif")
SPEAKER_KEYS = ("speaker",)
PHOTO_KEYS = ("photo", "photoAlt")
NAME_KEY = "nome"
INSTAGRAM_KEY = "instagram"
PHOTO_ALT_KEY = "foto"

_ATTACHMENT_NAME = re.compile(
    r"(?P<stem>[0-9]{4}-[0-9]{2}-[0-9]{2}_[^._]+?)"
    r"(?:-credito\.txt|\.(?:jpe?g|png|webp|heic|heif))"
)
_INSTAGRAM_HANDLE = re.compile(r"[A-Za-z0-9._]{1,30}")
_INSTAGRAM_URL = re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/(?P<handle>[^/?#]+)/?.*")


class CreditError(Exception):
    pass


@dataclass(frozen=True)
class Credit:
    name: str
    instagram: str | None
    photo_alt: str | None


@dataclass(frozen=True)
class Assembly:
    keys: tuple[str, ...]
    front_matter: tuple[str, ...]
    photo_name: str | None
    photo_data: bytes | None
    summary: tuple[str, ...]


def attachment_stem(name: str) -> str | None:
    match = _ATTACHMENT_NAME.fullmatch(_normalized(name))
    return None if match is None else match["stem"]


def attachment_files(directory: Path, stem: str) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        entry
        for entry in directory.iterdir()
        if entry.is_file() and attachment_stem(entry.name) == stem
    )


def looks_like_credit_note(text: str) -> bool:
    for line in text.lstrip("﻿").splitlines():
        if line.strip():
            key, separator, _ = line.partition(":")
            return bool(separator) and _normalized(key).strip() == NAME_KEY
    return False


def parse_credit_note(text: str) -> tuple[Credit, list[str]]:
    fields: dict[str, str] = {}
    for line in text.lstrip("﻿").splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = _normalized(key).strip()
        if key in (NAME_KEY, INSTAGRAM_KEY, PHOTO_ALT_KEY) and key not in fields:
            fields[key] = " ".join(value.split())
    name = fields.get(NAME_KEY, "")
    if not name:
        raise CreditError("The credit note has no `Nome:` line, so the post has no credit.")
    warnings = []
    instagram = None
    raw_instagram = fields.get(INSTAGRAM_KEY, "")
    if raw_instagram:
        instagram = instagram_handle(raw_instagram)
        if instagram is None:
            warnings.append(
                f"`{raw_instagram}` in the credit note is not an Instagram handle; left it out."
            )
    return Credit(
        name=name, instagram=instagram, photo_alt=fields.get(PHOTO_ALT_KEY) or None
    ), warnings


def instagram_handle(value: str) -> str | None:
    value = value.strip()
    url = _INSTAGRAM_URL.fullmatch(value)
    handle = url["handle"] if url else value.removeprefix("@")
    return handle if _INSTAGRAM_HANDLE.fullmatch(handle) else None


def assemble(stem: str, directory: Path) -> Assembly:
    files = attachment_files(directory, stem)
    notes = [file for file in files if _normalized(file.name).endswith(CREDIT_NOTE_SUFFIX)]
    photos = [file for file in files if file.suffix.lower() in PHOTO_EXTENSIONS]
    unsupported = [file for file in files if file.suffix.lower() in UNSUPPORTED_PHOTO_EXTENSIONS]
    keys: list[str] = []
    front_matter: list[str] = []
    summary: list[str] = []
    credit = None
    if notes:
        try:
            credit, warnings = parse_credit_note(_read_text(notes[0]))
        except CreditError as error:
            summary.append(str(error))
        else:
            keys += SPEAKER_KEYS
            front_matter += ["speaker:", f"  name: {_yaml_string(credit.name)}"]
            if credit.instagram:
                front_matter.append(f"  instagram: {_yaml_string(credit.instagram)}")
            handle = f" (@{credit.instagram})" if credit.instagram else ""
            summary += [f"Credit: {credit.name}{handle}.", *warnings]
    else:
        summary.append(
            f"No credit note yet: send `{stem}{CREDIT_NOTE_SUFFIX}` to credit the speaker."
        )
    summary += [
        f"`{file.name}` was ignored: HEIC photos are not supported; send it as JPEG."
        for file in unsupported
    ]
    photo, photo_data = _first_readable(photos, summary)
    photo_name = None
    if photo is None:
        if not photos and not unsupported:
            summary.append(f"No meeting photo yet: send `{stem}.jpg`.")
    elif credit is None or credit.photo_alt is None:
        summary.append(
            f"`{photo.name}` is not in the post: the credit note needs a `Foto:` line "
            "describing the photo (`photoAlt`)."
        )
        photo_data = None
    else:
        photo_name = stem + photo.suffix.lower()
        keys += PHOTO_KEYS
        front_matter += [
            f"photo: {_yaml_string('./' + photo_name)}",
            f"photoAlt: {_yaml_string(credit.photo_alt)}",
        ]
        summary.append(f"Photo: `{photo_name}`, without its metadata.")
    return Assembly(
        keys=tuple(keys),
        front_matter=tuple(front_matter),
        photo_name=photo_name,
        photo_data=photo_data,
        summary=tuple(summary),
    )


def _first_readable(photos: list[Path], summary: list[str]) -> tuple[Path | None, bytes | None]:
    chosen: tuple[Path | None, bytes | None] = (None, None)
    for photo in photos:
        if chosen[0] is not None:
            summary.append(f"`{photo.name}` was ignored: a post has only one photo.")
            continue
        try:
            chosen = (photo, strip_metadata(photo.read_bytes(), photo.suffix))
        except PhotoError as error:
            summary.append(f"`{photo.name}` could not be read: {error}.")
    return chosen


def apply(document: str, assembly: Assembly) -> str:
    if not assembly.keys:
        return document
    return set_front_matter(document, assembly.keys, assembly.front_matter)


def write_credit(work_dir: Path, stem: str, directory: Path) -> Assembly:
    assembly = assemble(stem, directory)
    draft = work_dir / DRAFT
    draft.write_text(apply(draft.read_text(encoding="utf-8"), assembly), encoding="utf-8")
    if assembly.photo_name is not None and assembly.photo_data is not None:
        (work_dir / assembly.photo_name).write_bytes(assembly.photo_data)
    record = {"photo": assembly.photo_name, "summary": list(assembly.summary)}
    (work_dir / CREDIT).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return assembly


def read_credit(work_dir: Path) -> tuple[Path | None, list[str]]:
    try:
        record = json.loads((work_dir / CREDIT).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, []
    photo = record.get("photo")
    return (None if photo is None else work_dir / photo), [str(line) for line in record["summary"]]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise CreditError(f"The credit note `{path.name}` is not UTF-8 text.") from error


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _normalized(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
