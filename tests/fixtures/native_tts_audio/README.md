# Native TTS audio fixtures

These fixtures contain generated tones. They contain no provider audio or user data.

- `three-tones.pcm`: signed PCM-16, little-endian, 16,000 Hz, mono, 24,000 sample frames (48,000 bytes).
- `three-tones.wav`: the same samples, with a standard 44-byte PCM WAV header.
- `wav-origin.pcm`: output from the existing `script_audio.OutputCheck("wav", None, 1)`, fed in 797-byte chunks.
- `three-tones.mp3`: the tones encoded with FFmpeg `libmp3lame` at 64 kbit/s, then passed through existing `Mp3HeadFilter(drop_id3=False)`.

The PCM contains three 500 ms tones: 330, 550, and 770 Hz. Each sample is `round(6000*sin(2*pi*frequency*i/16000))`.
Each tone starts at sample index zero. This permits exact byte comparisons for missing fragments or duplicate prefixes.

The MP3 has 44 MPEG-2 Layer III audio frames. Each frame produces 576 PCM samples: 25,344 decoded samples total.
This count includes codec padding. The existing backend filter removes its Info frame. The leading ID3 tag remains.
No synthetic WAV size or second decoder enters the Android player.

The Android test server sends one owned audio response. It delays each third by 1.7 seconds.
It uses HTTP/1.1 because MockWebServer 4.12.0 does not convert `setChunkedBody` framing for HTTP/2 bodies.
The pass-through AudioProcessor records decoded PCM. Media3 `onAudioPositionAdvancing` separately confirms actual AudioTrack advancement.
Successful drain and AudioTrack logs confirm complete output. This is not acoustic capture or physical-device validation.
