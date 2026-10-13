"""Daily-derived weeks preserve independent raw-item reference results."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import DailyActivity, Project, Session, SessionItem, WeeklyActivity

pytestmark = pytest.mark.django_db(transaction=True)
MONDAY = date(2026, 10, 12)


@pytest.mark.parametrize('provider', list(Provider))
@pytest.mark.parametrize('scope', ['p', None])
def test_week_matches_independent_item_reference(provider, scope):
    assert hasattr(WeeklyActivity, 'recalculate_from_daily'), 'daily-derived weeks are missing'
    start = datetime.combine(MONDAY, datetime.min.time(), tzinfo=UTC)
    for index, (project, hidden, native, created, timestamp, cost) in enumerate([
        ('p', False, False, start, start, '0.123456'),
        ('p', True, False, start, start + timedelta(days=1), '0.000001'),
        ('p', False, True, start, start + timedelta(days=6, hours=23), '1.999999'),
        ('p', False, False, start - timedelta(seconds=1), start + timedelta(days=3), '3.111111'),
        ('p', False, False, start, start - timedelta(microseconds=1), '40'),
        ('p', False, False, start + timedelta(days=7), start + timedelta(days=7), '50'),
        ('other', False, False, start, start, '6.000001'),
    ]):
        project, _ = Project.objects.get_or_create(id=project)
        session = Session.objects.create(id=f's{index}', file_path=f'/{index}', project=project,
            provider=provider.value, type='subagent' if native else 'session', hidden=hidden,
            created_at=created, user_message_count=1)
        SessionItem.objects.create(session=session, line_num=1, timestamp=timestamp, cost=cost,
                                   kind=ItemKind.USER_MESSAGE)
    other_provider = next(value for value in Provider if value != provider)
    foreign = Session.objects.create(id='foreign', file_path='/foreign', project_id='p',
        provider=other_provider.value, created_at=start, user_message_count=1)
    SessionItem.objects.create(session=foreign, line_num=1, timestamp=start, cost='100', kind=ItemKind.USER_MESSAGE)
    WeeklyActivity.recalculate(scope, MONDAY, provider)
    expected = WeeklyActivity.objects.filter(project_id=scope, provider=provider.value, date=MONDAY).values_list(
        'cost', 'user_message_count', 'session_count').get()
    for offset in range(7):
        DailyActivity.recalculate(scope, MONDAY + timedelta(days=offset), provider)
    WeeklyActivity.objects.all().delete()
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        WeeklyActivity.recalculate_from_daily(scope, MONDAY, provider)
    actual = WeeklyActivity.objects.filter(project_id=scope, provider=provider.value, date=MONDAY).values_list(
        'cost', 'user_message_count', 'session_count').get()
    assert actual == expected
    assert actual == ((Decimal('5.234567'), 2, 2) if scope else (Decimal('11.234568'), 3, 3))
    assert not any('core_sessionitem' in query['sql'] for query in queries)


def test_missing_and_zero_days_delete_old_week_without_global_gate():
    assert hasattr(WeeklyActivity, 'recalculate_from_daily'), 'daily-derived weeks are missing'
    Project.objects.create(id='p')
    WeeklyActivity.objects.create(project_id='p', provider='codex', date=MONDAY, cost=99)
    DailyActivity.objects.create(project_id='p', provider='codex', date=MONDAY, cost=0)
    DailyActivity.objects.create(project_id='p', provider='codex', date=MONDAY + timedelta(days=7), cost=42)
    with transaction.atomic():
        WeeklyActivity.recalculate_from_daily('p', MONDAY, Provider.CODEX)
    assert not WeeklyActivity.objects.exists()


def test_week_preserves_legacy_fractional_storage_before_display_rounding():
    project = Project.objects.create(id='p')
    start = datetime.combine(MONDAY, datetime.min.time(), tzinfo=UTC)
    session = Session.objects.create(id='s', project=project, file_path='/s', provider='codex')
    for line, amount in enumerate(('0.123456789', '0.234567891'), 1):
        SessionItem.objects.create(session=session, line_num=line, timestamp=start + timedelta(days=line), cost=0)
        with connection.cursor() as cursor:
            cursor.execute('UPDATE core_sessionitem SET cost = %s WHERE session_id = %s AND line_num = %s',
                           [amount, session.id, line])
    WeeklyActivity.recalculate('p', MONDAY, Provider.CODEX)
    with connection.cursor() as cursor:
        cursor.execute('SELECT cost FROM core_weeklyactivity WHERE project_id = %s', ['p'])
        expected = cursor.fetchone()[0]
    for offset in range(7):
        DailyActivity.recalculate('p', MONDAY + timedelta(days=offset), Provider.CODEX)
    with transaction.atomic():
        WeeklyActivity.recalculate_from_daily('p', MONDAY, Provider.CODEX)
    with connection.cursor() as cursor:
        cursor.execute('SELECT cost FROM core_weeklyactivity WHERE project_id = %s', ['p'])
        assert cursor.fetchone()[0] == expected == 0.35802468
    assert WeeklyActivity.objects.get(project_id='p').cost == Decimal('0.358025')
