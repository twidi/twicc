"""The split of a speech text into parts, one OpenRouter request each.

A text that fits in its first part is sent raw, in one request. A longer one is cut at the best
boundary inside each part's size: a paragraph end, else a sentence end, else a clause end, else a
space, else inside a word. The parts grow from ``FIRST_PART_CHARS`` so the audio starts fast,
except for a streaming format, whose audio starts while its part is generated.
"""

import re

from twicc.tts import model_catalog
from twicc.tts.openrouter import SpeechRequest

# The longest part sent in one request (a model's own limit can lower it).
MAX_PART_CHARS = 2000
# The size of the first part when the format does not stream, then each part is PART_GROWTH times
# the size of the one before it, up to the limit.
FIRST_PART_CHARS = 250
PART_GROWTH = 2

# Closing quotes and brackets that stay with the sentence or clause they end.
_CLOSERS = r"[\"'”’»›)\]}」』）】〕〉》]*"

# The cut rules, in order. Each pattern gives its cut position as ``(match start, match end)``:
# a paragraph cuts at its start, the others after their punctuation (the whitespace stays out of
# the match, behind a lookahead), and a space at itself.
_PARAGRAPH = re.compile(r"\n[ \t]*\n")
_SENTENCE = re.compile(rf"[.!?…]{_CLOSERS}(?=\s)|[。！？]{_CLOSERS}")
_CLAUSE = re.compile(rf"[,;:—–]{_CLOSERS}(?=\s)|[、，；：]")
_SPACE = re.compile(r"\s")
_CUT_RULES = ((_PARAGRAPH, True), (_SENTENCE, False), (_CLAUSE, False), (_SPACE, True))

_LEADING_SPACE = re.compile(r"\s*")


def has_speech(text: str) -> bool:
    """True when the text holds a letter or a digit: something a model can read aloud."""
    return any(character.isalnum() for character in text)


def _cut_position(window: str, size: int) -> int:
    """Where to end a part, in a window of ``size + 1`` characters (the extra one lets a lookahead
    see the character after the size): the last match of the first rule that has one above 0 and
    not past ``size``, else ``size``."""
    for pattern, at_start in _CUT_RULES:
        cut = 0
        for match in pattern.finditer(window):
            position = match.start() if at_start else match.end()
            if 0 < position <= size:
                cut = position
        if cut:
            return cut
    return size


def split_text(text: str, limit: int, first: int) -> list[str]:
    """The parts of ``text``: part *k* (from 0) holds at most ``min(limit, first * PART_GROWTH ** k)``
    characters. A text within ``first`` characters is returned unchanged, as one part. A longer
    one gives stripped parts; a part without speech is dropped."""
    if limit < 1 or first < 1:
        raise ValueError("limit and first must be positive")
    if len(text) <= first:
        return [text]
    parts: list[str] = []
    position = _LEADING_SPACE.match(text).end()
    while position < len(text):
        size = min(limit, first * PART_GROWTH ** len(parts))
        if len(text) - position <= size:
            cut = len(text) - position
        else:
            # Only the window is scanned, never the whole rest: the split stays linear.
            cut = _cut_position(text[position : position + size + 1], size)
        part = text[position : position + cut].strip()
        if has_speech(part):
            parts.append(part)
        position = _LEADING_SPACE.match(text, position + cut).end()
    return parts


def speech_parts(request: SpeechRequest) -> list[str]:
    """The parts to send for this request. A model of ``WHOLE_MP3_MODELS`` gets its MP3 text whole.
    Otherwise the parts fit the model's limit (at most ``MAX_PART_CHARS``); they start at that
    limit for a format the model streams, else at ``FIRST_PART_CHARS``."""
    model, response_format = request.model, request.response_format
    if response_format == "mp3" and model in model_catalog.WHOLE_MP3_MODELS:
        return [request.text]
    limit = min(MAX_PART_CHARS, model_catalog.max_chars_of(model))
    facts = model_catalog.KNOWN_SPEECH_MODELS.get(model)
    first = limit if facts is not None and response_format in facts.streams else min(limit, FIRST_PART_CHARS)
    return split_text(request.text, limit, first)
