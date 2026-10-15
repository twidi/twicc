"""Bounded native-root queries and active-tree admission use the real database."""

import pytest
from django.db import connection

from twicc.core.models import AgentLink, Project, Session

pytestmark = pytest.mark.django_db


@pytest.fixture
def source():
    project = Project.objects.create(id='root-selection')

    def create(sid, *, mtime=0, version=None, parent=None, session_type='session', provider='claude_code'):
        return Session.objects.create(
            id=sid, project=project, file_path=f'/test/{sid}', provider=provider,
            mtime=mtime, compute_version=version, parent_session_id=parent, type=session_type,
        )
    return create


def test_root_query_selects_one_native_outdated_old_root(source, django_assert_num_queries):
    from twicc.providers.compute_tree import select_outdated_root

    source('old', mtime=10, version=6)
    source('null', mtime=20)
    source('equal', mtime=20, version=0)
    source('current', mtime=25, version=7)
    source('future-version', mtime=26, version=8)
    source('recent', mtime=31)
    source('orphan', mtime=29, session_type='subagent')
    source('child', mtime=28, parent='old')
    source('other-provider', mtime=27, provider='codex')
    with django_assert_num_queries(1):
        selected = select_outdated_root('claude_code', 7, cutoff=30)
    assert selected in {'null', 'equal'}
    with django_assert_num_queries(1):
        assert select_outdated_root('claude_code', 7, cutoff=30, excluded_root_ids=('null', 'equal')) == 'old'
    assert select_outdated_root('claude_code', 7, cutoff=30, excluded_root_ids=('null', 'equal', 'old')) is None


@pytest.mark.parametrize('old_count', [490, 3, 0], ids=['most-old', 'few-old', 'no-old'])
def test_parameterized_orm_query_uses_partial_index_without_sort(source, old_count):
    from twicc.providers.compute_tree import select_outdated_root

    for index in range(500):
        source(f'root-{index}', mtime=index, version=None if index < old_count else 7)
    with connection.cursor() as cursor:
        cursor.execute('ANALYZE core_session')
    statements = []

    def capture(execute, sql, params, many, context):
        statements.append((sql, params))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        select_outdated_root('claude_code', 7, cutoff=450, excluded_root_ids=('root-1', 'root-2'))
    assert len(statements) == 1
    sql, params = statements[0]
    assert sql.endswith('ORDER BY "core_session"."mtime" DESC LIMIT 1')
    assert '"core_session"."parent_session_id" IS NULL' in sql
    assert '"core_session"."compute_version" IS NULL' in sql
    with connection.cursor() as cursor:
        cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
        details = ' '.join(row[3] for row in cursor.fetchall())
    assert 'idx_session_compute_root' in details
    assert 'TEMP B-TREE' not in details


def test_only_recent_root_wakes_at_deadline_without_notification(source, django_assert_num_queries):
    from twicc.providers.compute_tree import next_root_eligibility, select_outdated_root

    source('recent', mtime=100)
    source('later', mtime=110, version=6)
    source('current', mtime=95, version=7)
    source('orphan', mtime=96, session_type='subagent')
    source('child', mtime=97, parent='recent')
    assert select_outdated_root('claude_code', 7, cutoff=99) is None
    with django_assert_num_queries(1) as queries:
        assert next_root_eligibility('claude_code', 7, cutoff=99) == 130
    sql = queries.captured_queries[0]['sql']
    assert sql.startswith('SELECT \"core_session\".\"mtime\" AS \"mtime\"')
    assert sql.endswith('ORDER BY 1 ASC LIMIT 1')
    assert next_root_eligibility('claude_code', 7, cutoff=99, excluded_root_ids=('recent',)) == 140
    assert select_outdated_root('claude_code', 7, cutoff=100) == 'recent'
    assert next_root_eligibility('claude_code', 7, cutoff=110) is None


def test_recent_descendant_reports_deadline_not_completion(source):
    from twicc.providers.compute_selection import TreeSelection, select_tree_source

    source('root')
    source('child', mtime=100, parent='root')
    assert select_tree_source('root', 7, cutoff=99) == TreeSelection(None, True, 130)
    assert select_tree_source('root', 7, cutoff=100) == TreeSelection('child', True, None)
    Session.objects.filter(pk='child').update(compute_version=7)
    assert select_tree_source('root', 7, cutoff=100) == TreeSelection('root', True, None)
    Session.objects.filter(pk='root').update(compute_version=7)
    assert select_tree_source('root', 7, cutoff=100, outstanding=('root',)) == TreeSelection(None, True, None)
    assert select_tree_source('root', 7, cutoff=100) == TreeSelection(None, False, None)


