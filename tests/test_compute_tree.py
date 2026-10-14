"""Native membership and launcher depth control source ordering."""

import pytest

from twicc.core.models import AgentLink, Project, Session

pytestmark = pytest.mark.django_db


@pytest.fixture
def source():
    project = Project.objects.create(id='tree-project')

    def create(sid, parent=None, mtime=0, version=None, session_type='session', spawned_by=None):
        return Session.objects.create(
            id=sid, project=project, provider='claude_code', file_path=f'/test/{sid}',
            parent_session_id=parent, mtime=mtime, compute_version=version, type=session_type,
            spawned_by_id=spawned_by,
        )
    return create


def link(parent, child):
    AgentLink.objects.create(session_id=parent, agent_id=child, tool_use_line_num=1, tool_use_id=child)


def test_native_members_order_by_launcher_depth_then_source_mtime(source):
    from twicc.providers.compute_tree import load_native_tree, resolve_native_root
    source('root', mtime=100)
    for sid, mtime in [('a', 20), ('b', 30), ('c', 10), ('unlinked', 40), ('tie', 20)]:
        source(sid, parent='root', mtime=mtime, session_type='subagent')
    link('root', 'a')
    link('a', 'b')
    link('b', 'c')
    link('root', 'tie')
    tree = load_native_tree('root')
    assert resolve_native_root('c') == 'root'
    assert tree.member_ids == ('root', 'a', 'b', 'c', 'tie', 'unlinked')
    assert tree.ordered_source_ids == ('c', 'b', 'unlinked', 'a', 'tie', 'root')


def test_discovery_rereads_native_members_and_launcher_links(source):
    from twicc.providers.compute_tree import load_native_tree
    source('root')
    source('a', parent='root')
    assert load_native_tree('root').ordered_source_ids == ('a', 'root')
    source('b', parent='a')
    link('root', 'a')
    link('a', 'b')
    assert load_native_tree('root').ordered_source_ids == ('b', 'a', 'root')


def test_roots_rank_by_root_mtime_without_independent_subagents(source):
    from twicc.providers.compute_tree import select_outdated_roots
    source('recent', mtime=1000)
    source('old-child', parent='recent', mtime=1, session_type='subagent')
    source('other', mtime=100)
    source('orphan', mtime=2000, session_type='subagent')
    source('spawned', mtime=100, spawned_by='recent')
    source('current', mtime=3000, version=7)
    assert select_outdated_roots('claude_code', 7) == ('recent', 'other', 'spawned')


def test_interrupted_tree_skips_current_sources_and_keeps_root_last(source):
    from twicc.providers.compute_selection import select_required_source_ids
    from twicc.providers.compute_tree import load_native_tree
    source('root')
    source('done', parent='root', version=7)
    source('pending', parent='root')
    tree = load_native_tree('root')
    assert select_required_source_ids(tree, 7) == ('pending', 'root')
    assert select_required_source_ids(tree, 7, forced_source_ids=('done',)) == ('done', 'pending', 'root')


@pytest.mark.parametrize('malformed', ['launcher-cycle', 'conflicting-parent', 'membership-cycle'])
def test_malformed_tree_raises_retryable_failure(source, malformed):
    from twicc.providers.compute_tree import NativeTreeError, load_native_tree, resolve_native_root
    source('root')
    source('a', parent='root')
    source('b', parent='root')
    if malformed == 'membership-cycle':
        Session.objects.filter(id='root').update(parent_session_id='a')
        with pytest.raises(NativeTreeError):
            resolve_native_root('a')
        return
    link('a', 'b')
    link('b' if malformed == 'launcher-cycle' else 'root', 'a' if malformed == 'launcher-cycle' else 'b')
    with pytest.raises(NativeTreeError):
        load_native_tree('root')


def test_independent_spawned_root_ignores_outer_launcher(source):
    from twicc.providers.compute_tree import load_native_tree, resolve_native_root
    source('outer')
    source('spawned', spawned_by='outer')
    link('outer', 'spawned')
    assert resolve_native_root('spawned') == 'spawned'
    assert load_native_tree('spawned').ordered_source_ids == ('spawned',)
