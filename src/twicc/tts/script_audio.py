"""The checks of the audio a script writes, against the format of its config.

A script config declares MP3, WAV or raw PCM. :class:`OutputCheck` reads the output chunk by
chunk, holds back its first bytes until the format is decided, and fails the run with a message
that names the format when the output does not match. MP3 and PCM pass through unchanged. A WAV
loses its header: its samples play as ``audio/pcm`` with the header's rate and channels. No
sample is converted.
"""

import struct

from twicc.tts.openrouter import MAX_PCM_CHANNELS, MAX_PCM_RATE, SpeechError
from twicc.tts.part_audio import MP3_HEADER_SIZE, id3_tag_size, mp3_frame

# A WAV whose ``data`` chunk does not start within these first bytes is refused.
WAV_HEADER_MAX_BYTES = 64 * 1024
# Raw PCM has no signature: its first bytes are only checked against the known containers.
SNIFF_BYTES = 12

NOT_MP3 = "The script output is not MP3"
NOT_WAV = "The script output is not WAV"
WAV_NOT_16BIT = "The script WAV is not 16-bit PCM"
WAV_BAD_FORMAT = "The script WAV has an unsupported sample rate or channel count"
NO_AUDIO = "The script gave no audio"
LOOKS_LIKE_MP3 = "The script output looks like MP3: choose MP3"
LOOKS_LIKE_WAV = "The script output looks like WAV: choose WAV"
LOOKS_LIKE_OGG = "The script output looks like Ogg audio, which TwiCC does not play"
LOOKS_LIKE_FLAC = "The script output looks like FLAC audio, which TwiCC does not play"
LOOKS_LIKE_MP4 = "The script output looks like MP4 audio, which TwiCC does not play"

_WAV_FORMAT_PCM = 1
_WAV_FORMAT_EXTENSIBLE = 0xFFFE
_WAV_FMT_SIZE = 16  # format, channels, rate, byte rate, block align, bits
_WAV_SUB_FORMAT_OFFSET = 24  # the sub-format GUID of an extensible ``fmt ``
_WAV_SUB_FORMAT_PCM = b"\x01\x00"  # the first two bytes of KSDATAFORMAT_SUBTYPE_PCM
# A ``data`` size of 0 or 0xFFFFFFFF: a WAV written as it goes, every later byte is a sample.
_WAV_UNKNOWN_SIZES = (0, 0xFFFFFFFF)


def _pcm_type(rate: int, channels: int) -> str:
    return f"audio/pcm;rate={rate};channels={channels}"


class _Mp3Check:
    """MP3: an ID3 tag or a frame header first. A leading ID3 tag is held back until a byte
    after it arrives (a tag alone is not audio); then every byte passes, the tag included."""

    _SNIFF = "sniff"  # waiting for the first bytes
    _TAG = "tag"  # inside the leading ID3 tag
    _PASS = "pass"

    def __init__(self):
        self.content_type: str | None = None
        self._state = self._SNIFF
        self._held = bytearray()

    def feed(self, chunk: bytes) -> bytes:
        if self._state == self._PASS:
            return chunk
        self._held += chunk
        held = self._held
        if self._state == self._SNIFF:
            if held[:3] == b"ID3":
                self._state = self._TAG
                self.content_type = "audio/mpeg"
            elif len(held) < MP3_HEADER_SIZE:
                return b""
            elif mp3_frame(held) is None:
                raise SpeechError(NOT_MP3)
            else:
                self.content_type = "audio/mpeg"
                return self._pass_held()
        size = id3_tag_size(held)
        if size is None or len(held) <= size:
            return b""  # the tag header or the tag is not whole yet, or nothing follows it
        return self._pass_held()

    def end(self) -> bytes:
        if self._state == self._SNIFF and self._held:
            raise SpeechError(NOT_MP3)
        return b""  # a held ID3 tag alone (or its cut header) is not audio

    def _pass_held(self) -> bytes:
        held = bytes(self._held)
        self._held.clear()
        self._state = self._PASS
        return held


