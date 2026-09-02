"""Provider-level plan-doc extraction tests (Claude Code + Codex).

Mostly DB-less: the extraction hooks operate on parsed JSONL dicts (Django
setup is still needed to import the compute modules). The subagent
ancestor-fold tests at the bottom exercise real Session rows.
"""

from datetime import datetime, UTC

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.codex.compute import CodexSessionCompute
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.plan_docs import DocEditEvent


@pytest.fixture
def claude():
    return ClaudeCodeSessionCompute()


@pytest.fixture
def codex():
    return CodexSessionCompute()


def claude_assistant_line(*blocks):
    return {
        'type': 'assistant',
        'message': {'role': 'assistant', 'content': list(blocks)},
    }


class TestClaudeCodeDocEdits:
    def test_write_tool(self, claude):
        line = claude_assistant_line(
            {'type': 'tool_use', 'id': 'tu1', 'name': 'Write',
             'input': {'file_path': '/repo/docs/plans/feature-plan.md', 'content': 'x'}},
        )
        events = claude.extract_doc_edit_events(line, cwd='/repo')
        assert [(e.path, e.action) for e in events] == [('/repo/docs/plans/feature-plan.md', 'write')]

    def test_edit_and_multiedit_tools(self, claude):
        line = claude_assistant_line(
            {'type': 'tool_use', 'id': 'tu1', 'name': 'Edit',
             'input': {'file_path': '/repo/notes.md', 'old_string': 'a', 'new_string': 'b'}},
            {'type': 'tool_use', 'id': 'tu2', 'name': 'MultiEdit',
             'input': {'file_path': '/repo/spec.md', 'edits': []}},
        )
        events = claude.extract_doc_edit_events(line, cwd='/repo')
        assert {e.path for e in events} == {'/repo/notes.md', '/repo/spec.md'}

    def test_non_matching_file_ignored(self, claude):
        line = claude_assistant_line(
            {'type': 'tool_use', 'id': 'tu1', 'name': 'Write',
             'input': {'file_path': '/repo/src/main.py', 'content': 'x'}},
        )
        assert claude.extract_doc_edit_events(line, cwd='/repo') == []

    def test_bash_redirect(self, claude):
        line = claude_assistant_line(
            {'type': 'tool_use', 'id': 'tu1', 'name': 'Bash',
             'input': {'command': 'echo hi > handoff.md'}},
        )
        events = claude.extract_doc_edit_events(line, cwd='/repo')
        assert [(e.path, e.action) for e in events] == [('/repo/handoff.md', 'write')]

    def test_bash_rm_is_delete(self, claude):
        line = claude_assistant_line(
            {'type': 'tool_use', 'id': 'tu1', 'name': 'Bash',
             'input': {'command': 'rm /repo/old-plan.md'}},
        )
        events = claude.extract_doc_edit_events(line, cwd='/repo')
        assert [(e.path, e.action) for e in events] == [('/repo/old-plan.md', 'delete')]

    def test_bash_relative_without_cwd_dropped(self, claude):
        line = claude_assistant_line(
            {'type': 'tool_use', 'id': 'tu1', 'name': 'Bash',
             'input': {'command': 'echo hi > notes.md'}},
        )
        assert claude.extract_doc_edit_events(line, cwd=None) == []

    def test_notebook_edit_ignored(self, claude):
        line = claude_assistant_line(
            {'type': 'tool_use', 'id': 'tu1', 'name': 'NotebookEdit',
             'input': {'notebook_path': '/repo/plan.ipynb'}},
        )
        assert claude.extract_doc_edit_events(line, cwd='/repo') == []

    def test_non_assistant_line(self, claude):
        assert claude.extract_doc_edit_events({'type': 'user', 'message': {'content': 'hi'}}, cwd='/repo') == []

    def test_extra_events_native_plan(self, claude, provider_home):
        plans_dir = provider_home.claude / 'plans'
        plans_dir.mkdir()
        (plans_dir / 'my-slug.md').write_text('# plan')

        from twicc.core.models import Session
        session = Session(slug=None)
        events = claude.extra_doc_edit_events(session, last_slug='my-slug')
        assert len(events) == 1
        event, timestamp = events[0]
        assert event.path == str(plans_dir / 'my-slug.md')
        assert event.action == 'write'
        assert event.source == 'claude_plan'
        assert timestamp is not None

    def test_extra_events_no_slug_or_missing_file(self, claude, provider_home):
        from twicc.core.models import Session
        assert claude.extra_doc_edit_events(Session(slug=None), last_slug=None) == []
        assert claude.extra_doc_edit_events(Session(slug='gone'), last_slug=None) == []


def codex_patch_apply_end(changes, *, success=True, status='completed'):
    return {
        'type': 'event_msg',
        'payload': {
            'type': 'patch_apply_end',
            'call_id': 'c1',
            'success': success,
            'status': status,
            'changes': changes,
        },
    }


def codex_function_call(name, arguments):
    return {
        'type': 'response_item',
        'payload': {
            'type': 'function_call',
            'name': name,
            'call_id': 'c1',
            'arguments': orjson.dumps(arguments).decode(),
        },
    }


