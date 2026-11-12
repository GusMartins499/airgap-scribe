import struct
import zlib

ORIENTATION_TAG = 0x0112
SHORT = 3
EXIF_HEADER = b"Exif\x00\x00"
ICC_PROFILE_HEADER = b"ICC_PROFILE\x00"
ADOBE_HEADER = b"Adobe"
JFIF_HEADER = b"JFIF\x00"

JPEG_SOI = b"\xff\xd8"
JPEG_APP0 = 0xE0
JPEG_APP1 = 0xE1
JPEG_APP2 = 0xE2
JPEG_APP14 = 0xEE
JPEG_SOS = 0xDA
JPEG_EOI = 0xD9
JPEG_COM = 0xFE
JPEG_STANDALONE_MARKERS = frozenset({0x01, *range(0xD0, 0xD8)})

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_KEPT_CHUNKS = frozenset(
    {
        b"IHDR",
        b"PLTE",
        b"IDAT",
        b"IEND",
        b"tRNS",
        b"gAMA",
        b"cHRM",
        b"sRGB",
        b"iCCP",
        b"sBIT",
        b"pHYs",
        b"acTL",
        b"fcTL",
        b"fdAT",
    }
)
PNG_EXIF = b"eXIf"
PNG_IEND = b"IEND"

WEBP_DROPPED_CHUNKS = frozenset({b"EXIF", b"XMP "})
WEBP_VP8X = b"VP8X"
WEBP_EXIF = b"EXIF"
WEBP_EXIF_FLAG = 0x08
WEBP_XMP_FLAG = 0x04


class PhotoError(Exception):
    pass


def strip_metadata(data: bytes, extension: str) -> bytes:
    extension = extension.lower().lstrip(".")
    try:
        if extension in ("jpg", "jpeg"):
            return _strip_jpeg(data)
        if extension == "png":
            return _strip_png(data)
        if extension == "webp":
            return _strip_webp(data)
    except (IndexError, struct.error) as error:
        raise PhotoError("the file is truncated or damaged") from error
    raise PhotoError(f"unsupported photo format '.{extension}'")


def orientation(tiff: bytes) -> int | None:
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    order = "<" if tiff[:2] == b"II" else ">"
    (ifd_offset,) = struct.unpack_from(order + "I", tiff, 4)
    if ifd_offset + 2 > len(tiff):
        return None
    (count,) = struct.unpack_from(order + "H", tiff, ifd_offset)
    for index in range(count):
        entry = ifd_offset + 2 + index * 12
        if entry + 12 > len(tiff):
            return None
        tag, kind, _, value = struct.unpack_from(order + "HHIH", tiff, entry)
        if tag == ORIENTATION_TAG and kind == SHORT:
            return value if 1 <= value <= 8 else None
    return None


def orientation_tiff(value: int) -> bytes:
    return (
        b"II*\x00"
        + struct.pack("<IH", 8, 1)
        + struct.pack("<HHIHH", ORIENTATION_TAG, SHORT, 1, value, 0)
        + struct.pack("<I", 0)
    )


