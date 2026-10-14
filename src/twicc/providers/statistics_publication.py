"""Publish prepared replacements through bounded background writer transactions."""
import asyncio
from decimal import Decimal

from django.db import connection, transaction

from twicc.core.models import DailyActivity, Project, Session, WeeklyActivity, Workflow
from twicc.providers.contribution_revisions import DailyKey, observations_match
from twicc.providers.statistics_prepare import ActivityValues, PreparedTreeCosts

SESSION_PUBLICATION_LIMIT = 256


class BackgroundInvalidated(RuntimeError):
    """The consumer must retain its batch and prepare it again."""


async def _publish(function, /, *args):
    from twicc.providers.db_writer import publish_statistics_sync

    return await publish_statistics_sync(function, *args)


def _validate(prepared, costs, own):
    if not observations_match(update.invalidation_observation for update in prepared.own_updates):
        raise BackgroundInvalidated('Background own cost changed during preparation')
    identities = {row.session_id: row.storage_incarnation for row in costs}
    identities.update((source.session_id, source.incarnation) for update in own
                      for source, _ in update.invalidation_observation.own_costs)
    current = dict(Session.objects.filter(pk__in=identities).values_list('id', 'storage_incarnation'))
    if current != identities:
        raise BackgroundInvalidated('Statistics source identity changed during preparation')



def _session_slice(prepared, costs, own):
    with transaction.atomic():
        _validate(prepared, costs, own)
        quote = connection.ops.quote_name
        table = quote(Session._meta.db_table)
        def parameters(fields, rows):
            return [[Session._meta.get_field(field).get_db_prep_save(value, connection)
                     for field, value in zip(fields, row)] for row in rows]
        with connection.cursor() as cursor:
            if costs:
                fields = ('self_cost', 'subagents_cost', 'total_cost', 'id')
                cursor.executemany(
                    f'UPDATE {table} SET self_cost=%s, subagents_cost=%s, total_cost=%s WHERE id=%s',
                    parameters(fields, [(row.self_cost, row.subagents_cost, row.total_cost, row.session_id)
                                        for row in costs]),
                )
            if own:
                fields = ('self_cost', 'items_cost_computed_through_at', 'id')
                cursor.executemany(
                    f'UPDATE {table} SET self_cost=%s, items_cost_computed_through_at=%s WHERE id=%s',
                    parameters(fields, [(row.self_cost, row.observed_at, row.session_id) for row in own]),
                )


async def publish_tree_costs(prepared: PreparedTreeCosts) -> None:
    own = {row.session_id: row for row in prepared.own_updates}
    costs = {row.session_id: row for row in prepared.session_costs}
    ids = tuple(dict.fromkeys((*costs, *own)))
    for start in range(0, len(ids), SESSION_PUBLICATION_LIMIT):
        batch = ids[start:start + SESSION_PUBLICATION_LIMIT]
        await _publish(_session_slice, prepared, tuple(costs[sid] for sid in batch if sid in costs),
                       tuple(own[sid] for sid in batch if sid in own))
        await asyncio.sleep(0)


def _activity(model, key, values):
    with transaction.atomic():
        model.objects.update_or_create(project_id=key.project_id, provider=key.provider, date=key.day,
            defaults=values._asdict())


async def publish_daily(key: DailyKey, values: ActivityValues) -> None:
    await _publish(_activity, DailyActivity, key, values)
    await asyncio.sleep(0)


async def publish_weekly(key: DailyKey, values: ActivityValues) -> None:
    if key.day.weekday() != 0:
        raise ValueError('Weekly activity date must be Monday')
    await _publish(_activity, WeeklyActivity, key, values)
    await asyncio.sleep(0)


def _project(project_id, value):
    with transaction.atomic():
        Project.objects.filter(pk=project_id).update(total_cost=value)


async def publish_project_cost(project_id: str, value: Decimal | None) -> None:
    await _publish(_project, project_id, value)
    await asyncio.sleep(0)


def _workflow(workflow_id, cost, phases_cost):
    with transaction.atomic():
        Workflow.objects.filter(pk=workflow_id).update(cost=cost, phases_cost=phases_cost)


async def publish_workflow_cost(workflow_id: int, cost: Decimal, phases_cost: dict[str, float]) -> None:
    await _publish(_workflow, workflow_id, cost, phases_cost)
    await asyncio.sleep(0)


async def notify_statistics_published(
    root_ids: tuple[str, ...], project_ids: tuple[str, ...], workflow_owner_ids: tuple[str, ...],
) -> None:
    """Notify clients after derived publication, before queue acknowledgement."""
    from channels.layers import get_channel_layer
    from twicc.providers.db_writer import broadcast_session_updated, _broadcast_project_updated

    for root_id in dict.fromkeys(root_ids):
        await broadcast_session_updated(root_id)
    for project_id in dict.fromkeys(project_ids):
        await _broadcast_project_updated(project_id)
    layer = get_channel_layer()
    for owner_id in dict.fromkeys(workflow_owner_ids):
        await layer.group_send('updates', {'type': 'broadcast', 'data': {
            'type': 'workflow_changed', 'session_id': owner_id,
        }})
    await layer.group_send('updates', {'type': 'broadcast', 'data': {
        'type': 'statistics_updated', 'root_ids': list(dict.fromkeys(root_ids)),
        'project_ids': list(dict.fromkeys(project_ids)),
    }})
    await layer.group_send('updates', {'type': 'broadcast', 'data': {'type': 'activity_updated'}})
