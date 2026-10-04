"""Provider-aware indexed prompt lookup shared by subscription and detection."""


def first_non_command_prompt(session_id: str, provider: str, after_line: int):
    """Return the first genuine prompt after the cursor, or None."""
    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    from twicc.providers.helpers import get_provider_helpers

    helpers = get_provider_helpers(provider)
    rows = SessionItem.objects.filter(
        session_id=session_id, kind=ItemKind.USER_MESSAGE, line_num__gt=after_line,
    ).order_by("line_num")
    for item in rows.iterator():
        if not helpers.is_command_message(item.content):
            return item
    return None
