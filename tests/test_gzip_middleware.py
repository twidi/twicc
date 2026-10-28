import gzip

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.test import RequestFactory

from twicc.gzip_middleware import GZIP_MIN_LENGTH, TwiccGZipMiddleware

BIG_TEXT = "x" * (GZIP_MIN_LENGTH * 4)


def _get(response, accept_encoding="gzip, deflate"):
    request = RequestFactory().get("/api/anything/", HTTP_ACCEPT_ENCODING=accept_encoding)
    return TwiccGZipMiddleware(lambda _request: response)(request)


def test_big_json_is_compressed_and_round_trips():
    payload = [{"line_num": number, "kind": "assistant_message"} for number in range(500)]
    response = _get(JsonResponse(payload, safe=False))
    assert response["Content-Encoding"] == "gzip"
    assert response["Content-Length"] == str(len(response.content))
    assert gzip.decompress(response.content) == JsonResponse(payload, safe=False).content


def test_body_below_the_minimum_stays_plain():
    response = _get(JsonResponse({"ok": "x" * (GZIP_MIN_LENGTH // 2)}))
    assert "Content-Encoding" not in response


def test_body_just_above_the_minimum_is_compressed():
    response = _get(HttpResponse("x" * GZIP_MIN_LENGTH, content_type="text/plain"))
    assert response["Content-Encoding"] == "gzip"


def test_client_without_gzip_gets_plain_body():
    response = _get(HttpResponse(BIG_TEXT, content_type="text/html"), accept_encoding="identity")
    assert "Content-Encoding" not in response
    assert response.content == BIG_TEXT.encode()


def test_already_compressed_types_are_left_alone():
    for content_type in ("image/png", "application/pdf", "application/zip", "video/mp4"):
        response = _get(HttpResponse(BIG_TEXT, content_type=content_type))
        assert "Content-Encoding" not in response, content_type


def test_text_like_types_are_compressed():
    for content_type in ("text/html; charset=utf-8", "application/json", "image/svg+xml",
                         "application/problem+json", "application/javascript"):
        response = _get(HttpResponse(BIG_TEXT, content_type=content_type))
        assert response["Content-Encoding"] == "gzip", content_type


def test_streamed_files_keep_their_content_length():
    response = StreamingHttpResponse(iter([BIG_TEXT.encode()]), content_type="text/plain")
    response["Content-Length"] = str(len(BIG_TEXT))
    result = _get(response)
    assert "Content-Encoding" not in result
    assert result["Content-Length"] == str(len(BIG_TEXT))