def test_admitted_tree_keeps_members_eligible_and_refreshes_membership(source):
    from twicc.providers.compute_selection import select_tree_source
    from twicc.providers.compute_tree import select_outdated_root

    source('root', mtime=90)
    source('first', mtime=80, parent='root')
    source('second', mtime=70, parent='root')
    root = select_outdated_root('claude_code', 7, cutoff=100)
    assert root == 'root'
    Session.objects.filter(pk='root').update(mtime=110)
    assert select_tree_source(root, 7, cutoff=100).source_id == 'first'
    assert select_tree_source(root, 7, cutoff=100, outstanding=('first',)).source_id == 'second'
    source('deep', mtime=60, parent='root')
    AgentLink.objects.create(session_id='first', agent_id='deep', tool_use_line_num=1, tool_use_id='deep')
    assert select_tree_source(root, 7, cutoff=100, outstanding=('first',)).source_id == 'deep'
    Session.objects.filter(pk__in=('first', 'second', 'deep')).update(compute_version=7)
    assert select_tree_source(root, 7, cutoff=100).next_eligible_at == 140


def test_deepest_level_waits_for_applications_before_parent_and_root(source):
    from twicc.providers.compute_selection import TreeSelection, select_tree_source, tree_completion_context

    source('root')
    source('child', parent='root')
    source('deep', parent='root')
    AgentLink.objects.create(session_id='child', agent_id='deep', tool_use_line_num=1, tool_use_id='deep')
    assert select_tree_source('root', 7, cutoff=100).source_id == 'deep'
    Session.objects.filter(pk='deep').update(compute_version=7)
    assert select_tree_source('root', 7, cutoff=100, outstanding=('deep',)) == TreeSelection(None, True, None)
    assert select_tree_source('root', 7, cutoff=100).source_id == 'child'
    assert tree_completion_context('child', 7) is None
    Session.objects.filter(pk='child').update(compute_version=7)
    assert select_tree_source('root', 7, cutoff=100, outstanding=('child',)).source_id is None
    assert select_tree_source('root', 7, cutoff=100).source_id == 'root'
    assert tree_completion_context('root', 7, outstanding=('child',)) is None
    assert tree_completion_context('root', 7) == {
        'root_id': 'root', 'member_ids': ('root', 'child', 'deep'), 'compute_version': 7,
    }


def test_excluded_member_stays_pending_and_forced_current_sources_obey_final_ack(source):
    from twicc.providers.compute_selection import TreeSelection, select_tree_source

    source('root', version=7)
    source('a', mtime=20, version=7, parent='root')
    source('b', mtime=10, version=7, parent='root')
    forced = ('a', 'b', 'unrelated')
    assert select_tree_source('root', 7, cutoff=100, forced_source_ids=forced).source_id == 'a'
    assert select_tree_source('root', 7, cutoff=100, forced_source_ids=forced,
                              outstanding=('a',)) == TreeSelection(None, True, None)
    assert select_tree_source('root', 7, cutoff=100, forced_source_ids=('b',),
                              excluded_source_ids=('b',)) == TreeSelection(None, True, None)
    assert select_tree_source('root', 7, cutoff=100, forced_source_ids=('b',)).source_id == 'b'


def test_compute_root_migration_creates_only_index_without_global_fk_check(tmp_path, django_db_blocker):
    from django.db.migrations.executor import MigrationExecutor
    from tests.test_sqlite_migration_integration import default_alias_database, trace

    previous = ('core', '0160_statistics_queue_force_cost_sessions')
    target = ('core', '0161_session_compute_root_index')
    with default_alias_database(tmp_path / 'migration.sqlite3', django_db_blocker) as db:
        assert str(db.settings_dict['NAME']).startswith(str(tmp_path))
        MigrationExecutor(db).migrate([previous])
        with trace(db) as statements:
            MigrationExecutor(db).migrate([target])
        indexes = [sql for sql in statements if sql.startswith('CREATE INDEX')]
        assert len(indexes) == 1
        assert '"idx_session_compute_root" ON "core_session"' in indexes[0]
        assert '("provider", "mtime" DESC, "compute_version")' in indexes[0]
        assert '"parent_session_id" IS NULL' in indexes[0] and '"type" = \'session\'' in indexes[0]
        assert not any(sql.startswith('PRAGMA foreign_key_check') for sql in statements)
        assert not any(sql.startswith('CREATE TABLE') for sql in statements)