class TestCodexDocEdits:
    def test_patch_apply_end_add_and_update(self, codex):
        line = codex_patch_apply_end({
            '/repo/docs/plans/a.md': {'type': 'add', 'content': 'x'},
            '/repo/spec.md': {'type': 'update', 'unified_diff': '+x'},
            '/repo/src/main.py': {'type': 'add', 'content': 'code'},
        })
        events = codex.extract_doc_edit_events(line, cwd='/repo')
        assert {(e.path, e.action) for e in events} == {
            ('/repo/docs/plans/a.md', 'write'), ('/repo/spec.md', 'write'),
        }

    def test_patch_apply_end_delete(self, codex):
        line = codex_patch_apply_end({'/repo/old-notes.md': {'type': 'delete', 'content': 'x'}})
        events = codex.extract_doc_edit_events(line, cwd='/repo')
        assert [(e.path, e.action) for e in events] == [('/repo/old-notes.md', 'delete')]

    def test_failed_or_declined_patch_ignored(self, codex):
        changes = {'/repo/plan.md': {'type': 'add', 'content': 'x'}}
        assert codex.extract_doc_edit_events(
            codex_patch_apply_end(changes, success=False, status='failed'), cwd='/repo') == []
        assert codex.extract_doc_edit_events(
            codex_patch_apply_end(changes, success=False, status='declined'), cwd='/repo') == []

    def test_shell_argv(self, codex):
        line = codex_function_call('shell', {'command': ['bash', '-lc', 'echo x > notes.md'], 'workdir': '/work'})
        events = codex.extract_doc_edit_events(line, cwd='/elsewhere')
        assert [(e.path, e.action) for e in events] == [('/work/notes.md', 'write')]

    def test_exec_command_cmd_key(self, codex):
        line = codex_function_call('exec_command', {'cmd': 'tee /repo/design.md'})
        events = codex.extract_doc_edit_events(line, cwd='/repo')
        assert [(e.path, e.action) for e in events] == [('/repo/design.md', 'write')]

    def test_shell_command_string(self, codex):
        line = codex_function_call('shell_command', {'command': 'echo x >> roadmap.md'})
        events = codex.extract_doc_edit_events(line, cwd='/repo')
        assert [(e.path, e.action) for e in events] == [('/repo/roadmap.md', 'write')]

    def test_local_shell_call_action(self, codex):
        line = {
            'type': 'response_item',
            'payload': {
                'type': 'local_shell_call',
                'call_id': 'c1',
                'action': {
                    'type': 'exec',
                    'command': ['bash', '-lc', 'touch checklist.md'],
                    'working_directory': '/work',
                },
            },
        }
        events = codex.extract_doc_edit_events(line, cwd=None)
        assert [(e.path, e.action) for e in events] == [('/work/checklist.md', 'write')]

    def test_write_stdin_ignored(self, codex):
        line = codex_function_call('write_stdin', {'cmd': 'echo x > plan.md'})
        assert codex.extract_doc_edit_events(line, cwd='/repo') == []

    def test_relative_target_without_any_cwd_dropped(self, codex):
        line = codex_function_call('shell_command', {'command': 'echo x > plan.md'})
        assert codex.extract_doc_edit_events(line, cwd=None) == []

    def test_malformed_arguments(self, codex):
        line = {
            'type': 'response_item',
            'payload': {'type': 'function_call', 'name': 'shell', 'call_id': 'c1', 'arguments': 'not-json'},
        }
        assert codex.extract_doc_edit_events(line, cwd='/repo') == []

# ---------------------------------------------------------------------------
# Subagent ancestor fold (DB-level)
# ---------------------------------------------------------------------------

T1 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 4, 13, 0, 0, tzinfo=UTC)


@pytest.fixture
def parent_and_subagent(db, tmp_path):
    project = Project.objects.create(id='test-project-plan-docs', directory=str(tmp_path))
    parent = Session.objects.create(
        id='plan-docs-parent', project=project, provider=Provider.CLAUDE_CODE,
        file_path='test/plan-docs-parent.jsonl',
    )
    subagent = Session.objects.create(
        id='plan-docs-subagent', project=project, provider=Provider.CLAUDE_CODE,
        type=SessionType.SUBAGENT, parent_session=parent,
        file_path='test/plan-docs-subagent.jsonl',
    )
    return parent, subagent


class TestSubagentAncestorFold:
    def test_fold_into_ancestor(self, parent_and_subagent, tmp_path):
        parent, subagent = parent_and_subagent
        (tmp_path / 'handoff.md').write_text('x')
        BaseSessionCompute._fold_plan_doc_events_into_ancestor(
            subagent,
            [(DocEditEvent(str(tmp_path / 'handoff.md'), 'write', 'subagent'), T1)],
        )
        parent.refresh_from_db()
        assert parent.plan_paths == [{
            'path': 'handoff.md', 'exists': True,
            'created_at': T1.isoformat(), 'updated_at': T1.isoformat(),
            'source': 'subagent',
        }]
        subagent.refresh_from_db()
        assert subagent.plan_paths == []

    def test_fold_preserves_parent_entries(self, parent_and_subagent, tmp_path):
        parent, subagent = parent_and_subagent
        parent.plan_paths = [{'path': 'docs/plan.md', 'exists': True,
                              'created_at': T1.isoformat(), 'updated_at': T1.isoformat(),
                              'source': 'detected'}]
        parent.save(update_fields=['plan_paths'])
        (tmp_path / 'notes.md').write_text('x')
        BaseSessionCompute._fold_plan_doc_events_into_ancestor(
            subagent,
            [(DocEditEvent(str(tmp_path / 'notes.md'), 'write', 'subagent'), T2)],
        )
        parent.refresh_from_db()
        assert {e['path'] for e in parent.plan_paths} == {'docs/plan.md', 'notes.md'}

    def test_orphan_subagent_is_noop(self, db, tmp_path):
        project = Project.objects.create(id='test-project-orphan', directory=str(tmp_path))
        orphan = Session.objects.create(
            id='plan-docs-orphan', project=project, provider=Provider.CLAUDE_CODE,
            type=SessionType.SUBAGENT,
        )
        BaseSessionCompute._fold_plan_doc_events_into_ancestor(
            orphan, [(DocEditEvent('/tmp/plan.md', 'write', 'subagent'), T1)],
        )
        orphan.refresh_from_db()
        assert orphan.plan_paths == []
