"""Tests for the split of a speech text into parts (twicc.tts.parts)."""

import time

import pytest

from twicc.tts import model_catalog
from twicc.tts.openrouter import SpeechRequest
from twicc.tts.parts import PartSplitter, has_speech, speech_parts, split_text


def _sentences(length: int) -> str:
    """Short sentences, joined by spaces, up to at least ``length`` characters."""
    sentences = []
    total = 0
    index = 0
    while total < length:
        sentence = f"Sentence number {index} is short."
        sentences.append(sentence)
        total += len(sentence) + 1
        index += 1
    return " ".join(sentences)


def _prose(length: int) -> str:
    """Paragraphs of mixed sentences and clauses, up to at least ``length`` characters."""
    paragraph = (
        "The quick brown fox jumps over the lazy dog, then it rests a while. "
        "Does it sleep? Maybe; nobody knows: the dog — still lazy — keeps watching it! "
        "Later, the fox leaves the field and walks back home through the woods."
    )
    return "\n\n".join([paragraph] * (length // len(paragraph) + 1))


def _request(model: str, response_format: str, text: str) -> SpeechRequest:
    return SpeechRequest(text, model, "alloy", response_format, None, "")


def test_has_speech():
    for text in ("a", "3", "é", "你"):
        assert has_speech(text), text
    for text in ("👍", "…", "| --- |", "```", " \n "):
        assert not has_speech(text), text


def test_a_text_within_the_first_part_is_one_raw_part():
    assert split_text("  Hello.  ", 2000, 2000) == ["  Hello.  "]


def test_split_text_needs_positive_sizes():
    for limit, first in ((0, 250), (2000, 0), (-1, 250), (2000, -1)):
        with pytest.raises(ValueError):
            split_text("Hello there.", limit, first)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("Aaaa. Bbbb.\n\nCccc dddd eeee", ["Aaaa. Bbbb.", "Cccc dddd eeee"], id="paragraph"),
        pytest.param("Aaaa, bbbb. Cccc, dddd eeee ffff", ["Aaaa, bbbb.", "Cccc, dddd eeee ffff"], id="sentence"),
        pytest.param("Aaaa bbbb, cccc dddd eeee", ["Aaaa bbbb,", "cccc dddd eeee"], id="clause"),
        pytest.param("Aaaa bbbb cccc dddd eeee", ["Aaaa bbbb cccc dddd", "eeee"], id="space"),
        pytest.param("A" * 25, ["A" * 20, "A" * 5], id="inside-a-word"),
    ],
)
def test_cut_order(text, expected):
    assert split_text(text, 20, 20) == expected


def test_cjk_sentence_ends_cut_without_spaces():
    assert split_text("你好世界。再见朋友。", 6, 6) == ["你好世界。", "再见朋友。"]


def test_parts_without_speech_are_dropped_anywhere():
    text = "```\n\nHello there.\n\n```\n\n| --- |\n\nBye now."
    assert split_text(text, 15, 15) == ["Hello there.", "Bye now."]


def test_growing_parts():
    text = _sentences(6000)
    parts = split_text(text, 2000, 250)
    assert len(parts) > 3
    for index, part in enumerate(parts[:-1]):
        size = min(2000, 250 * 2**index)
        assert size / 2 < len(part) <= size, (index, len(part))
    assert len(parts[-1]) <= min(2000, 250 * 2 ** (len(parts) - 1))


def test_split_keeps_every_word():
    text = _sentences(6000)
    parts = split_text(text, 2000, 250)
    assert " ".join(parts).split() == text.split()


def test_a_long_text_splits_quickly():
    text = _prose(200_000)
    assert len(text) >= 200_000
    start = time.perf_counter()
    parts = split_text(text, 2000, 250)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, elapsed
    assert " ".join(parts).split() == text.split()


def test_speech_parts_follow_the_model(monkeypatch):
    monkeypatch.setattr(model_catalog, "WHOLE_MP3_MODELS", frozenset())
    text = _sentences(3000)

    parts = speech_parts(_request("sesame/csm-1b", "pcm", text))
    assert len(parts) > 1
    assert all(len(part) <= 200 for part in parts)

    parts = speech_parts(_request("heygen/heygen-voice-1-turbo:free", "pcm", text))
    assert 250 < len(parts[0]) <= 2000

    parts = speech_parts(_request("minimax/speech-2.8-hd", "mp3", text))
    assert len(parts) > 1
    assert all(len(part) <= 2000 for part in parts)

    parts = speech_parts(_request("elevenlabs/eleven-v3", "mp3", text))
    assert len(parts[0]) <= 250

    parts = speech_parts(_request("someone/new-tts", "mp3", text))
    assert len(parts[0]) <= 250
    assert all(len(part) <= 2000 for part in parts[1:])


def test_whole_mp3_models_are_sent_whole(monkeypatch):
    monkeypatch.setattr(model_catalog, "WHOLE_MP3_MODELS", frozenset({"elevenlabs/eleven-v3"}))
    text = _sentences(3000)
    assert speech_parts(_request("elevenlabs/eleven-v3", "mp3", text)) == [text]
    assert len(speech_parts(_request("elevenlabs/eleven-v3", "pcm", text))) > 1


