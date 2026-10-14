from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext
from twicc.core.models import DailyActivity, Project, Session, SessionItem

pytestmark = pytest.mark.django_db(transaction=True)


def read(name, *args):
    def run():
        from twicc.providers import statistics_prepare
        try:
            with CaptureQueriesContext(connection) as queries:
                result = getattr(statistics_prepare, name)(*args)
            return result, [q['sql'] for q in queries]
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result()


def root(sid, project='p', provider='claude_code', hidden=False):
    p, _ = Project.objects.get_or_create(id=project)
    return Session.objects.create(id=sid, project=p, provider=provider, file_path=f'/test/{sid}',
                                  created_at=datetime(2026, 10, 11, 23, tzinfo=UTC),
                                  hidden=hidden, user_message_count=1)


def item(session, day, cost=None):
    SessionItem.objects.create(session=session, line_num=day, content='{}', kind='user_message',
                               timestamp=datetime(2026, 10, day, tzinfo=UTC), cost=cost)


def test_days_and_daily_include_unrelated_trees_and_filters():
    from twicc.providers.contribution_revisions import DailyKey
    a, b, hidden = root('a'), root('b'), root('hidden', hidden=True)
    item(a, 11); item(a, 12, Decimal('1.25')); item(b, 12, Decimal('2'))
    item(hidden, 12, Decimal('3')); item(root('other', 'q', 'codex'), 12, Decimal('9'))
    keys, _ = read('prepare_affected_days', ('a', 'a', 'hidden'))
    assert set(keys) == {DailyKey(p, 'claude_code', date(2026, 10, d)) for p in ('p', None) for d in (11, 12)}
    assert len(keys) == 4
    values, queries = read('prepare_daily', DailyKey('p', 'claude_code', date(2026, 10, 12)))
    assert values == (2, 0, Decimal('6.25'))
    assert not any('"content"' in sql for sql in queries)
    assert read('prepare_daily', DailyKey(None, 'claude_code', date(2026, 10, 11)))[0] == (1, 2, Decimal(0))


def test_weekly_reads_daily_only_and_project_roots():
    from twicc.providers.contribution_revisions import DailyKey
    a = root('a'); a.total_cost = Decimal(5); a.save()
    Session.objects.create(id='child', project=a.project, provider=a.provider, file_path='/child',
                           type='subagent', parent_session=a, total_cost=Decimal(3))
    for d, cost in ((11, 99), (12, 2), (18, 3), (19, 99)):
        DailyActivity.objects.create(project=a.project, provider=a.provider, date=date(2026, 10, d),
                                     cost=cost, user_message_count=1, session_count=1)
    values, queries = read('prepare_weekly', DailyKey('p', a.provider, date(2026, 10, 12)))
    assert values == (2, 2, Decimal(5))
    assert not any('core_sessionitem' in sql for sql in queries)
    assert read('prepare_project_cost', 'p')[0] == Decimal(5)


@pytest.mark.parametrize('name,args', [
    ('prepare_affected_days', ((),)),
    ('prepare_daily', (None,)), ('prepare_weekly', (None,)),
    ('prepare_project_cost', ('p',)), ('prepare_workflow_cost', (1,)),
])
def test_preparation_rejects_main_thread(name, args):
    from twicc.providers import statistics_prepare
    with pytest.raises(RuntimeError, match='reader thread'):
        getattr(statistics_prepare, name)(*args)


def test_global_day_spans_projects_and_zero_conventions():
    from twicc.providers.contribution_revisions import DailyKey
    a, b = root('a'), root('b', project='q')
    item(a, 12, Decimal('1')); item(b, 12, Decimal('2'))
    assert read('prepare_daily', DailyKey(None, a.provider, date(2026, 10, 12)))[0] == (2, 0, Decimal(3))
    assert read('prepare_weekly', DailyKey(None, a.provider, date(2026, 10, 12)))[0] == (0, 0, Decimal(0))
    assert read('prepare_project_cost', 'q')[0] is None
