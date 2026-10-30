"""The audio of one speech part, before it joins the stream of the whole text.

A long text is spoken in parts, one OpenRouter request each, and their audio is appended into
one stream. An MP3 part starts with its own ID3 tag and an info frame (``Xing``, ``Info`` or
``VBRI``) that describes that part only: :class:`Mp3HeadFilter` drops the info frame of every
part, and the ID3 tag of every part after the first. A PCM part can end inside a frame:
:class:`PcmFrameTrimmer` drops that partial frame, which would shift every later part.

This module also holds the one MP3 frame-header and ID3-size parser of the package.
"""

from typing import NamedTuple

ID3_HEADER_SIZE = 10
MP3_HEADER_SIZE = 4

# MPEG audio bitrates (kbit/s) by bitrate index, for Layer III: MPEG-1, and MPEG-2 / 2.5.
_MP3_BITRATES = {
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
# Sample rates (Hz) by sample rate index, by version bits (3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5).
_MP3_SAMPLE_RATES = {
    3: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    0: (11025, 12000, 8000),
}
_MP3_MONO = 3  # channel mode
_SIDE_INFO_TAGS = (b"Xing", b"Info")
_VBRI_TAG = b"VBRI"
_VBRI_OFFSET = 36


class Mp3Frame(NamedTuple):
    bitrate: int  # bit/s
    sample_rate: int  # Hz
    length: int  # bytes, header and padding included
    side_info_end: int  # offset of a Xing / Info tag, from the frame start


def id3_tag_size(data: bytes | bytearray) -> int | None:
    """The whole size of the ID3v2 tag that starts ``data`` (header, payload and footer).

    None when ``data`` does not start with a whole ID3v2 header.
    """
    if len(data) < ID3_HEADER_SIZE or data[:3] != b"ID3":
        return None
    size = data[6:10]
    payload = (size[0] << 21) | (size[1] << 14) | (size[2] << 7) | size[3]
    footer = ID3_HEADER_SIZE if data[5] & 0x10 else 0
    return ID3_HEADER_SIZE + payload + footer


def mp3_frame(data: bytes | bytearray, offset: int = 0) -> Mp3Frame | None:
    """The MPEG Layer III frame whose header starts at ``offset`` in ``data``.

    None when the 4 bytes there are not a valid Layer III frame header (or are missing).
    """
    if len(data) < offset + MP3_HEADER_SIZE:
        return None
    b0, b1, b2, b3 = data[offset : offset + MP3_HEADER_SIZE]
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None
    version_bits = (b1 >> 3) & 0x03  # 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5, 1 = reserved
    layer_bits = (b1 >> 1) & 0x03  # 1 = Layer III
    bitrate_index = b2 >> 4
    rate_index = (b2 >> 2) & 0x03
    if version_bits == 1 or layer_bits != 1 or bitrate_index in (0, 15) or rate_index == 3:
        return None
    mpeg1 = version_bits == 3
    bitrate = _MP3_BITRATES[1 if mpeg1 else 2][bitrate_index] * 1000
    sample_rate = _MP3_SAMPLE_RATES[version_bits][rate_index]
    padding = (b2 >> 1) & 0x01
    mono = (b3 >> 6) == _MP3_MONO
    length = (144 if mpeg1 else 72) * bitrate // sample_rate + padding
    if mpeg1:
        side_info_end = 21 if mono else 36
    else:
        side_info_end = 13 if mono else 21
    return Mp3Frame(bitrate, sample_rate, length, side_info_end)


def info_frame_kind(frame: bytes | bytearray, header: Mp3Frame) -> bytes | None:
    """``b"Xing"``, ``b"Info"`` or ``b"VBRI"`` when the whole ``frame`` is an info frame, else None."""
    tag = bytes(frame[header.side_info_end : header.side_info_end + 4])
    if tag in _SIDE_INFO_TAGS:
        return tag
    if bytes(frame[_VBRI_OFFSET : _VBRI_OFFSET + 4]) == _VBRI_TAG:
        return _VBRI_TAG
    return None


class Mp3HeadFilter:
    """Filters the head of one MP3 part: its ID3 tag (dropped when ``drop_id3``, else passed)
    and its first frame (dropped when it is an info frame, else passed). It holds back only the
    head of the part, then passes every byte through. Bytes that do not start with a frame
    header where one must start pass through unchanged.

    ``info_frame`` is the kind of the dropped info frame, else None.
    """

    _ID3 = "id3"  # deciding whether the part starts with an ID3 tag
    _SKIP = "skip"  # inside the ID3 tag: dropping or passing ``_remaining`` more bytes
    _FRAME = "frame"  # waiting for the whole first frame
    _PASS = "pass"  # head done: every byte passes

    def __init__(self, *, drop_id3: bool):
        self._drop_id3 = drop_id3
        self._state = self._ID3
        self._held = bytearray()
        self._remaining = 0
        self.info_frame: bytes | None = None

    def feed(self, chunk: bytes) -> bytes:
        """The bytes to append now."""
        if self._state == self._PASS:
            return chunk
        out = bytearray()
        data = bytes(chunk)
        while data and self._state != self._PASS:
            if self._state == self._SKIP:
                count = min(self._remaining, len(data))
                if not self._drop_id3:
                    out += data[:count]
                self._remaining -= count
                data = data[count:]
                if not self._remaining:
                    self._state = self._FRAME
            else:
                self._held += data
                data = self._read_id3(out) if self._state == self._ID3 else self._read_first_frame(out)
        return bytes(out)

    def flush(self) -> bytes:
        """What it still holds, at the end of the part."""
        held = bytes(self._held)
        self._held.clear()
        self._state = self._PASS
        return held

    def _read_id3(self, out: bytearray) -> bytes:
        """Reads the held head as an ID3 tag header. Returns the held bytes that follow it, still
        to read (the tag payload and what follows)."""
        held = self._held
        if held[:3] != b"ID3"[: len(held)]:
            self._state = self._FRAME
            return self._read_first_frame(out)
        size = id3_tag_size(held)
        if size is None:
            return b""  # wait for the whole tag header
        if not self._drop_id3:
            out += held[:ID3_HEADER_SIZE]
        rest = bytes(held[ID3_HEADER_SIZE:])
        held.clear()
        self._remaining = size - ID3_HEADER_SIZE
        self._state = self._SKIP if self._remaining else self._FRAME
        return rest

    def _read_first_frame(self, out: bytearray) -> bytes:
        """Passes or drops the held first frame once it is whole. Returns no bytes to read: the
        held bytes are kept or given, never handed back."""
        held = self._held
        if len(held) < MP3_HEADER_SIZE:
            return b""
        header = mp3_frame(held)
        if header is None:
            self._pass_held(out)
            return b""
        if len(held) < header.length:
            return b""
        kind = info_frame_kind(held[: header.length], header)
        if kind is not None:
            self.info_frame = kind
            del held[: header.length]
        self._pass_held(out)
        return b""

    def _pass_held(self, out: bytearray) -> None:
        out += self._held
        self._held.clear()
        self._state = self._PASS


class PcmFrameTrimmer:
    """Passes whole PCM frames of ``frame_size`` bytes only, and holds the partial frame."""

    def __init__(self, frame_size: int):
        if frame_size < 1:
            raise ValueError("frame_size must be positive")
        self._frame_size = frame_size
        self._held = b""

    def feed(self, chunk: bytes) -> bytes:
        """The whole frames received so far and not given yet."""
        data = self._held + chunk
        whole = len(data) - len(data) % self._frame_size
        self._held = data[whole:]
        return data[:whole]

    def end(self) -> int:
        """Drops the held bytes at the end of the part, and returns their count."""
        dropped = len(self._held)
        self._held = b""
        return dropped
