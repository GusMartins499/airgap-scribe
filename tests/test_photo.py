import struct
import zlib

import pytest

from airgap_scribe.photo import PhotoError, orientation, orientation_tiff, strip_metadata

GPS_SECRET = b"GPS -27.59 -48.54 Galaxy S23"


def segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def exif_tiff(value: int, extra: bytes = GPS_SECRET) -> bytes:
    order = b"MM\x00*" + struct.pack(">I", 8)
    entries = struct.pack(">H", 2)
    entries += struct.pack(">HHIHH", 0x010F, 2, len(extra), 0, 0)
    entries += struct.pack(">HHIHH", 0x0112, 3, 1, value, 0)
    return order + entries + struct.pack(">I", 0) + extra


JFIF = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
ICC = segment(0xE2, b"ICC_PROFILE\x00\x01\x01color profile")
MPF = segment(0xE2, b"MPF\x00secondary image index")
XMP = segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + GPS_SECRET)
COMMENT = segment(0xFE, GPS_SECRET)
DQT = segment(0xDB, b"\x00" + bytes(64))
SOF = segment(0xC0, b"\x08\x00\x10\x00\x10\x01\x01\x11\x00")
DHT = segment(0xC4, b"\x00" + bytes(16))
SOS = segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
SCAN = b"\x12\xff\x00\x34\xff\xd0\x56\x78"
EOI = b"\xff\xd9"


def jpeg(*segments: bytes, trailer: bytes = b"") -> bytes:
    return b"\xff\xd8" + b"".join(segments) + SOS + SCAN + EOI + trailer


def test_jpeg_keeps_the_image_data_and_drops_every_metadata_block() -> None:
    exif = segment(0xE1, b"Exif\x00\x00" + exif_tiff(1))
    original = jpeg(JFIF, exif, XMP, ICC, MPF, COMMENT, DQT, SOF, DHT, trailer=b"motion video")

    stripped = strip_metadata(original, ".JPG")

    assert stripped == jpeg(JFIF, ICC, DQT, SOF, DHT)
    assert GPS_SECRET not in stripped


def test_jpeg_keeps_only_the_orientation_from_exif() -> None:
    exif = segment(0xE1, b"Exif\x00\x00" + exif_tiff(6))

    stripped = strip_metadata(jpeg(JFIF, exif, DQT, SOF, DHT), "jpeg")

    minimal_exif = segment(0xE1, b"Exif\x00\x00" + orientation_tiff(6))
    assert stripped == jpeg(JFIF, minimal_exif, DQT, SOF, DHT)
    assert GPS_SECRET not in stripped


def test_jpeg_without_jfif_gets_the_orientation_first() -> None:
    exif = segment(0xE1, b"Exif\x00\x00" + exif_tiff(8))

    stripped = strip_metadata(jpeg(exif, DQT, SOF), "jpg")

    assert stripped == jpeg(segment(0xE1, b"Exif\x00\x00" + orientation_tiff(8)), DQT, SOF)


def test_progressive_jpeg_keeps_every_scan() -> None:
    second_scan = DHT + SOS + b"\x9a\xff\x00"
    original = b"\xff\xd8" + JFIF + COMMENT + SOF + SOS + SCAN + second_scan + EOI

    stripped = strip_metadata(original, "jpg")

    assert stripped == b"\xff\xd8" + JFIF + SOF + SOS + SCAN + second_scan + EOI


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not an image", "not a JPEG file"),
        (b"\xff\xd8\xff", "truncated or damaged"),
        (b"\xff\xd8" + JFIF[:6], "structure is damaged"),
        (b"\xff\xd8\x00\x00", "structure is damaged"),
        (b"\xff\xd8\xff\xdb\x00\x40\x00", "structure is damaged"),
    ],
)
def test_damaged_jpeg_is_reported(data: bytes, message: str) -> None:
    with pytest.raises(PhotoError, match=message):
        strip_metadata(data, "jpg")


def chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


PNG = b"\x89PNG\r\n\x1a\n"
IHDR = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
IDAT = chunk(b"IDAT", b"pixels")
IEND = chunk(b"IEND", b"")


def test_png_drops_text_and_time_and_keeps_the_orientation() -> None:
    original = (
        PNG
        + IHDR
        + chunk(b"tEXt", b"Comment\x00" + GPS_SECRET)
        + chunk(b"eXIf", exif_tiff(3))
        + chunk(b"tIME", b"\x07\xea\x0b\x0c\x00\x00\x00")
        + IDAT
        + IEND
    )

    stripped = strip_metadata(original, ".png")

    assert stripped == PNG + IHDR + chunk(b"eXIf", orientation_tiff(3)) + IDAT + IEND


def test_png_without_orientation_has_no_exif() -> None:
    original = PNG + IHDR + chunk(b"eXIf", exif_tiff(1)) + IDAT + IEND

    assert strip_metadata(original, "png") == PNG + IHDR + IDAT + IEND


@pytest.mark.parametrize("data", [b"GIF89a", PNG + IHDR[:-2]])
def test_damaged_png_is_reported(data: bytes) -> None:
    with pytest.raises(PhotoError):
        strip_metadata(data, "png")


def riff(*chunks: bytes) -> bytes:
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_chunk(kind: bytes, payload: bytes) -> bytes:
    padding = b"\x00" if len(payload) % 2 else b""
    return kind + struct.pack("<I", len(payload)) + payload + padding


def vp8x(flags: int) -> bytes:
    return webp_chunk(b"VP8X", bytes([flags, 0, 0, 0]) + bytes(6))


def test_webp_drops_xmp_and_keeps_the_orientation() -> None:
    original = riff(
        vp8x(0x0C),
        webp_chunk(b"VP8 ", b"frame"),
        webp_chunk(b"EXIF", b"Exif\x00\x00" + exif_tiff(6)),
        webp_chunk(b"XMP ", GPS_SECRET),
    )

    stripped = strip_metadata(original, "webp")

    assert stripped == riff(
        vp8x(0x08),
        webp_chunk(b"VP8 ", b"frame"),
        webp_chunk(b"EXIF", orientation_tiff(6)),
    )


def test_webp_without_orientation_clears_the_metadata_flags() -> None:
    original = riff(vp8x(0x2C), webp_chunk(b"VP8L", b"lossless"), webp_chunk(b"XMP ", b"x"))

    assert strip_metadata(original, "webp") == riff(vp8x(0x20), webp_chunk(b"VP8L", b"lossless"))


def test_simple_webp_is_unchanged() -> None:
    original = riff(webp_chunk(b"VP8 ", b"frame"))

    assert strip_metadata(original, "webp") == original


@pytest.mark.parametrize(
    "data", [b"RIFF\x00\x00\x00\x00WAVE", riff(b"VP8 " + struct.pack("<I", 99) + b"x")]
)
def test_damaged_webp_is_reported(data: bytes) -> None:
    with pytest.raises(PhotoError):
        strip_metadata(data, "webp")


def test_other_formats_are_refused() -> None:
    with pytest.raises(PhotoError, match=r"unsupported photo format '\.heic'"):
        strip_metadata(b"ftypheic", ".heic")


@pytest.mark.parametrize(
    "tiff",
    [
        b"",
        b"XX\x00*\x00\x00\x00\x08",
        b"II*\x00" + struct.pack("<I", 99),
        b"II*\x00" + struct.pack("<IH", 8, 3),
        exif_tiff(9),
    ],
)
def test_orientation_ignores_malformed_exif(tiff: bytes) -> None:
    assert orientation(tiff) is None


def test_orientation_reads_little_and_big_endian_exif() -> None:
    assert orientation(exif_tiff(5)) == 5
    assert orientation(orientation_tiff(7)) == 7
