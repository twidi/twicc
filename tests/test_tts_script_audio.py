"""Tests for the checks of the audio a script writes (twicc.tts.script_audio)."""

import struct

import pytest

from twicc.tts.openrouter import SpeechError
from twicc.tts.script_audio import NO_AUDIO, NOT_WAV, WAV_BAD_FORMAT, WAV_HEADER_MAX_BYTES, WAV_NOT_16BIT, OutputCheck

_UNKNOWN_SIZE = 0xFFFFFFFF
# The KSDATAFORMAT_SUBTYPE_PCM GUID of a WAVE_FORMAT_EXTENSIBLE ``fmt `` chunk.
_PCM_GUID = bytes.fromhex("0100000000001000800000aa00389b71")
_FLOAT_GUID = bytes.fromhex("0300000000001000800000aa00389b71")


def _frame(bitrate_index: int = 9) -> bytes:
    """One MPEG-1 Layer III mono frame at 44.1 kHz (index 9: header ``FF FB 90 C4``, 417 bytes)."""
    frame = bytearray({9: 417, 5: 208}[bitrate_index])
    frame[:4] = bytes((0xFF, 0xFB, bitrate_index << 4, 0xC4))
    frame[4:] = b"\x55" * (len(frame) - 4)
    return bytes(frame)


MP3_FRAME = _frame()


