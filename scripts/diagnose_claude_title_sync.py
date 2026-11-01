#!/usr/bin/env python3
"""Real bundled-CLI upgrade gate for the private persistent title protocol.

Offline mode makes no model calls. Live mode needs explicit --yes consent.
Generated homes, transcripts, and tmux sessions are deleted on exit.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import importlib.metadata
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import uuid

import orjson

from twicc.provider_homes import provider_env_overlay


def report_exit(statuses):
    return 0 if statuses and all(status == 'PASS' for status in statuses) else 1


def bundled_cli():
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    return SubprocessCLITransport(prompt='x', options=ClaudeAgentOptions())._find_cli()


def offline_report():
    cli = bundled_cli()
    version = subprocess.run([cli, '--version'], env={**os.environ, **provider_env_overlay()},
                             capture_output=True, text=True, check=True, timeout=15)
    return {'sdk_version': importlib.metadata.version('claude-agent-sdk'),
            'cli_version': version.stdout.strip(), 'model_calls': 0,
            'total_cost_usd': 0.0, 'mode': 'offline',
            'checks': [{'name': 'installed_versions', 'status': 'PASS', 'detail': 'No model calls.'}]}


def render_report(report):
    return orjson.dumps(report, option=orjson.OPT_INDENT_2).decode()


@contextmanager
def isolated_environment(parent, token):
    """Read configured homes before overriding the child environment deliberately."""
    overlay = provider_env_overlay()
    with tempfile.TemporaryDirectory(prefix='claude-title-', dir=parent) as directory:
        root = Path(directory)
        for name in ('home', 'config', 'secure', 'cwd', 'codex'):
            (root / name).mkdir(mode=0o700)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('CLAUDE', 'ANTHROPIC'))}
        env.update(overlay)
        env.update(HOME=str(root / 'home'), CLAUDE_CONFIG_DIR=str(root / 'config'),
                   CLAUDE_SECURESTORAGE_CONFIG_DIR=str(root / 'secure'),
                   CLAUDE_CODE_OAUTH_TOKEN=token, CLAUDE_CODE_DISABLE_AUTO_MEMORY='1',
                   DISABLE_AUTOUPDATER='1')
        yield root, env


def title_rows(path, session_id):
    if not path.exists():
        return []
    return [row for line in path.read_bytes().split(b'\n')[:-1] if line
            and (row := orjson.loads(line)).get('type') == 'custom-title'
            and row.get('sessionId') == session_id]


def transcript(root, session_id):
    paths = list((root / 'config' / 'projects').glob(f'**/{session_id}.jsonl'))
    if len(paths) != 1:
        raise AssertionError('Canonical session transcript missing or ambiguous')
    return paths[0]


async def wait_title(path, session_id, title, boundary=0):
    for _ in range(100):
        rows = title_rows(path, session_id)
        if len(rows) > boundary and rows[-1]['customTitle'] == title:
            return rows
        await asyncio.sleep(.1)
    raise AssertionError('No matching post-dispatch title record')


async def sdk_probe(root, env, report):
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, SystemMessage
    from twicc.providers.claude_code.hermetic import check_claude_init
    from twicc.providers.claude_code.title_control import rename_running_session, TitleControlError

    session_id = str(uuid.uuid4())
    options = ClaudeAgentOptions(model='haiku', effort='low', cwd=str(root / 'cwd'), env=env,
        setting_sources=[], tools=[], allowed_tools=[], strict_mcp_config=True,
        permission_mode='dontAsk', max_turns=1, max_budget_usd=1.0,
        include_partial_messages=True,
        extra_args={'session-id': session_id, 'disable-slash-commands': None})

    def passed(name, detail):
        report['checks'].append({'name': name, 'status': 'PASS', 'detail': detail})

    async def turn(client, prompt='Reply OK.', streaming_title=None):
        report['model_calls'] += 1
        await client.query(prompt)
        result_seen = False
        renamed = False
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == 'init':
                check_claude_init(message.data, cwd=root / 'cwd', alias='haiku')
            if streaming_title and type(message).__name__ == 'StreamEvent':
                if message.event.get('type') == 'content_block_delta' and not renamed:
                    path = transcript(root, session_id)
                    boundary = len(title_rows(path, session_id))
                    await rename_running_session(client, session_id, streaming_title)
                    await wait_title(path, session_id, streaming_title, boundary)
                    renamed = True
            if isinstance(message, ResultMessage):
                report['total_cost_usd'] += message.total_cost_usd or 0
                assert not message.is_error, 'Model result error'
                assert message.session_id == session_id, 'Unexpected model session identity'
                result_seen = True
        assert result_seen, 'Missing completed model response'
        if streaming_title:
            assert renamed, 'No streaming delta available'

    async with ClaudeSDKClient(options=options) as client:
        await turn(client)
        path = transcript(root, session_id)
        boundary = len(title_rows(path, session_id))
        await rename_running_session(client, session_id, 'TITLE-A')
        await wait_title(path, session_id, 'TITLE-A', boundary)
        passed('initial_creation_idle_control', 'Session identity set before first prompt; ACK plus new title record.')
        # Reproduce the unsafe external append, then force the active CLI to flush metadata.
        with path.open('ab') as stream:
            stream.write(orjson.dumps({'type': 'custom-title', 'sessionId': session_id,
                                      'customTitle': 'EXTERNAL-FILE'}) + b'\n')
        await turn(client, 'Reply OK.' + ' ' * 70000)
        assert title_rows(path, session_id)[-1]['customTitle'] == 'TITLE-A'
        passed('file_append_reproduction', 'Active CLI metadata restores its title after external append.')
        await turn(client, 'Write the integers from 1 to 150, separated by spaces.', 'TITLE-B')
        passed('streaming_control', 'Control during text delta; matching record and completed response.')
        boundary = len(title_rows(path, session_id))
        await turn(client, 'Reply OK.' + ' ' * 70000)
        await wait_title(path, session_id, 'TITLE-B', boundary)
        passed('metadata_reappend', 'New metadata record preserves streaming title.')
        before = path.read_bytes()
        try:
            await rename_running_session(client, str(uuid.uuid4()), 'WRONG-ID')
        except TitleControlError as error:
            assert error.reason == 'rejected', 'Wrong identity did not explicitly reject'
        else:
            raise AssertionError('Wrong session identity accepted')
        assert path.read_bytes() == before, 'Wrong identity changes canonical transcript'
        passed('wrong_session_rejected', 'Explicit rejection and unchanged canonical transcript.')
        boundary = len(title_rows(path, session_id))
        await rename_running_session(client, session_id, 'TITLE-A')
        await wait_title(path, session_id, 'TITLE-A', boundary)
        passed('old_title_reuse', 'Voluntary return to earlier title produces a new record.')
    resumed = replace(options, resume=session_id, extra_args={'disable-slash-commands': None})
    async with ClaudeSDKClient(options=resumed) as client:
        boundary = len(title_rows(path, session_id))
        await turn(client, 'Reply OK.' + ' ' * 70000)
        await wait_title(path, session_id, 'TITLE-A', boundary)
    passed('resume', 'Resumed CLI re-appends the final title under the same session identity.')


def verify_hybrid_evidence(rows, session_id):
    """Check real rows separately from the strict coordinator fixture."""
    from django.conf import settings
    if not settings.configured:
        settings.configure(INSTALLED_APPS=['twicc.core'],
                           DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}})
        import django
        django.setup()
    from twicc.providers.claude_code.compute import extract_title_observations
    from twicc.providers.claude_code.title_sync import SourceCursor, TitleSyncCoordinator
    coordinator = TitleSyncCoordinator()
    observations = []
    parents = {row.get('uuid'): row for row in rows if row.get('uuid')}
    for number, row in enumerate(rows, 1):
        observations.extend(extract_title_observations(row,
            SourceCursor(session_id, 'diagnostic', 0, number), parent=parents.get(row.get('parentUuid'))))
    command = next((item for item in observations if item.kind == 'rename_command'
                    and item.title == 'HYBRID-TITLE'), None)
    assert command, 'Missing live /rename command echo'
    outcome = next((item for item in observations if item.kind == 'command_success'
                    and item.parent_message_id == command.message_id), None)
    assert outcome and outcome.title == 'HYBRID-TITLE', 'Missing live command result ancestry'
    title = next((item for item in observations if item.kind == 'title'
                  and item.title == 'HYBRID-TITLE'), None)
    assert title, 'Missing live title record'
    assert command.cursor.line_num < outcome.cursor.line_num, 'Command result precedes command echo'
    # This fixture uses the actual ordered CLI evidence. It proves convergence,
    # while preserving the independent uncertainty about the paste sender.
    with coordinator.staged_state() as stage:
        coordinator.attach_process(session_id, 'diagnostic-process', hybrid=True)
        decision = coordinator.accept_decision(session_id, 'HYBRID-TITLE')
        assert coordinator.start_dispatch(decision, SourceCursor(session_id, 'diagnostic', 0, 0))
        result = coordinator.observe(tuple(observations))
        assert any(item.title == 'HYBRID-TITLE' for item in result.apply)
        assert not result.confirmed, 'Convergence falsely confirms own delivery'
        coordinator.acknowledge(decision, success=True, boundary=observations[-1].cursor)
        successor = coordinator.accept_decision(session_id, 'SUCCESSOR')
        assert not coordinator.start_dispatch(successor, observations[-1].cursor), 'Uncertain paste permits successor'
        stage.commit()
    return {'command_line': command.cursor.line_num, 'result_line': outcome.cursor.line_num,
            'title_line': title.cursor.line_num, 'successor_blocked': True,
            'own_delivery_confirmed': False}


def cleanup_hybrid_server(tmux, report):
    """Clean only the diagnostic's private socket and report bounded failures."""
    try:
        result = tmux('kill-server', check=False)
        absent = ('no server running on ' in result.stderr
                  or (result.stderr.startswith('error connecting to ')
                      and '(No such file or directory)' in result.stderr))
        success = result.returncode == 0 or absent
        detail = 'Diagnostic tmux server stopped or already absent.' if success else 'Diagnostic tmux cleanup rejected.'
    except (OSError, subprocess.TimeoutExpired) as error:
        success = False
        detail = 'Diagnostic tmux cleanup failed: ' + type(error).__name__
    report['checks'].append({'name': 'hybrid_cleanup', 'status': 'PASS' if success else 'FAIL', 'detail': detail})


