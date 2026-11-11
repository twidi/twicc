"""Explicit legacy acceptance adapter; new gate tests never install it."""

import pytest
from django.utils import timezone


@pytest.fixture
def accept_legacy_message_decisions(monkeypatch):
    from twicc.agent.semantic_types import Classification
    from twicc.core.models import TimelineDecision
    from twicc.core.services import timeline_decisions, timeline_messages

    capture = timeline_messages._capture

    def capture_and_accept(*args, **kwargs):
        result = capture(*args, **kwargs)
        for row in TimelineDecision.objects.filter(kind='message', status='pending'):
            timeline_decisions.resolve_decision(decision_id=str(row.pk), classification=Classification('YES', 'legacy source regression'),
                effective_route='provider', now=timezone.now())
            timeline_decisions.publish_message_if_ready(decision_id=str(row.pk))
        return result

    monkeypatch.setattr(timeline_messages, '_capture', capture_and_accept)
