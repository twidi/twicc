"""The Android fixture contains the exact PCM samples from the existing WAV script path."""
from tests.native_tts_fixture_server import FIXTURES, normalized_audio


def test_wav_script_fixture_strips_only_the_header():
    content_type, samples = normalized_audio("wav")
    assert content_type == "audio/pcm;rate=16000;channels=1"
    assert samples == (FIXTURES / "three-tones.pcm").read_bytes()
    assert samples == (FIXTURES / "wav-origin.pcm").read_bytes()
    assert len(samples) // 2 == 24000


def test_mp3_and_raw_pcm_keep_the_existing_script_validation():
    assert normalized_audio("mp3") == ("audio/mpeg", (FIXTURES / "three-tones.mp3").read_bytes())
    assert normalized_audio("pcm") == ("audio/pcm;rate=16000;channels=1", (FIXTURES / "three-tones.pcm").read_bytes())
