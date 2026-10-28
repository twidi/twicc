"""Gzip compression of HTTP responses.

Django's ``GZipMiddleware``, restricted to what gains from it:

- Only complete (non-streaming) responses of a text type: JSON, HTML, plain text, SVG, XML. Files
  streamed from disk (attachments, artifacts) keep their ``Content-Length``, and images, PDFs or
  archives are already compressed.
- Only bodies of at least ``GZIP_MIN_LENGTH`` bytes. Django hardcodes 200: below about 1 KB a
  response fits in one TCP packet, so compressing it saves no round trip.

Django's ASGI handler gives each request its own thread for sync middleware code, so compressing a
big response (about 0.1 s for 16 MB) never blocks the event loop nor another request.
"""

from django.middleware.gzip import GZipMiddleware

GZIP_MIN_LENGTH = 1024

_COMPRESSIBLE_TYPES = frozenset({
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
})


def _is_compressible(response):
    media_type = response.get("Content-Type", "").split(";")[0].strip().lower()
    return (
        media_type.startswith("text/")
        or media_type in _COMPRESSIBLE_TYPES
        or media_type.endswith(("+json", "+xml"))
    )


class TwiccGZipMiddleware(GZipMiddleware):
    def process_response(self, request, response):
        if response.streaming or len(response.content) < GZIP_MIN_LENGTH or not _is_compressible(response):
            return response
        return super().process_response(request, response)
