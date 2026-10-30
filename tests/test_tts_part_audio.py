"""Tests for the audio filters of speech parts (twicc.tts.part_audio)."""

from twicc.tts.part_audio import Mp3HeadFilter, PcmFrameTrimmer

# MPEG-1 Layer III, 44.1 kHz, mono, no CRC, no padding: frame length by bitrate index.
_FRAME_LENGTHS = {9: 417, 5: 208}  # 128 kbit/s, 64 kbit/s


def _frame(tag: bytes | None = None, bitrate_index: int = 9, tag_offset: int = 21) -> bytes:
    """One MPEG-1 Layer III mono frame at 44.1 kHz (index 9: header ``FF FB 90 C4``, 417 bytes;
    index 5: 64 kbit/s, 208 bytes), with ``tag`` at ``tag_offset`` (21: the mono side-info end)."""
    frame = bytearray(_FRAME_LENGTHS[bitrate_index])
    frame[:4] = bytes((0xFF, 0xFB, bitrate_index << 4, 0xC4))
    if tag is not None:
        frame[tag_offset : tag_offset + len(tag)] = tag
    return bytes(frame)


def _id3(size: int) -> bytes:
    """An ID3v2.4 tag of ``size`` payload bytes (10 + ``size`` bytes in all)."""
    syncsafe = bytes(((size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F))
    return b"ID3\x04\x00\x00" + syncsafe + b"\x01" * size


def _filtered(data: bytes, *, drop_id3: bool, step: int | None = None) -> tuple[bytes, Mp3HeadFilter]:
    head_filter = Mp3HeadFilter(drop_id3=drop_id3)
    step = step or max(len(data), 1)
    out = b"".join(head_filter.feed(data[i : i + step]) for i in range(0, len(data), step))
    return out + head_filter.flush(), head_filter


def test_audio_frames_pass_untouched():
    frames = _frame() + _frame(bitrate_index=5) + _frame()
    assert _filtered(frames, drop_id3=True)[0] == frames
    assert _filtered(frames, drop_id3=False)[0] == frames


def test_first_part_keeps_its_id3_and_drops_its_info_frame():
    out, _ = _filtered(_id3(30) + _frame(b"Info") + _frame(), drop_id3=False)
    assert out == _id3(30) + _frame()


def test_a_later_part_drops_its_id3_and_its_info_frame():
    out, _ = _filtered(_id3(30) + _frame(b"Xing") + _frame(), drop_id3=True)
    assert out == _frame()


def test_a_vbri_frame_is_dropped_too():
    out, _ = _filtered(_id3(30) + _frame(b"VBRI", tag_offset=36) + _frame(), drop_id3=True)
    assert out == _frame()
    # VBRI only counts at offset 36, Xing and Info only at the side-info offset.
    assert _filtered(_frame(b"VBRI") + _frame(), drop_id3=True)[0] == _frame(b"VBRI") + _frame()
    assert _filtered(_frame(b"Xing", tag_offset=36), drop_id3=True)[0] == _frame(b"Xing", tag_offset=36)


def test_an_id3_footer_is_dropped_with_its_tag():
    tag = bytearray(_id3(30) + b"\x02" * 10)
    tag[5] = 0x10  # footer present: 10 more bytes after the payload
    assert _filtered(bytes(tag) + _frame(), drop_id3=True)[0] == _frame()
    assert _filtered(bytes(tag) + _frame(), drop_id3=False)[0] == bytes(tag) + _frame()


def test_the_head_can_arrive_byte_by_byte():
    for data, drop_id3 in (
        (_id3(30) + _frame(b"Info") + _frame(), False),
        (_id3(30) + _frame(b"Xing") + _frame(), True),
        (_frame() + _frame(), True),
        (b"abcdefgh" * 100, True),
    ):
        whole, _ = _filtered(data, drop_id3=drop_id3)
        assert _filtered(data, drop_id3=drop_id3, step=1)[0] == whole


def test_a_part_shorter_than_its_head_is_flushed():
    half = _frame()[:200]
    head_filter = Mp3HeadFilter(drop_id3=True)
    assert head_filter.feed(half) == b""
    assert head_filter.flush() == half
    assert _filtered(b"", drop_id3=True)[0] == b""
    assert _filtered(b"ID", drop_id3=True)[0] == b"ID"


def test_not_mp3_passes_through():
    data = b"abcdefgh" * 100
    assert _filtered(data, drop_id3=True)[0] == data
    # An ID3 tag followed by something that is not a frame: the tag follows its own rule only.
    assert _filtered(_id3(30) + data, drop_id3=True)[0] == data
    assert _filtered(_id3(30) + data, drop_id3=False)[0] == _id3(30) + data


def test_the_filter_reports_the_info_frame_kind():
    for tag, offset in ((b"Xing", 21), (b"Info", 21), (b"VBRI", 36)):
        head_filter = Mp3HeadFilter(drop_id3=True)
        assert head_filter.info_frame is None
        # Known as soon as feed returns the bytes that follow the dropped frame.
        assert head_filter.feed(_frame(tag, tag_offset=offset) + b"\xff") == b"\xff"
        assert head_filter.info_frame == tag
    assert _filtered(_frame() + _frame(), drop_id3=True)[1].info_frame is None
    assert _filtered(b"abcdefgh" * 100, drop_id3=True)[1].info_frame is None


def test_pcm_trimmer_keeps_whole_frames_and_drops_the_tail():
    trimmer = PcmFrameTrimmer(4)
    assert trimmer.feed(b"abc") == b""
    assert trimmer.feed(b"defgh") == b"abcdefgh"
    assert trimmer.feed(b"ij") == b""
    assert trimmer.end() == 2

    whole = PcmFrameTrimmer(4)
    assert whole.feed(b"abcd") + whole.feed(b"efgh") == b"abcdefgh"
    assert whole.end() == 0
