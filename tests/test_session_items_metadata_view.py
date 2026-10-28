"""The app's items metadata endpoint leaves out DEBUG_ONLY items."""

from __future__ import annotations

import asyncio

import orjson
import pytest
from django.test import AsyncClient

from twicc.core.enums import ItemDisplayLevel
from twicc.core.models import Project, Session, SessionItem

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


def test_metadata_leaves_out_debug_only_items(client):
    project = Project.objects.create(id="meta-project", directory="/meta")
    session = Session.objects.create(id="meta-session", project=project, provider="codex")
    levels = [ItemDisplayLevel.ALWAYS, ItemDisplayLevel.DEBUG_ONLY, ItemDisplayLevel.COLLAPSIBLE,
              ItemDisplayLevel.DEBUG_ONLY, None]
    SessionItem.objects.bulk_create([
        SessionItem(session=session, line_num=line, content="", display_level=level)
        for line, level in enumerate(levels, start=1)
    ])

    response = asyncio.run(client.get(f"/api/projects/{project.id}/sessions/{session.id}/items/metadata/"))

    assert response.status_code == 200
    # Uncomputed lines (NULL level) stay: only a known DEBUG_ONLY line is left out.
    assert [row["line_num"] for row in orjson.loads(response.content)] == [1, 3, 5]
