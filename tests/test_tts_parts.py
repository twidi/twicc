"""Tests for the split of a speech text into parts (twicc.tts.parts)."""

import time

import pytest

from twicc.tts import model_catalog
from twicc.tts.openrouter import SpeechRequest
from twicc.tts.parts import has_speech, speech_parts, split_text


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