class _WavCheck:
    """WAV: a RIFF/WAVE header, then chunks until ``fmt `` and ``data``. Gives the samples
    only: the header is dropped, and so is what follows a ``data`` chunk of known size."""

    def __init__(self):
        self.content_type: str | None = None
        self._held = bytearray()
        self._in_data = False
        self._remaining: int | None = None  # sample bytes still to give; None: every byte

    def feed(self, chunk: bytes) -> bytes:
        if self._in_data:
            return self._samples(chunk)
        self._held += chunk
        start = self._read_header()
        if start is None:
            return b""
        samples = bytes(self._held[start:])
        self._held.clear()
        self._in_data = True
        return self._samples(samples)

    def end(self) -> bytes:
        if not self._in_data and self._held:
            raise SpeechError(NOT_WAV)
        return b""

    def _samples(self, data: bytes) -> bytes:
        if self._remaining is None:
            return data
        given = data[: self._remaining]
        self._remaining -= len(given)
        return given

    def _read_header(self) -> int | None:
        """The offset of the first sample in the held bytes, or None while the header is not
        whole. Re-read from the start at each chunk: it skips chunk payloads by their size."""
        held = self._held
        if held[:4] != b"RIFF"[: len(held)] or held[8:12] != b"WAVE"[: max(len(held) - 8, 0)]:
            raise SpeechError(NOT_WAV)
        position = 12
        wav_format = None
        while True:
            if len(held) < position + 8:
                self._check_header_room(position + 8)
                return None
            chunk_id = bytes(held[position : position + 4])
            size = int.from_bytes(held[position + 4 : position + 8], "little")
            payload = position + 8
            if chunk_id == b"data":
                if wav_format is None:
                    raise SpeechError(NOT_WAV)  # no rate nor channels for the samples
                self._remaining = None if size in _WAV_UNKNOWN_SIZES else size
                self.content_type = _pcm_type(*wav_format)
                return payload
            if chunk_id == b"fmt ":
                if len(held) < payload + size:
                    self._check_header_room(payload + size)
                    return None
                wav_format = self._read_fmt(bytes(held[payload : payload + size]))
            position = payload + size + (size & 1)  # a pad byte after an odd size

    @staticmethod
    def _check_header_room(length: int) -> None:
        """Refuses a header that needs more than its first bytes to be read."""
        if length > WAV_HEADER_MAX_BYTES:
            raise SpeechError(NOT_WAV)

    @staticmethod
    def _read_fmt(payload: bytes) -> tuple[int, int]:
        """``(rate, channels)`` of a 16-bit PCM ``fmt `` chunk payload."""
        if len(payload) < _WAV_FMT_SIZE:
            raise SpeechError(NOT_WAV)
        format_tag, channels, rate, _byte_rate, _block_align, bits = struct.unpack_from("<HHIIHH", payload)
        if format_tag == _WAV_FORMAT_EXTENSIBLE:
            sub_format = payload[_WAV_SUB_FORMAT_OFFSET : _WAV_SUB_FORMAT_OFFSET + 2]
            if len(sub_format) < 2:
                raise SpeechError(NOT_WAV)
            pcm = sub_format == _WAV_SUB_FORMAT_PCM
        else:
            pcm = format_tag == _WAV_FORMAT_PCM
        if not pcm or bits != 16:
            raise SpeechError(WAV_NOT_16BIT)
        if not (0 < rate <= MAX_PCM_RATE and 0 < channels <= MAX_PCM_CHANNELS):
            raise SpeechError(WAV_BAD_FORMAT)
        return rate, channels


def _container_lookalike(head: bytes) -> str | None:
    """The message for a PCM output whose first bytes look like a known container, else None."""
    if head[:3] == b"ID3" or mp3_frame(head) is not None:
        return LOOKS_LIKE_MP3
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return LOOKS_LIKE_WAV
    if head[:4] == b"OggS":
        return LOOKS_LIKE_OGG
    if head[:4] == b"fLaC":
        return LOOKS_LIKE_FLAC
    if head[4:8] == b"ftyp":
        return LOOKS_LIKE_MP4
    return None


class _PcmCheck:
    """Raw PCM: anything that does not start like a known container, decided at
    :data:`SNIFF_BYTES` or at the end."""

    def __init__(self, content_type: str):
        self.content_type: str | None = None
        self._config_type = content_type
        self._held = bytearray()
        self._decided = False

    def feed(self, chunk: bytes) -> bytes:
        if self._decided:
            return chunk
        self._held += chunk
        if len(self._held) < SNIFF_BYTES:
            return b""
        return self._decide()

    def end(self) -> bytes:
        if self._decided or not self._held:
            return b""
        return self._decide()

    def _decide(self) -> bytes:
        held = bytes(self._held)
        message = _container_lookalike(held)
        if message is not None:
            raise SpeechError(message)
        self._held.clear()
        self._decided = True
        self.content_type = self._config_type
        return held


class OutputCheck:
    """Checks the output of one script run against ``audio_format`` (``"mp3"``, ``"wav"`` or
    ``"pcm"``; a PCM config needs ``pcm_rate``). Feed it every chunk, then call :meth:`end`."""

    def __init__(self, audio_format: str, pcm_rate: int | None, pcm_channels: int):
        if audio_format == "mp3":
            self._check = _Mp3Check()
        elif audio_format == "wav":
            self._check = _WavCheck()
        elif audio_format == "pcm":
            if pcm_rate is None:
                raise ValueError("a PCM output check needs a rate")
            self._check = _PcmCheck(_pcm_type(pcm_rate, pcm_channels))
        else:
            raise ValueError(f"unknown audio format: {audio_format!r}")
        self._gave_audio = False

    @property
    def content_type(self) -> str | None:
        """The audio type, once the format is decided."""
        return self._check.content_type

    def feed(self, chunk: bytes) -> bytes:
        """The audio bytes to append now. Raises :class:`SpeechError` on a mismatch."""
        audio = self._check.feed(chunk)
        self._gave_audio = self._gave_audio or bool(audio)
        return audio

    def end(self) -> bytes:
        """The output ended: the held audio bytes. Raises :class:`SpeechError` on a mismatch
        decided only now, or when no audio byte ever came."""
        audio = self._check.end()
        if not audio and not self._gave_audio:
            raise SpeechError(NO_AUDIO)
        return audio