def _id3(size: int) -> bytes:
    """An ID3v2.4 tag of ``size`` payload bytes (10 + ``size`` bytes in all)."""
    syncsafe = bytes(((size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F))
    return b"ID3\x04\x00\x00" + syncsafe + b"\x01" * size


def _chunk(chunk_id: bytes, payload: bytes, size: int | None = None) -> bytes:
    pad = b"\x00" if len(payload) % 2 else b""
    return chunk_id + struct.pack("<I", len(payload) if size is None else size) + payload + pad


def _wav(
    samples: bytes,
    *,
    rate=8000,
    channels=1,
    bits=16,
    fmt=1,
    sub_format=_PCM_GUID,
    data_size=None,
    extra_chunk=b"",
) -> bytes:
    """A WAV of ``samples``, with ``extra_chunk`` between ``fmt `` and ``data``. The RIFF and data
    sizes are 0xFFFFFFFF when ``data_size`` is None (a WAV written as it goes)."""
    block_align = channels * bits // 8
    fmt_payload = struct.pack("<HHIIHH", fmt, channels, rate, rate * block_align, block_align, bits)
    if fmt == 0xFFFE:
        fmt_payload += struct.pack("<HHI", 22, bits, 0) + sub_format
    body = b"WAVE" + _chunk(b"fmt ", fmt_payload) + extra_chunk
    if data_size is None:
        body += b"data" + struct.pack("<I", _UNKNOWN_SIZE) + samples
        riff_size = _UNKNOWN_SIZE
    else:
        body += _chunk(b"data", samples, data_size)
        riff_size = len(body)
    return b"RIFF" + struct.pack("<I", riff_size) + body


def _fed(check, data: bytes, step: int) -> bytes:
    out = b"".join(check.feed(data[i : i + step]) for i in range(0, len(data), step))
    return out + check.end()


@pytest.mark.parametrize("step", [1, 3, 64])
def test_a_streamed_wav_becomes_pcm(step):
    check = OutputCheck("wav", None, 1)
    wav = _wav(b"\x01\x02" * 50, extra_chunk=b"LIST\x03\x00\x00\x00abc\x00")
    assert _fed(check, wav, step) == b"\x01\x02" * 50
    assert check.content_type == "audio/pcm;rate=8000;channels=1"


def test_the_wav_type_is_set_once_its_header_is_read():
    check = OutputCheck("wav", None, 1)
    wav = _wav(b"\x01\x02" * 10, rate=22050, channels=2)
    assert check.feed(wav[:20]) == b""
    assert check.content_type is None
    assert check.feed(wav[20:]) == b"\x01\x02" * 10
    assert check.content_type == "audio/pcm;rate=22050;channels=2"


def test_an_extensible_pcm_wav_is_accepted():
    check = OutputCheck("wav", None, 1)
    assert _fed(check, _wav(b"\x03\x04" * 8, rate=48000, channels=2, fmt=0xFFFE), 64) == b"\x03\x04" * 8
    assert check.content_type == "audio/pcm;rate=48000;channels=2"


def test_an_extensible_float_wav_is_refused():
    with pytest.raises(SpeechError, match=f"^{WAV_NOT_16BIT}$"):
        _fed(OutputCheck("wav", None, 1), _wav(b"\x00" * 16, fmt=0xFFFE, bits=32, sub_format=_FLOAT_GUID), 64)
    with pytest.raises(SpeechError, match=f"^{WAV_NOT_16BIT}$"):
        _fed(OutputCheck("wav", None, 1), _wav(b"\x00" * 16, fmt=0xFFFE, sub_format=_FLOAT_GUID), 64)


def test_a_24_bit_wav_is_refused():
    with pytest.raises(SpeechError, match=f"^{WAV_NOT_16BIT}$"):
        _fed(OutputCheck("wav", None, 1), _wav(b"\x00" * 30, bits=24), 64)


def test_a_float_wav_is_refused():
    with pytest.raises(SpeechError, match=f"^{WAV_NOT_16BIT}$"):
        _fed(OutputCheck("wav", None, 1), _wav(b"\x00" * 32, bits=32, fmt=3), 64)


@pytest.mark.parametrize("rate, channels", [(0, 1), (400000, 1), (8000, 0), (8000, 9)])
def test_a_wav_with_a_bad_rate_is_refused(rate, channels):
    with pytest.raises(SpeechError, match=f"^{WAV_BAD_FORMAT}$"):
        _fed(OutputCheck("wav", None, 1), _wav(b"\x00" * 32, rate=rate, channels=channels), 64)


def test_a_wav_with_no_samples_is_no_audio():
    check = OutputCheck("wav", None, 1)
    assert check.feed(_wav(b"")) == b""
    with pytest.raises(SpeechError, match=f"^{NO_AUDIO}$"):
        check.end()


def test_a_known_data_size_drops_a_trailing_chunk():
    samples = bytes(range(100))
    wav = _wav(samples, data_size=100) + _chunk(b"LIST", b"INFOabc") + _chunk(b"id3 ", b"\x00" * 9)
    for step in (1, 7, 64, len(wav)):
        assert _fed(OutputCheck("wav", None, 1), wav, step) == samples


def test_a_zero_data_size_is_unknown():
    wav = _wav(b"", data_size=0) + b"\x05\x06" * 20
    assert _fed(OutputCheck("wav", None, 1), wav, 5) == b"\x05\x06" * 20


@pytest.mark.parametrize(
    "data",
    [
        b"Loading model...\n" + _wav(b"\x00" * 8),
        b"RIFF\x00\x00\x00\x00AVI " + bytes(40),
        b"RIF",  # a cut header, decided at the end
        _wav(b"")[:30],
    ],
)
def test_a_non_wav_output_is_refused(data):
    with pytest.raises(SpeechError, match=f"^{NOT_WAV}$"):
        _fed(OutputCheck("wav", None, 1), data, 64)


# RIFF/WAVE (12), ``fmt `` (8 + 16), a ``JUNK`` chunk header (8), then the ``data`` header (8).
_JUNK_FOR_A_FULL_HEADER = WAV_HEADER_MAX_BYTES - 12 - 24 - 8 - 8


@pytest.mark.parametrize("step", [1, 4096, None])
def test_a_wav_whose_data_header_ends_past_the_limit_is_refused(step):
    junk = _chunk(b"JUNK", bytes(_JUNK_FOR_A_FULL_HEADER + 2))
    wav = _wav(b"\x00" * 8, extra_chunk=junk)
    with pytest.raises(SpeechError, match=f"^{NOT_WAV}$"):
        _fed(OutputCheck("wav", None, 1), wav, step or len(wav))


@pytest.mark.parametrize("step", [1, 4096, None])
def test_a_wav_whose_data_header_ends_at_the_limit_is_accepted(step):
    junk = _chunk(b"JUNK", bytes(_JUNK_FOR_A_FULL_HEADER))
    wav = _wav(b"\x07\x08" * 4, extra_chunk=junk)
    assert _fed(OutputCheck("wav", None, 1), wav, step or len(wav)) == b"\x07\x08" * 4


@pytest.mark.parametrize("step", [1, 4096, None])
def test_a_wav_whose_fmt_chunk_ends_past_the_limit_is_refused(step):
    wav = _wav(b"\x00" * 8)
    fmt_at = wav.index(b"fmt ")
    big_fmt = _chunk(b"fmt ", wav[fmt_at + 8 : fmt_at + 24] + bytes(WAV_HEADER_MAX_BYTES))
    wav = wav[:fmt_at] + big_fmt + wav[fmt_at + 24 :]
    with pytest.raises(SpeechError, match=f"^{NOT_WAV}$"):
        _fed(OutputCheck("wav", None, 1), wav, step or len(wav))


@pytest.mark.parametrize("step", [1, 5, None])
def test_a_header_of_many_small_chunks_is_read_across_feeds(step):
    small_chunks = b"".join(_chunk(b"JUNK", bytes(index % 3)) for index in range(500))
    wav = _wav(b"\x09\x0a" * 6, extra_chunk=small_chunks)
    check = OutputCheck("wav", None, 1)
    assert _fed(check, wav, step or len(wav)) == b"\x09\x0a" * 6
    assert check.content_type == "audio/pcm;rate=8000;channels=1"


def test_a_header_past_the_limit_is_refused_from_its_first_bytes():
    # The declared chunk size is enough: nothing near 64 KiB is held before the refusal.
    junk = _chunk(b"JUNK", bytes(_JUNK_FOR_A_FULL_HEADER + 2))
    past_data = _wav(b"\x00" * 8, extra_chunk=junk)
    wav = _wav(b"\x00" * 8)
    fmt_at = wav.index(b"fmt ")
    big_fmt = _chunk(b"fmt ", wav[fmt_at + 8 : fmt_at + 24] + bytes(WAV_HEADER_MAX_BYTES))
    past_fmt = wav[:fmt_at] + big_fmt + wav[fmt_at + 24 :]
    for data in (past_data, past_fmt):
        with pytest.raises(SpeechError, match=f"^{NOT_WAV}$"):
            OutputCheck("wav", None, 1).feed(data[:100])


def test_pcm_needs_a_rate():
    with pytest.raises(ValueError):
        OutputCheck("pcm", None, 1)


def test_an_unknown_format_is_a_programming_error():
    with pytest.raises(ValueError):
        OutputCheck("ogg", None, 1)


def test_text_is_not_mp3():
    with pytest.raises(SpeechError, match="^The script output is not MP3$"):
        _fed(OutputCheck("mp3", None, 1), b"Loading model...\n" + MP3_FRAME, 64)


@pytest.mark.parametrize("data", [b"\xff\xfb", b"I"])
def test_a_short_non_mp3_output_is_refused_at_the_end(data):
    check = OutputCheck("mp3", None, 1)
    assert check.feed(data) == b""
    with pytest.raises(SpeechError, match="^The script output is not MP3$"):
        check.end()


@pytest.mark.parametrize("step", [1, 3, 64])
def test_mp3_with_or_without_id3_passes_through_unchanged(step):
    for data in (MP3_FRAME + _frame(5), _id3(30) + MP3_FRAME + _frame(5)):
        check = OutputCheck("mp3", None, 1)
        assert _fed(check, data, step) == data
        assert check.content_type == "audio/mpeg"


def test_an_id3_tag_is_held_until_a_byte_follows_it():
    check = OutputCheck("mp3", None, 1)
    tag = _id3(30)
    assert check.feed(tag) == b""
    assert check.content_type == "audio/mpeg"
    assert check.feed(MP3_FRAME[:1]) == tag + MP3_FRAME[:1]
    assert check.feed(MP3_FRAME[1:]) == MP3_FRAME[1:]


@pytest.mark.parametrize("step", [1, 64])
def test_an_id3_tag_alone_is_no_audio(step):
    check = OutputCheck("mp3", None, 1)
    tag = _id3(100)
    assert b"".join(check.feed(tag[i : i + step]) for i in range(0, len(tag), step)) == b""
    with pytest.raises(SpeechError, match=f"^{NO_AUDIO}$"):
        check.end()


def test_a_cut_id3_header_is_no_audio():
    check = OutputCheck("mp3", None, 1)
    assert check.feed(b"ID3\x04\x00") == b""
    with pytest.raises(SpeechError, match=f"^{NO_AUDIO}$"):
        check.end()


@pytest.mark.parametrize(
    "head, message",
    [
        (b"ID3\x04\x00\x00\x00\x00\x00\x00", "The script output looks like MP3: choose MP3"),
        (MP3_FRAME[:12], "The script output looks like MP3: choose MP3"),
        (b"RIFF\x00\x00\x00\x00WAVE", "The script output looks like WAV: choose WAV"),
        (b"OggS" + bytes(8), "The script output looks like Ogg audio, which TwiCC does not play"),
        (b"fLaC" + bytes(8), "The script output looks like FLAC audio, which TwiCC does not play"),
        (bytes(4) + b"ftypM4A ", "The script output looks like MP4 audio, which TwiCC does not play"),
    ],
)
def test_pcm_refuses_a_known_container(head, message):
    for step in (1, 64):
        with pytest.raises(SpeechError, match=f"^{message}$"):
            _fed(OutputCheck("pcm", 24000, 1), head + bytes(100), step)


@pytest.mark.parametrize(
    "head, message",
    [
        (b"ID3", "The script output looks like MP3: choose MP3"),
        (MP3_FRAME[:4], "The script output looks like MP3: choose MP3"),
        (b"OggS", "The script output looks like Ogg audio, which TwiCC does not play"),
        (b"fLaC\x00", "The script output looks like FLAC audio, which TwiCC does not play"),
    ],
)
def test_a_short_pcm_lookalike_is_refused_at_the_end(head, message):
    check = OutputCheck("pcm", 24000, 1)
    assert check.feed(head) == b""
    with pytest.raises(SpeechError, match=f"^{message}$"):
        check.end()


def test_pcm_passes_any_other_bytes_with_the_config_type():
    check = OutputCheck("pcm", 22050, 2)
    data = bytes(range(256)) * 4
    assert check.feed(data[:5]) == b""
    assert check.content_type is None
    assert check.feed(data[5:20]) == data[:20]
    assert check.content_type == "audio/pcm;rate=22050;channels=2"
    assert _fed(check, data[20:], 64) == data[20:]


def test_a_short_pcm_output_is_decided_at_the_end():
    check = OutputCheck("pcm", 16000, 1)
    assert check.feed(b"abc") == b""
    assert check.end() == b"abc"
    assert check.content_type == "audio/pcm;rate=16000;channels=1"


@pytest.mark.parametrize("audio_format", ["mp3", "wav", "pcm"])
def test_an_empty_output_is_no_audio_for_every_format(audio_format):
    check = OutputCheck(audio_format, 24000, 1)
    assert check.feed(b"") == b""
    with pytest.raises(SpeechError, match=f"^{NO_AUDIO}$"):
        check.end()