def _kept_orientation(tiff: bytes | None) -> int | None:
    value = None if tiff is None else orientation(tiff)
    return None if value in (None, 1) else value


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(JPEG_SOI):
        raise PhotoError("not a JPEG file")
    kept: list[bytes] = []
    exif: bytes | None = None
    position = 2
    while True:
        if data[position] != 0xFF:
            raise PhotoError("the JPEG structure is damaged")
        marker = data[position + 1]
        if marker == 0xFF:
            position += 1
            continue
        if marker == JPEG_EOI:
            end = position + 2
            break
        if marker in JPEG_STANDALONE_MARKERS:
            kept.append(data[position : position + 2])
            position += 2
            continue
        (length,) = struct.unpack_from(">H", data, position + 2)
        segment_end = position + 2 + length
        if segment_end > len(data):
            raise PhotoError("the JPEG structure is damaged")
        payload = data[position + 4 : segment_end]
        if marker == JPEG_SOS:
            scan_end = _scan_end(data, segment_end)
            kept.append(data[position:scan_end])
            position = scan_end
            continue
        if marker == JPEG_APP1 and payload.startswith(EXIF_HEADER) and exif is None:
            exif = payload[len(EXIF_HEADER) :]
        if _keep_jpeg_segment(marker, payload):
            kept.append(data[position:segment_end])
        position = segment_end
    value = _kept_orientation(exif)
    if value is not None:
        payload = EXIF_HEADER + orientation_tiff(value)
        segment = bytes([0xFF, JPEG_APP1]) + struct.pack(">H", len(payload) + 2) + payload
        at = 1 if kept and kept[0][1] == JPEG_APP0 else 0
        kept.insert(at, segment)
    return JPEG_SOI + b"".join(kept) + data[end - 2 : end]


def _keep_jpeg_segment(marker: int, payload: bytes) -> bool:
    if marker == JPEG_APP0:
        return payload.startswith(JFIF_HEADER)
    if marker == JPEG_APP2:
        return payload.startswith(ICC_PROFILE_HEADER)
    if marker == JPEG_APP14:
        return payload.startswith(ADOBE_HEADER)
    return not (0xE0 <= marker <= 0xEF or marker == JPEG_COM)


def _scan_end(data: bytes, position: int) -> int:
    while True:
        position = data.index(b"\xff", position)
        following = data[position + 1]
        if following == 0x00 or 0xD0 <= following <= 0xD7 or following == 0xFF:
            position += 1
            continue
        return position


def _strip_png(data: bytes) -> bytes:
    if not data.startswith(PNG_SIGNATURE):
        raise PhotoError("not a PNG file")
    kept: list[bytes] = []
    exif: bytes | None = None
    position = len(PNG_SIGNATURE)
    while True:
        (length,) = struct.unpack_from(">I", data, position)
        kind = data[position + 4 : position + 8]
        chunk_end = position + 12 + length
        if chunk_end > len(data):
            raise PhotoError("the PNG structure is damaged")
        if kind == PNG_EXIF:
            exif = data[position + 8 : position + 8 + length]
        if kind in PNG_KEPT_CHUNKS:
            if kind == PNG_IEND:
                value = _kept_orientation(exif)
                if value is not None:
                    kept.insert(1, _png_chunk(PNG_EXIF, orientation_tiff(value)))
            kept.append(data[position:chunk_end])
        if kind == PNG_IEND:
            return PNG_SIGNATURE + b"".join(kept)
        position = chunk_end


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _strip_webp(data: bytes) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise PhotoError("not a WebP file")
    (riff_size,) = struct.unpack_from("<I", data, 4)
    end = min(len(data), 8 + riff_size)
    chunks: list[bytearray] = []
    exif: bytes | None = None
    position = 12
    while position + 8 <= end:
        kind = data[position : position + 4]
        (length,) = struct.unpack_from("<I", data, position + 4)
        chunk_end = position + 8 + length + (length & 1)
        if position + 8 + length > end:
            raise PhotoError("the WebP structure is damaged")
        if kind == WEBP_EXIF:
            payload = data[position + 8 : position + 8 + length]
            exif = payload.removeprefix(EXIF_HEADER)
        if kind not in WEBP_DROPPED_CHUNKS:
            chunks.append(bytearray(data[position:chunk_end]))
        position = chunk_end
    value = _kept_orientation(exif)
    extended = chunks and chunks[0][:4] == WEBP_VP8X
    if extended:
        chunks[0][8] &= ~(WEBP_EXIF_FLAG | WEBP_XMP_FLAG) & 0xFF
        if value is not None:
            chunks[0][8] |= WEBP_EXIF_FLAG
            tiff = orientation_tiff(value)
            chunks.append(bytearray(WEBP_EXIF + struct.pack("<I", len(tiff)) + tiff))
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body
