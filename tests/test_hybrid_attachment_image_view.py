"""Hybrid image thumbnails: the CLI's own ``attachment`` record of an ``@``-referenced file, served as an image.

The CLI writes, right after the user line, one ``attachment`` record per
referenced file (base64 inside). Those records are DEBUG_ONLY, so the client
never loads their content; the history thumbnail asks this endpoint instead.
"""

from __future__ import annotations

import asyncio
import base64

import orjson
import pytest
from django.test import AsyncClient

from twicc.core.models import Project, Session, SessionItem

SID = "6f1d2c3b-4a59-4e8f-9a0b-1c2d3e4f5a6b"
REFERENCE = "att_3bd7fe5bf701.png"
OTHER_REFERENCE = "att_1e508b01c568.png"
PNG_BYTES = b"\x89PNG\r\n\x1a\n-fake-image-bytes"


def attachment_record(reference: str, data: bytes = PNG_BYTES, media_type: str = "image/png", *, ftype="image") -> str:
    return orjson.dumps({
        "type": "attachment",
        "attachment": {
            "type": "file",
            "filename": f"/data/hybrid/{SID}/{reference}",
            "content": {"type": ftype, "file": {"base64": base64.b64encode(data).decode(), "type": media_type}},
        },
    }).decode()


@pytest.fixture
def session(db, tmp_path, settings):
    settings.TWICC_PASSWORD_HASH = ""
    project = Project.objects.create(id="-tmp-hybrid-thumb", directory=str(tmp_path))
    return Session.objects.create(id=SID, project=project)


def put(session, line_num, content):
    SessionItem.objects.create(session=session, line_num=line_num, content=content)


def get(session, line_num, reference):
    url = f"/api/projects/{session.project_id}/sessions/{session.id}/items/{line_num}/attachments/{reference}"
    return asyncio.run(AsyncClient().get(url))


@pytest.mark.django_db(transaction=True)
def test_serves_the_bytes_of_the_matching_record_after_the_user_line(session):
    put(session, 1, orjson.dumps({"type": "user"}).decode())
    put(session, 2, attachment_record(OTHER_REFERENCE, b"other"))
    put(session, 3, attachment_record(REFERENCE))

    response = get(session, 1, REFERENCE)

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response.content == PNG_BYTES
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "max-age" in response["Cache-Control"]


@pytest.mark.django_db(transaction=True)
def test_ignores_a_record_before_the_user_line(session):
    put(session, 1, attachment_record(REFERENCE))
    put(session, 2, orjson.dumps({"type": "user"}).decode())

    assert get(session, 2, REFERENCE).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_unknown_reference_is_404(session):
    put(session, 1, orjson.dumps({"type": "user"}).decode())
    put(session, 2, attachment_record(OTHER_REFERENCE))

    assert get(session, 1, REFERENCE).status_code == 404


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("reference", ["att_zz.png", "..%2Fatt_3bd7fe5bf701.png", "att_3bd7fe5bf701.png.exe/x", "notes.txt"])
def test_malformed_reference_is_404(session, reference):
    put(session, 1, attachment_record(REFERENCE))

    assert get(session, 0, reference).status_code == 404


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("media_type", ["image/svg+xml", "text/html", "application/pdf"])
def test_only_raster_image_types_are_served(session, media_type):
    put(session, 1, orjson.dumps({"type": "user"}).decode())
    put(session, 2, attachment_record(REFERENCE, b"<svg/>", media_type))

    assert get(session, 1, REFERENCE).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_invalid_base64_or_non_image_record_is_404(session):
    put(session, 1, orjson.dumps({"type": "user"}).decode())
    put(session, 2, attachment_record(REFERENCE, ftype="text"))

    assert get(session, 1, REFERENCE).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_unknown_session_is_404(session):
    url = f"/api/projects/{session.project_id}/sessions/nope/items/1/attachments/{REFERENCE}"
    assert asyncio.run(AsyncClient().get(url)).status_code == 404