async def hybrid_probe(root, env, report):
    """Run the actual interactive CLI, never infer own-paste attribution."""
    session = 'title-probe-' + uuid.uuid4().hex
    socket = str(root / 'tmux.sock')
    session_id = str(uuid.uuid4())
    args = [bundled_cli(), '--session-id', session_id, '--model', 'haiku', '--tools', '',
            '--setting-sources', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--permission-mode', 'dontAsk', '--no-chrome']
    def tmux(*arguments, check=True):
        return subprocess.run(['tmux', '-S', socket, *arguments], env=env, cwd=root / 'cwd',
                              capture_output=True, text=True, check=check, timeout=10)
    try:
        tmux('new-session', '-d', '-s', session, '-x', '160', '-y', '50', shlex.join(args))
        for _ in range(20):
            screen = tmux('capture-pane', '-p', '-t', session).stdout
            # Complete only the isolated CLI's onboarding, without user config copies.
            if 'Choose the text style' in screen or 'Choose the theme' in screen:
                tmux('send-keys', '-t', session, 'Enter')
            elif 'trust this folder' in screen.lower() or 'trust this directory' in screen.lower():
                tmux('send-keys', '-t', session, 'Down', 'Enter')
            elif '❯' in screen and 'Welcome' not in screen and 'Accessing workspace:' not in screen:
                break
            await asyncio.sleep(1)
        tmux('send-keys', '-t', session, '-l', '/rename HYBRID-TITLE')
        tmux('send-keys', '-t', session, 'Enter')
        for _ in range(15):
            paths = list((root / 'config' / 'projects').glob(f'**/{session_id}.jsonl'))
            if paths and title_rows(paths[0], session_id):
                break
            await asyncio.sleep(1)
        screen = tmux('capture-pane', '-p', '-t', session).stdout
        if not paths or not title_rows(paths[0], session_id):
            report['checks'].append({'name': 'hybrid_command_evidence', 'status': 'INCONCLUSIVE',
                'detail': 'Isolated TUI does not produce rename evidence; onboarding or command readiness blocks the probe.',
                'blocker': ('login onboarding' if 'login' in screen.lower() or 'log in' in screen.lower() else 'theme onboarding' if 'theme' in screen.lower() else 'trust onboarding' if 'trust' in screen.lower() else 'unknown TUI state')})
            return
        rows = [orjson.loads(line) for line in paths[0].read_bytes().splitlines() if line]
        evidence = verify_hybrid_evidence(rows, session_id)
        report['checks'].append({'name': 'hybrid_command_evidence', 'status': 'PASS',
            'detail': 'Real CLI echo, result ancestry, title order, and strict hazard verified.', 'evidence': evidence})
    finally:
        cleanup_hybrid_server(tmux, report)


async def live_report(report, *, hybrid=False):
    from twicc.providers.claude_code.auth import get_credentials
    credentials = get_credentials()
    if not credentials:
        raise RuntimeError('No configured OAuth credential')
    report['mode'] = 'live'
    with isolated_environment(None, credentials[0]) as (root, env):
        # SDK transport inherits os.environ before applying options.env. Remove nesting flags too.
        original = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(env)
            try:
                await asyncio.wait_for(sdk_probe(root, env, report), timeout=180)
            except Exception as error:
                report['checks'].append({'name': 'sdk_probe', 'status': 'FAIL', 'detail': str(error) if isinstance(error, AssertionError) else type(error).__name__})
                expected = ('initial_creation_idle_control', 'file_append_reproduction', 'streaming_control',
                            'metadata_reappend', 'wrong_session_rejected', 'old_title_reuse', 'resume')
                completed = {check['name'] for check in report['checks']}
                report['checks'].extend({'name': name, 'status': 'INCONCLUSIVE',
                    'detail': 'Not completed after SDK probe failure.'} for name in expected if name not in completed)
            if hybrid:
                try:
                    await hybrid_probe(root, env, report)
                except Exception as error:
                    report['checks'].append({'name': 'hybrid_command_evidence', 'status': 'INCONCLUSIVE', 'detail': type(error).__name__})
            else:
                report['checks'].append({'name': 'hybrid_command_evidence', 'status': 'NOT_RUN',
                    'detail': 'Hybrid is disabled. Use --live --yes --hybrid before future activation.'})
        finally:
            os.environ.clear()
            os.environ.update(original)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--hybrid', action='store_true', help='Also require the real TUI probe before future hybrid activation.')
    parser.add_argument('--json', action='store_true', help='Emit structured JSON (also the default format).')
    args = parser.parse_args(argv)
    if args.hybrid and not args.live:
        parser.error('--hybrid requires --live --yes')
    if args.live and not args.yes:
        parser.error('--live requires --yes (billable isolated model calls)')
    try:
        report = offline_report()
        if args.live:
            asyncio.run(live_report(report, hybrid=args.hybrid))
    except Exception as error:
        print(render_report({'status': 'SETUP_FAILURE', 'detail': type(error).__name__}))
        return 2
    print(render_report(report))
    return report_exit([check['status'] for check in report['checks']
                        if not (check['name'] == 'hybrid_command_evidence' and check['status'] == 'NOT_RUN')])


if __name__ == '__main__':
    raise SystemExit(main())
