"""Server-side cache of generated speech audio (``twicc.tts.audio_cache``)."""

from twicc.tts.audio_cache import AudioCache, CachedAudio
from twicc.tts.openrouter import SpeechRequest


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _key(text: str) -> SpeechRequest:
    return SpeechRequest(text=text, model="m", voice="Kore", response_format="pcm", speed=None, instructions="")


def _audio(content: bytes) -> CachedAudio:
    return CachedAudio(content, "audio/pcm;rate=24000;channels=1")


def test_get_returns_a_stored_audio():
    cache = AudioCache()
    audio = _audio(b"abc")
    assert cache.set(_key("a"), audio) is True
    assert cache.get(_key("a")) == audio
    assert cache.get(_key("missing")) is None
    # Another speech field is another key.
    assert cache.get(_key("a")._replace(voice="Puck")) is None
    assert cache.total_bytes == 3


def test_entries_expire_after_the_ttl():
    clock = FakeClock()
    cache = AudioCache(now=clock)
    cache.set(_key("a"), _audio(b"x"))
    clock.t = 599.0
    assert cache.get(_key("a")) is not None
    clock.t = 600.0
    assert cache.get(_key("a")) is None
    assert cache.total_bytes == 0


def test_reading_refreshes_recency_not_lifetime():
    clock = FakeClock()
    cache = AudioCache(max_bytes=8, ttl=1000.0, now=clock)
    cache.set(_key("a"), _audio(b"1111"))
    cache.set(_key("b"), _audio(b"2222"))
    clock.t = 900.0
    assert cache.get(_key("a")) is not None
    # "a" is now the most recently used: "c" evicts "b".
    cache.set(_key("c"), _audio(b"3333"))
    assert cache.get(_key("b")) is None
    clock.t = 1000.0
    # The read did not extend the lifetime of "a"; "c", stored at 900, stays.
    assert cache.get(_key("a")) is None
    assert cache.get(_key("c")) is not None
    assert cache.total_bytes == 4


def test_total_size_cap_evicts_least_recently_used():
    cache = AudioCache(max_bytes=10)
    cache.set(_key("first"), _audio(b"1111"))
    cache.set(_key("second"), _audio(b"2222"))
    cache.set(_key("third"), _audio(b"3333"))
    assert cache.get(_key("first")) is None
    assert cache.get(_key("second")) is not None
    assert cache.get(_key("third")) is not None
    assert cache.total_bytes == 8


def test_an_audio_larger_than_the_cap_is_not_stored():
    cache = AudioCache(max_bytes=3)
    assert cache.set(_key("big"), _audio(b"1234")) is False
    assert cache.get(_key("big")) is None
    assert cache.total_bytes == 0
    # Exactly at the cap fits.
    assert cache.set(_key("fits"), _audio(b"123")) is True


def test_setting_a_key_again_replaces_it_and_clear_empties_the_cache():
    cache = AudioCache(max_bytes=10)
    cache.set(_key("a"), _audio(b"1111"))
    replacement = _audio(b"22")
    cache.set(_key("a"), replacement)
    assert cache.get(_key("a")) == replacement
    assert cache.total_bytes == 2
    cache.clear()
    assert cache.get(_key("a")) is None
    assert cache.total_bytes == 0
