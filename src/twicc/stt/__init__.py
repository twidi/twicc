"""Speech to text: dictation into the composer.

Design: docs/plans/2026-11-18-speech-to-text-design.md. The browser engine
(Web Speech API) runs entirely client-side; this package serves the two server
engines — OpenRouter transcription and a user script — behind
``POST /api/stt/transcribe/``.
"""
