"""Offline contracts for the real CLI title compatibility gate."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def diagnostic():
    spec = importlib.util.spec_from_file_location('title_diagnostic', Path(__file__).parents[1] / 'scripts/diagnose_claude_title_sync.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('statuses,expected', [(['PASS'], 0), (['FAIL'], 1), (['INCONCLUSIVE'], 1)])
def test_exit(diagnostic, statuses, expected):
    assert diagnostic.report_exit(statuses) == expected


def test_offline_has_no_calls(diagnostic):
    report = diagnostic.offline_report()
    assert report['model_calls'] == 0
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in diagnostic.render_report(report)


def test_cleanup_on_error(diagnostic, tmp_path):
    with pytest.raises(RuntimeError), diagnostic.isolated_environment(tmp_path, 'secret') as (root, env):
        assert Path(env['CLAUDE_CONFIG_DIR']).is_relative_to(root)
        assert Path(env['CLAUDE_SECURESTORAGE_CONFIG_DIR']).is_relative_to(root)
        raise RuntimeError()
    assert list(tmp_path.iterdir()) == []


def test_provider_overlay_and_nesting(diagnostic, monkeypatch, tmp_path):
    monkeypatch.setenv('CLAUDECODE', 'nested')
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'inherited')
    monkeypatch.setattr(diagnostic, 'provider_env_overlay', lambda: {'CODEX_HOME': '/configured/codex'})
    with diagnostic.isolated_environment(tmp_path, 'secret') as (_, env):
        assert env['CODEX_HOME'] == '/configured/codex'
        assert 'CLAUDECODE' not in env
        assert 'CLAUDE_CODE_SESSION_ID' not in env
    assert diagnostic.os.environ['CLAUDECODE'] == 'nested'


def test_invalid_session_records(diagnostic, tmp_path):
    path = tmp_path / 'session.jsonl'
    path.write_bytes(b'{"type":"custom-title","sessionId":"foreign","customTitle":"bad"}\n')
    assert diagnostic.title_rows(path, 'wanted') == []


def test_setup_failure(diagnostic, monkeypatch, capsys):
    monkeypatch.setattr(diagnostic, 'offline_report', lambda: (_ for _ in ()).throw(RuntimeError('secret')))
    assert diagnostic.main([]) == 2
    assert 'secret' not in capsys.readouterr().out


def test_hybrid_requires_real_command_ancestry(diagnostic):
    with pytest.raises(AssertionError):
        diagnostic.verify_hybrid_evidence([], 'session')


def test_hybrid_strict_hazard(diagnostic):
    sid = 'session'
    rows = [
        {'type': 'user', 'uuid': 'cmd', 'message': {'content': '<command-name>/rename</command-name><command-args>HYBRID-TITLE</command-args>'}},
        {'type': 'user', 'uuid': 'result', 'parentUuid': 'cmd', 'message': {'content': '<local-command-stdout>Session renamed to: HYBRID-TITLE</local-command-stdout>'}},
        {'type': 'custom-title', 'sessionId': sid, 'customTitle': 'HYBRID-TITLE'},
    ]
    assert diagnostic.verify_hybrid_evidence(rows, sid)['successor_blocked'] is True


def test_offline_never_opens_sdk_or_credentials(diagnostic, monkeypatch):
    import claude_agent_sdk
    from twicc.providers.claude_code import auth
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline mode starts a provider')
    monkeypatch.setattr(claude_agent_sdk, 'ClaudeSDKClient', forbidden)
    monkeypatch.setattr(auth, 'get_credentials', forbidden)
    assert diagnostic.offline_report()['model_calls'] == 0


def test_live_error_cleans_home_restores_environment_and_hides_secret(diagnostic, monkeypatch):
    import asyncio
    from twicc.providers.claude_code import auth
    monkeypatch.setattr(auth, 'get_credentials', lambda: ('private-token', 0))
    monkeypatch.setenv('CLAUDECODE', 'original-nesting')
    roots = []
    async def fail(root, env, report):
        roots.append(root)
        assert 'CLAUDECODE' not in diagnostic.os.environ
        assert env['CLAUDE_CODE_OAUTH_TOKEN'] == 'private-token'
        raise RuntimeError('private-token')
    async def no_hybrid(*args):
        return None
    monkeypatch.setattr(diagnostic, 'sdk_probe', fail)
    monkeypatch.setattr(diagnostic, 'hybrid_probe', no_hybrid)
    report = asyncio.run(diagnostic.live_report(diagnostic.offline_report()))
    assert all(not root.exists() for root in roots)
    assert diagnostic.os.environ['CLAUDECODE'] == 'original-nesting'
    assert 'private-token' not in diagnostic.render_report(report)
    assert diagnostic.report_exit([row['status'] for row in report['checks']]) == 1


@pytest.mark.parametrize('hybrid,status,expected', [(False, 'NOT_RUN', 0), (True, 'INCONCLUSIVE', 1)])
def test_scoped_live_gate(diagnostic, monkeypatch, capsys, hybrid, status, expected):
    async def report(value, *, hybrid=False):
        value['checks'].append({'name': 'hybrid_command_evidence', 'status': status})
        return value
    monkeypatch.setattr(diagnostic, 'live_report', report)
    args = ['--live', '--yes', '--json'] + (['--hybrid'] if hybrid else [])
    assert diagnostic.main(args) == expected
    assert status in capsys.readouterr().out


def test_title_reader_ignores_incomplete_append(diagnostic, tmp_path):
    path = tmp_path / 'session.jsonl'
    path.write_bytes(b'{"type":"custom-title","sessionId":"s","customTitle":"complete"}\n{"type":')
    assert [row['customTitle'] for row in diagnostic.title_rows(path, 's')] == ['complete']
