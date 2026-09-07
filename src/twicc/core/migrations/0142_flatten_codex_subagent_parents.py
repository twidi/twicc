"""Anchor Codex descendants to their root and repair stored aggregates."""

from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def flatten(apps, schema_editor):
    Session = apps.get_model("core", "Session")
    SessionItem = apps.get_model("core", "SessionItem")
    Project = apps.get_model("core", "Project")
    alias = schema_editor.connection.alias
    sessions = Session.objects.using(alias)
    ancestry = {row.id: row for row in sessions.all().only("id", "parent_session_id", "type", "provider")}
    moves = []
    affected = set()
    for sub in ancestry.values():
        if sub.provider != "codex" or sub.type != "subagent" or not sub.parent_session_id:
            continue
        current = sub.parent_session_id
        seen = {sub.id}
        while current not in seen:
            seen.add(current)
            row = ancestry.get(current)
            if row is None:
                break
            if row.parent_session_id is None:
                if row.type == "session" and current != sub.parent_session_id:
                    moves.append((sub.id, current))
                    affected.update((sub.parent_session_id, current))
                break
            current = row.parent_session_id

    for sub_id, root_id in moves:
        sessions.filter(id=sub_id).update(parent_session_id=root_id)

    project_ids = set()
    for session in sessions.filter(id__in=affected):
        session.self_cost = SessionItem.objects.using(alias).filter(session_id=session.id).aggregate(
            total=Sum("cost"),
        )["total"]
        session.subagents_cost = SessionItem.objects.using(alias).filter(
            session__parent_session_id=session.id,
        ).aggregate(total=Sum("cost"))["total"]
        total = (session.self_cost or Decimal(0)) + (session.subagents_cost or Decimal(0))
        session.total_cost = total if total > 0 else None
        session.save(using=alias, update_fields=["self_cost", "subagents_cost", "total_cost"])
        if session.project_id:
            project_ids.add(session.project_id)

    # Match Project.recalculate_total_cost's actual filters and null semantics.
    for project_id in project_ids:
        total = sessions.filter(project_id=project_id, type="session").aggregate(total=Sum("total_cost"))["total"]
        Project.objects.using(alias).filter(id=project_id).update(total_cost=total if total and total > 0 else None)


class Migration(migrations.Migration):
    dependencies = [("core", "0141_mcp_oauth_source_hash")]
    operations = [migrations.RunPython(flatten, migrations.RunPython.noop)]