def _fed(text: str, chunk: int, limit: int | None, first: int | None) -> list[str]:
    """The parts a ``PartSplitter`` gives for ``text`` fed by ``chunk`` characters, then ended."""
    splitter = PartSplitter(limit, first)
    parts: list[str] = []
    for start in range(0, len(text), chunk):
        parts.extend(splitter.feed(text[start : start + chunk]))
    return parts + splitter.end()


_SPLITTER_TEXTS = {
    "paragraphs": _prose(3000),
    "sentences": _sentences(3000),
    "one-long-word": "Supercalifragilistic" * 80,
    "no-speech-parts": "```\n\nHello there.\n\n```\n\n| --- |\n\n" + _sentences(400) + "\n\n---\n\n" + _prose(300),
    "spaces-around": "   \n" + _sentences(700) + "  \n\n  ",
    "cjk": "你好世界。再见朋友。" * 60,
}


@pytest.mark.parametrize("chunk", [10_000_000, 1, 7])
@pytest.mark.parametrize(("limit", "first"), [(2000, 250), (100, 20), (40, 40)])
@pytest.mark.parametrize("name", list(_SPLITTER_TEXTS))
def test_the_splitter_gives_the_parts_of_split_text(name, limit, first, chunk):
    text = _SPLITTER_TEXTS[name]
    assert len(text) > first
    expected = split_text(text, limit, first)
    assert len(expected) > 1
    assert _fed(text, chunk, limit, first) == expected


def test_the_splitter_releases_nothing_within_the_first_target():
    splitter = PartSplitter(2000, 20)
    assert splitter.feed("Aaaa bbbb. Cccc dddd") == []
    assert splitter.feed("e") == ["Aaaa bbbb."]
    assert splitter.end() == ["Cccc dddde"]


def test_the_splitter_waits_until_the_cut_point_cannot_move():
    # At 19 characters the best cut is the clause; the 20th character makes it a sentence end, and
    # the 21st, a space, confirms it.
    splitter = PartSplitter(2000, 20)
    assert splitter.feed("Aaaa, bbbb cccc ddd") == []
    assert splitter.feed(".") == []
    assert splitter.feed(" Eeee") == ["Aaaa, bbbb cccc ddd."]
    assert splitter.end() == ["Eeee"]


def test_the_splitter_drops_a_part_without_speech_and_keeps_the_size_order():
    splitter = PartSplitter(10, 5)
    # "-----" is dropped: "Aaaa" is still the first part (5 characters), the next one gets 10.
    assert splitter.feed("----- Aaaa bbbb cccc dddd") == ["Aaaa", "bbbb cccc"]
    assert splitter.end() == ["dddd"]


def test_the_splitter_strips_a_short_text_and_drops_it_without_speech():
    splitter = PartSplitter(2000, 250)
    assert splitter.feed("  Hello.  ") == []
    assert splitter.end() == ["Hello."]
    splitter = PartSplitter(2000, 250)
    assert splitter.feed(" | --- | ") == []
    assert splitter.end() == []
    assert PartSplitter(2000, 250).end() == []


def test_the_splitter_without_a_limit_gives_one_part_at_the_end():
    text = _prose(5000)
    splitter = PartSplitter(None, None)
    for start in range(0, len(text), 7):
        assert splitter.feed(text[start : start + 7]) == []
    assert splitter.end() == [text.strip()]
    splitter = PartSplitter(None, None)
    splitter.feed(" ``` ")
    assert splitter.end() == []


def test_the_splitter_needs_positive_sizes_or_none_for_both():
    for limit, first in ((0, 250), (2000, 0), (-1, 250), (2000, None), (None, 250)):
        with pytest.raises(ValueError):
            PartSplitter(limit, first)


def test_a_long_text_feeds_quickly():
    text = _prose(200_000)
    start = time.perf_counter()
    parts = _fed(text, 3, 2000, 250)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, elapsed
    assert parts == split_text(text, 2000, 250)


@pytest.mark.parametrize(
    ("fed", "tail"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("Aaaa bbbb, cccc dddd", "", id="no-sentence-end"),
        pytest.param("Aaaa bbbb.", "Aaaa bbbb.", id="a-final-period"),
        pytest.param("Aaaa?", "Aaaa?", id="a-final-question-mark"),
        pytest.param('Il dit "oui."', 'Il dit "oui."', id="a-final-period-and-closer"),
        pytest.param("Aaaa bbbb\n\ncccc, dddd", "Aaaa bbbb", id="paragraph-end"),
        pytest.param("  Aaaa. Bbbb! Cccc dd", "Aaaa. Bbbb!", id="sentence-end-then-half-a-sentence"),
        pytest.param("Aaaa.\n\nBbbb? Cccc", "Aaaa.\n\nBbbb?", id="last-of-both"),
        pytest.param("你好世界。再见", "你好世界。", id="cjk"),
    ],
)
def test_the_tail_ends_at_the_last_sentence_or_paragraph_end(fed, tail):
    splitter = PartSplitter(2000, 250)
    assert splitter.feed(fed) == []
    assert splitter.tail() == tail


def test_the_tail_holds_only_what_was_not_released():
    splitter = PartSplitter(2000, 20)
    assert splitter.feed("Aaaa bbbb. Cccc dddd eeee. Ffff gg") == ["Aaaa bbbb."]
    assert splitter.tail() == "Cccc dddd eeee."
