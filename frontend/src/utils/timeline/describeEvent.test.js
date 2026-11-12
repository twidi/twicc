import assert from 'node:assert/strict'
import test from 'node:test'

import { PROCESS_ENDED_REASONS, describeEvent } from './describeEvent.js'
import { ARROW } from './eventTime.js'

const TIME = { locale: 'en-GB', timeZone: 'UTC' }
const sessions = {
    boss: { title: 'The boss', project_id: 'p', provider: 'claude_code', hidden: false, archived: false },
    parent: { title: 'Parent session', project_id: 'p', provider: 'codex', hidden: false, archived: false },
}
let nextId = 1
const make = (kind, fields = {}) => ({
    id: nextId++, kind, occurred_at: '2026-10-14T14:32:07.000000Z', precision: 'exact', actor: 'agent', via: null,
    actor_ref: null, actor_name: null, reason: '', data: {}, session_id: 's1', project_id: 'p', provider: 'codex', ...fields,
})
const describe = (kind, fields, context = {}) => describeEvent(make(kind, fields), { sessions, time: TIME, ...context })
// Text as is, a value (a title, a setting value) in [brackets], the arrow as " → ", a session mention in <angle brackets>.
const words = (vm) => vm.parts.map((part) => {
    if (part.type === 'text') return part.text
    if (part.type === 'value') return `[${part.text}]`
    if (part.type === 'arrow') return ' → '
    return `<${part.mention.title}>`
}).join('')

test('the 23 kinds: wording, icon and severity (section 6.1)', () => {
    const cases = [
        ['session.created', { data: { origin: 'twicc' } }, 'Session created', 'plus', 'neutral'],
        ['title.changed', { data: { source: 'user', new: 'B', old: 'A' } }, 'Renamed [A] → [B]', 'pencil', 'neutral'],
        ['settings.changed', { data: { changes: { effort: ['low', 'high'] } } }, 'effort: [low] → [high]', 'sliders', 'neutral'],
        ['process.started', { data: { cause: 'new' } }, 'Process started', 'play', 'success'],
        ['process.ended', { reason: 'manual' }, 'Process stopped', 'power-off', 'neutral'],
        ['turn.started', {}, 'Turn started', 'robot', 'neutral'],
        ['turn.ended', { data: { background_work: null } }, 'Turn ended', 'circle-check', 'success'],
        ['compaction.requested', { data: { text: 'Keep X', source: 'send' } }, 'Compaction requested', 'compress', 'neutral'],
        ['compaction.completed', { data: { trigger: 'auto', text: null } }, 'Compaction completed', 'compress', 'neutral'],
        ['awaiting_input.started', { data: { request_type: 'tool_approval' } }, 'Waiting for approval', 'hand', 'warning'],
        ['awaiting_input.ended', { data: { request_type: 'tool_approval' } }, 'Approval handled', 'circle-check', 'neutral'],
        ['archived.started', {}, 'Archived', 'box-archive', 'neutral'],
        ['archived.ended', {}, 'Unarchived', 'box-open', 'neutral'],
        ['hidden.started', {}, 'Hidden', 'eye-slash', 'neutral'],
        ['hidden.ended', {}, 'Unhidden', 'eye', 'neutral'],
        ['muted.started', {}, 'Muted', 'bell-slash', 'neutral'],
        ['muted.ended', {}, 'Unmuted', 'bell', 'neutral'],
        ['tree_detached.started', {}, 'Detached from parent', 'sitemap', 'neutral'],
        ['tree_detached.ended', {}, 'Reattached to parent', 'sitemap', 'neutral'],
        ['pinned.started', { data: { scope: 'project' } }, 'Pinned', 'thumbtack', 'neutral'],
        ['pinned.ended', { data: { scope: 'project' } }, 'Unpinned', 'thumbtack-slash', 'neutral'],
        ['backend.started', { session_id: null, data: { version: '1.2.3' } }, 'TwiCC started', 'server', 'neutral'],
        ['backend.ended', { session_id: null, data: { clean: true } }, 'TwiCC stopped', 'power-off', 'neutral'],
    ]
    assert.equal(cases.length, 23)
    for (const [kind, fields, wording, icon, severity] of cases) {
        const vm = describe(kind, fields)
        assert.deepEqual([words(vm), vm.icon, vm.severity], [wording, icon, severity], kind)
    }
})

test('an unknown kind shows its raw name with a generic icon and no detail', () => {
    const vm = describe('plan.approved', { data: { anything: 1 } })
    assert.deepEqual([words(vm), vm.icon, vm.severity, vm.tags, vm.badge, vm.detail], ['plan.approved', 'circle-info', 'neutral', [], null, null])
})

test('data missing or not an object is treated as empty and never throws', () => {
    for (const data of [undefined, null, 'text', 7, ['a']]) {
        for (const kind of ['session.created', 'title.changed', 'settings.changed', 'process.started', 'process.ended', 'turn.ended',
            'compaction.requested', 'compaction.completed', 'awaiting_input.started', 'pinned.started', 'backend.started', 'backend.ended', 'archived.started']) {
            assert.doesNotThrow(() => describe(kind, { data }), `${kind} / ${JSON.stringify(data)}`)
        }
    }
})

test('process.ended: every reason has its wording, severity and cause tag; the table has 16 reasons', () => {
    assert.equal(Object.keys(PROCESS_ENDED_REASONS).length, 16)
    const expected = {
        manual: ['Process stopped', 'neutral', []], force: ['Process killed', 'neutral', []],
        archived: ['Process stopped', 'neutral', ['session archived']], shutdown: ['Process stopped', 'neutral', ['TwiCC shutdown']],
        'apply-settings': ['Process restarted to apply settings', 'neutral', []],
        'switch-hybrid': ['Process stopped', 'neutral', ['switch to hybrid']],
        timeout_user_turn: ['Process stopped', 'neutral', ['idle timeout']], 'cli-exit': ['Hybrid CLI exited', 'neutral', []],
        timeout_starting: ['Process stopped: start timed out', 'warning', []],
        timeout_assistant_turn: ['Process stopped: no activity', 'warning', []],
        cron_restart_timeout: ['Cron restart timed out', 'warning', []], lost: ['Process lost', 'warning', ['found dead at boot']],
        auth_required: ['Process stopped: login required', 'danger', []], 'startup-failed': ['Process failed to start', 'danger', []],
        'agent-start-failed': ['Process failed to start', 'danger', []],
        error: ['Process crashed', 'danger', []],
    }
    for (const [reason, [wording, severity, tags]] of Object.entries(expected)) {
        const vm = describe('process.ended', { reason })
        assert.deepEqual([words(vm), vm.severity, vm.tags], [wording, severity, tags], reason)
        assert.equal(vm.icon, 'power-off')
    }
})

test('process.ended: an unknown reason reads "Process stopped" with the raw reason in the tooltip', () => {
    for (const reason of ['brand_new', 'constructor', '']) {
        const vm = describe('process.ended', { reason })
        assert.deepEqual([words(vm), vm.severity, vm.tags], ['Process stopped', 'neutral', []], reason)
    }
    assert.match(describe('process.ended', { reason: 'brand_new' }).tooltip, /Reason: brand_new/)
})

test('process.ended error: the first line is the detail, the rest sits behind the expand control', () => {
    const vm = describe('process.ended', { reason: 'error', data: { error: 'Boom: it broke\n  at file.js:1\n  at main' } })
    assert.deepEqual(vm.detail, { first: 'Boom: it broke', rest: 'at file.js:1\n  at main' })
    assert.equal(describe('process.ended', { reason: 'error', data: { error: null } }).detail, null)
    assert.equal(describe('process.ended', { reason: 'manual', data: { error: 'ignored' } }).detail, null)
})

test('process.ended lost: the upper bound adds "Detected at boot HH:MM" to the tooltip', () => {
    const vm = describe('process.ended', {
        reason: 'lost', precision: 'estimated', data: { upper_bound: '2026-10-14T16:45:00+00:00' },
    })
    assert.match(vm.tooltip, /Detected at boot 16:45/)
})

test('process.started: resumed, hybrid, unknown cause', () => {
    assert.equal(words(describe('process.started', { data: { cause: 'resume' } })), 'Process resumed')
    const shown = (vm) => [words(vm), vm.tags]
    assert.deepEqual(shown(describe('process.started', { data: { cause: 'resume' } })), ['Process resumed', []])
    assert.deepEqual(shown(describe('process.started', { data: { cause: 'resume', hybrid: true } })), ['Process resumed', ['hybrid CLI']])
    assert.deepEqual(shown(describe('process.started', { data: { cause: 'new', hybrid: true } })), ['Process started', ['hybrid CLI']])
    assert.equal(words(describe('process.started', { data: { cause: 'cron_restart' } })), 'Process started')
    assert.match(describe('process.started', { data: { twicc_pid: 4242 } }).tooltip, /Process id: 4242/)
})

test('awaiting_input: request_type wins over tool_name; tool_name is a value, cut near 40 characters', () => {
    const w = (kind, data) => words(describe(kind, { data }))
    const started = 'awaiting_input.started'
    const ended = 'awaiting_input.ended'
    assert.equal(w(started, { request_type: 'tool_approval', tool_name: 'Bash' }), 'Waiting for approval ([Bash])')
    assert.equal(w(started, { request_type: 'tool_approval', tool_name: null }), 'Waiting for approval')
    assert.equal(w(ended, { request_type: 'tool_approval', tool_name: 'Bash' }), 'Approval handled ([Bash])')
    assert.equal(w(ended, { request_type: 'tool_approval' }), 'Approval handled')
    assert.equal(w(started, { request_type: 'ask_user_question', tool_name: 'AskUserQuestion' }), 'Question for you')
    assert.equal(w(ended, { request_type: 'ask_user_question', tool_name: 'AskUserQuestion' }), 'Question answered')
    assert.equal(w(started, { request_type: 'hybrid_terminal', tool_name: 'Bash' }), 'Waiting in the terminal ([Bash])')
    assert.equal(w(started, { request_type: 'hybrid_terminal' }), 'Waiting in the terminal')
    assert.equal(w(ended, { request_type: 'hybrid_terminal', tool_name: 'Bash' }), 'Terminal input handled')
    assert.equal(w(started, { request_type: null, tool_name: 'Bash' }), 'Waiting for input ([Bash])')
    assert.equal(w(started, { request_type: 'future_type' }), 'Waiting for input')
    assert.equal(w(ended, { request_type: 'future_type', tool_name: 'Bash' }), 'Input handled')
    const denied = describe(ended, { reason: 'auto_denied', data: { request_type: 'tool_approval', tool_name: 'Bash', auto_deny_after: 120 } })
    assert.equal(words(denied), 'Approval denied automatically ([Bash])')
    assert.equal(denied.icon, 'clock')
    assert.equal(denied.severity, 'warning')
    assert.deepEqual(denied.tags, ['no answer in 2 min'])
    const odd = describe(ended, { reason: 'auto_denied', data: { request_type: 'tool_approval', auto_deny_after: 90 } })
    assert.deepEqual(odd.tags, ['no answer in 90 s'])
    assert.deepEqual(describe(ended, { reason: 'auto_denied', data: { request_type: 'tool_approval' } }).tags, [])
    const long = w(started, { request_type: 'tool_approval', tool_name: 'mcp__server__' + 'x'.repeat(100) })
    // TOOL_MAX (40) counts the ellipsis: 39 characters of the name, then the ellipsis.
    assert.equal(long, `Waiting for approval ([${'mcp__server__' + 'x'.repeat(26)}…])`)
})

test('session.created: origin wording', () => {
    const w = (data) => words(describe('session.created', { actor: 'system', data }))
    assert.equal(w({ origin: 'twicc' }), 'Session created')
    assert.equal(w({ origin: 'external' }), 'Session created from a terminal')
    assert.equal(w({ origin: 'sync' }), 'Session created')
    assert.equal(w({ origin: 'something-else' }), 'Session created')
    assert.equal(w({}), 'Session created')
})

test('session.created: the parent appears once, and never next to an agent author label', () => {
    const parentPart = (vm) => vm.parts.find((part) => part.type === 'session')
    const plain = describe('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })
    assert.equal(words(plain), 'Session created by <Parent session>')
    assert.equal(parentPart(plain).mention.link, true)
    // A compute-written creation row reads like a live one, parent included.
    const synced = describe('session.created', { actor: 'system', data: { origin: 'sync', spawned_by: 'parent' } })
    assert.equal(words(synced), 'Session created by <Parent session>')
    const external = describe('session.created', { actor: 'system', data: { origin: 'external', spawned_by: 'parent' } })
    assert.equal(words(external), 'Session created from a terminal (spawned by <Parent session>)')
    const byAgent = describe('session.created', {
        actor: 'agent', via: 'mcp_internal', actor_ref: 'boss', data: { origin: 'twicc', spawned_by: 'parent' },
    })
    assert.equal(words(byAgent), 'Session created')
    assert.equal(parentPart(byAgent), undefined)
    assert.equal(byAgent.author.kind, 'session')
    const missing = describe('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'purged-id-123' } })
    assert.equal(words(missing), 'Session created by <purged-i>')
    assert.equal(parentPart(missing).mention.link, false)
})

test('title.changed: renamed, titled, cleared, source tags', () => {
    const title = (data) => describe('title.changed', { data })
    assert.equal(words(title({ source: 'user', old: 'A', new: 'B' })), 'Renamed [A] → [B]')
    assert.equal(words(title({ source: 'user', old: null, new: 'B' })), 'Titled [B]')
    assert.equal(words(title({ source: 'user', old: '', new: 'B' })), 'Titled [B]')
    assert.equal(words(title({ source: 'user', old: 'A', new: null })), 'Title cleared')
    assert.equal(words(title({ source: 'user', old: 'A', new: '' })), 'Title cleared')
    assert.deepEqual(title({ source: 'user', new: 'B' }).tags, [])
    assert.deepEqual(title({ source: 'auto', new: 'B' }).tags, ['automatic'])
    // The provider wrote it in its own files (a /rename in the CLI, Codex naming its thread): named after the provider.
    const labelsFor = (provider) => ({ claude_code: { product: 'Claude Code' }, codex: { product: 'Codex' } }[provider] ?? null)
    assert.deepEqual(describe('title.changed', { provider: 'codex', data: { source: 'agent', new: 'B' } }, { labelsFor }).tags, ['from Codex'])
    assert.deepEqual(describe('title.changed', { provider: 'claude_code', data: { source: 'agent', new: 'B' } }, { labelsFor }).tags, ['from Claude Code'])
    assert.deepEqual(title({ source: 'agent', new: 'B' }).tags, ['from the provider'])
    assert.deepEqual(title({ source: 'future', new: 'B' }).tags, [])
    assert.deepEqual(title({ source: 'constructor', new: 'B' }).tags, [])
})

test('title.changed: the titles are value parts (no quotes), the arrow is its own part with a text alternative', () => {
    const vm = describe('title.changed', { data: { source: 'auto', old: 'Old one', new: 'New one' } })
    assert.deepEqual(vm.parts, [
        { type: 'text', text: 'Renamed ' }, { type: 'value', text: 'Old one' }, { type: 'arrow', label: 'to' }, { type: 'value', text: 'New one' },
    ])
    assert.deepEqual(describe('title.changed', { data: { new: 'B' } }).parts, [{ type: 'text', text: 'Titled ' }, { type: 'value', text: 'B' }])
    assert.deepEqual(describe('title.changed', { data: { old: 'A' } }).parts, [{ type: 'text', text: 'Title cleared' }])
})

test('settings.changed: the values are value parts like the titles, with the same arrow part; the field name is prose', () => {
    const vm = describe('settings.changed', { data: { changes: { effort: ['low', 'high'], fast_mode: [false, true] } } })
    assert.deepEqual(vm.parts, [
        { type: 'text', text: 'effort: ' }, { type: 'value', text: 'low' }, { type: 'arrow', label: 'to' }, { type: 'value', text: 'high' },
        { type: 'text', text: ', fast_mode: ' }, { type: 'value', text: 'off' }, { type: 'arrow', label: 'to' }, { type: 'value', text: 'on' },
    ])
})

test('title.changed: long titles are cut near 80 characters and the full text goes to the tooltip', () => {
    const old = 'O'.repeat(200)
    const next = 'N'.repeat(200)
    const vm = describe('title.changed', { data: { source: 'user', old, new: next } })
    // TITLE_MAX (80) counts the ellipsis: 79 characters of each title, then the ellipsis.
    assert.equal(words(vm), `Renamed [${'O'.repeat(79)}…] → [${'N'.repeat(79)}…]`)
    assert.ok(vm.tooltip.includes(`Old title: ${old}`) && vm.tooltip.includes(`New title: ${next}`))
    const short = describe('title.changed', { data: { source: 'user', old: 'a', new: 'b' } })
    assert.ok(!short.tooltip.includes('Old title'))
})

test('strings from data stay plain strings: markup is not interpreted here', () => {
    const vm = describe('title.changed', { data: { source: 'user', old: null, new: '<img src=x onerror=alert(1)>' } })
    assert.equal(words(vm), 'Titled [<img src=x onerror=alert(1)>]')
})

test('settings.changed: labels come from the provider of the row, then of the session', () => {
    const asked = []
    const labelsFor = (provider) => {
        asked.push(provider)
        return null
    }
    describeEvent(make('settings.changed', { provider: 'codex', data: { changes: { effort: ['a', 'b'] } } }), { sessions, labelsFor, time: TIME })
    describeEvent(make('settings.changed', { provider: null, session_id: 'boss', data: { changes: { effort: ['a', 'b'] } } }), { sessions, labelsFor, time: TIME })
    assert.deepEqual(asked, ['codex', 'claude_code'])
})

test('settings.changed: a long diff keeps all changes in the tooltip; opening permissions is a warning', () => {
    const changes = { selected_model: ['a', 'b'], effort: ['a', 'b'], fast_mode: [false, true], permission_mode: ['default', 'yolo'] }
    const vm = describe('settings.changed', { data: { changes } })
    assert.equal(vm.severity, 'warning')
    assert.match(words(vm), /\+1 more$/)
    assert.ok(vm.tooltip.includes(`permission_mode: default${ARROW}yolo`), 'the tooltip is plain text with the arrow character')
    assert.equal(ARROW, ' → ')
})

test('pinned: scope tag and fallbacks', () => {
    const w = (kind, scope) => {
        const vm = describe(kind, { data: { scope } })
        return [words(vm), vm.tags]
    }
    // The scope is a tag at the end of the wording.
    assert.deepEqual(w('pinned.started', 'project'), ['Pinned', ['project']])
    assert.deepEqual(w('pinned.started', 'workspace'), ['Pinned', ['workspace']])
    assert.deepEqual(w('pinned.started', 'all'), ['Pinned', ['all projects']])
    assert.deepEqual(w('pinned.started', null), ['Pinned', []])
    assert.deepEqual(w('pinned.started', 'galaxy'), ['Pinned', []])
    assert.deepEqual(w('pinned.ended', 'all'), ['Unpinned', ['was all projects']])
    assert.deepEqual(w('pinned.ended', null), ['Unpinned', []])
    assert.deepEqual(w('pinned.ended', 'toString'), ['Unpinned', []])
})

test('backend rows: version, machine restart, clean or not, tooltip facts', () => {
    const started = (data) => describe('backend.started', { session_id: null, actor: 'system', data })
    // The version is a tag at the end of the wording, after the machine restart.
    const shown = (vm) => [words(vm), vm.tags]
    assert.deepEqual(shown(started({ version: null })), ['TwiCC started', []])
    assert.deepEqual(shown(started({ version: '' })), ['TwiCC started', []])
    assert.deepEqual(shown(started({ version: '1.2.3', machine_restarted: true })), ['TwiCC started after a machine restart', ['v1.2.3']])
    assert.deepEqual(shown(started({ version: '1.2.3', machine_restarted: false })), ['TwiCC started', ['v1.2.3']])
    assert.deepEqual(shown(started({ version: '1.2.3', machine_restarted: null })), ['TwiCC started', ['v1.2.3']])
    const tooltip = started({ pid: 77, version: '1.2.3', boot_id: 'abc-def' }).tooltip
    assert.ok(tooltip.includes('Process id: 77') && tooltip.includes('Version: 1.2.3') && tooltip.includes('Boot id: abc-def'))
    const ended = (clean) => describe('backend.ended', { session_id: null, actor: 'system', data: { clean } })
    assert.deepEqual([words(ended(true)), ended(true).severity], ['TwiCC stopped', 'neutral'])
    assert.deepEqual([words(ended(false)), ended(false).severity], ['TwiCC stopped unexpectedly', 'danger'])
    assert.deepEqual([words(ended(null)), ended(null).severity], ['TwiCC stopped', 'neutral'])
})

test('turn.ended: the background-work badge, plain when there is none or the shape is unknown', () => {
    const badge = describe('turn.ended', { data: { background_work: { subagents: 2, shells: 0, monitors: 0, scheduled_wakeup_at: null, goal: false } } }).badge
    assert.equal(badge.text, '2 subagents')
    assert.equal(describe('turn.ended', { data: { background_work: null } }).badge, null)
    assert.equal(describe('turn.ended', { data: { background_work: 'weird' } }).badge, null)
    assert.equal(words(describe('turn.ended', { data: { background_work: 'weird' } })), 'Turn ended')
})

test('time: clock in the locale, "~", a flag and a caption for estimated only; derived reads as exact', () => {
    const exact = describe('turn.started', {})
    assert.deepEqual([exact.time.text, exact.time.estimated, exact.time.caption], ['14:32', false, null])
    const estimated = describe('turn.started', { precision: 'estimated' })
    assert.deepEqual([estimated.time.text, estimated.time.estimated], ['~14:32', true])
    assert.match(estimated.tooltip, /Estimated: it happened at or before this time/)
    // A `derived` time is the session's `created_at`, read from its file: shown like an exact one.
    const derived = describe('session.created', { precision: 'derived', actor: 'system', data: { origin: 'sync' } })
    assert.deepEqual([derived.time.text, derived.time.estimated, derived.time.caption], ['14:32', false, null])
    assert.ok(!derived.tooltip.includes('Estimated:'))
    assert.match(derived.tooltip, /Precision: derived/)
    const unknown = describe('turn.started', { precision: 'approximate' })
    assert.deepEqual([unknown.time.text, unknown.time.estimated], ['14:32', false])
})

test('tooltip: full time, precision, reason, via, actor ref and name; unknown combinations show the actor', () => {
    const vm = describe('settings.changed', {
        actor: 'external', via: 'mcp_external', actor_ref: 'conn-1', actor_name: 'ChatGPT', reason: 'set_mode',
        data: { changes: { effort: ['a', 'b'] } },
    })
    for (const line of ['14 Oct 2026', '14:32:07', 'Precision: exact', 'Reason: set_mode', 'Via: mcp_external', 'Actor ref: conn-1', 'Actor name: ChatGPT']) {
        assert.ok(vm.tooltip.includes(line), line)
    }
    assert.ok(!vm.tooltip.includes('Actor: '))
    const odd = describe('archived.started', { actor: 'user', via: 'mcp_external' })
    assert.ok(odd.tooltip.includes('Actor: user'))
})

test('a known subagent is plain text in an agent author label and as the parent of session.created', () => {
    const isSubagent = (id) => id === 'boss' || id === 'parent'
    const label = describeEvent(make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 'boss' }), { sessions, time: TIME, isSubagent })
    assert.equal(label.author.parts[1].mention.link, false)
    assert.equal(describeEvent(make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 'boss' }), { sessions, time: TIME }).author.parts[1].mention.link, true)
    const created = make('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })
    const mention = (vm) => vm.parts.find((part) => part.type === 'session').mention
    assert.equal(mention(describeEvent(created, { sessions, time: TIME, isSubagent })).link, false)
    assert.equal(mention(describeEvent(created, { sessions, time: TIME })).link, true)
})

test('the tooltips of process and backend rows carry their technical facts', () => {
    assert.match(describe('process.ended', { reason: 'manual', data: { twicc_pid: 99 } }).tooltip, /Process id: 99/)
    const ended = describe('backend.ended', { session_id: null, actor: 'system', data: { clean: true, pid: 5, version: '1.0.0', boot_id: 'boot-1' } })
    for (const line of ['Process id: 5', 'Version: 1.0.0', 'Boot id: boot-1']) assert.ok(ended.tooltip.includes(line), line)
})

test('the author label is part of the view-model', () => {
    const vm = describe('archived.started', { actor: 'user', via: 'cli' })
    assert.equal(vm.author.parts[0].text, 'by you (CLI)')
})

test('an automatic row: an "automatic" tag at the end of the wording, no author label, never twice', () => {
    const restarted = describe('process.started', { actor: 'system', via: null, data: { cause: 'resume', hybrid: true } })
    // After the tags of the kind: the source comes last.
    assert.deepEqual([words(restarted), restarted.tags, restarted.author.parts], ['Process resumed', ['hybrid CLI', 'automatic'], []])
    const titled = describe('title.changed', { actor: 'system', via: null, data: { source: 'agent', new: 'B' } })
    assert.deepEqual(titled.tags, ['from the provider', 'automatic'])
    // An automatic title already says it.
    assert.deepEqual(describe('title.changed', { actor: 'system', via: null, data: { source: 'auto', new: 'B' } }).tags, ['automatic'])
    // Not on the kinds whose wording says it, nor on a row the user caused.
    assert.deepEqual(describe('backend.ended', { session_id: null, actor: 'system', via: null, data: { clean: true } }).tags, [])
    assert.deepEqual(describe('process.ended', { actor: 'user', via: 'ui', reason: 'manual' }).tags, [])
})

test('no "automatic" tag when the wording already says it: a known end reason, an automatic settings reason', () => {
    const tagsOf = (kind, fields) => describe(kind, { actor: 'system', via: null, ...fields }).tags
    for (const reason of Object.keys(PROCESS_ENDED_REASONS)) {
        assert.ok(!tagsOf('process.ended', { reason }).includes('automatic'), reason)
    }
    assert.deepEqual(tagsOf('process.ended', { reason: 'shutdown' }), ['TwiCC shutdown'])
    // A reason this frontend does not know: the tag is the only hint.
    assert.deepEqual(tagsOf('process.ended', { reason: 'brand_new' }), ['automatic'])
    const changes = { data: { changes: { effort: ['low', 'high'] } } }
    assert.deepEqual(tagsOf('settings.changed', { ...changes, reason: 'model_retired' }), [])
    assert.deepEqual(tagsOf('settings.changed', { ...changes, reason: 'switch-hybrid' }), [])
    assert.deepEqual(tagsOf('settings.changed', { ...changes, reason: '' }), ['automatic'])
})


test('message labels identify the role from kind, while origin remains a separate author label', () => {
    for (const [kind, label] of [['user_message.sent', 'User message'], ['assistant_message.sent', 'Assistant message']]) {
        const vm = describe(kind, { actor: 'external', via: 'mcp_external', actor_name: 'ChatGPT', data: { text: 'Saved secret text' } })
        assert.equal(words(vm), label)
        assert.ok(!vm.tooltip.includes('Saved secret text'))
        assert.ok(!vm.author.parts.map(part => part.text ?? '').join('').includes(label))
        assert.equal(vm.severity, 'neutral')
    }
})

test('workflow rows: start, resume and every end status', () => {
    const data = { run_id: 'wf_1', task_id: 't1', name: 'normalize-i18n-glossary' }
    const cases = [
        ['workflow.started', { data: { ...data, cause: 'new' } }, 'Workflow started [normalize-i18n-glossary]', 'sitemap', 'neutral'],
        ['workflow.started', { data: { ...data, cause: 'resume' } }, 'Workflow resumed [normalize-i18n-glossary]', 'sitemap', 'neutral'],
        ['workflow.ended', { data: { ...data, status: 'completed' } }, 'Workflow completed [normalize-i18n-glossary]', 'circle-check', 'success'],
        ['workflow.ended', { data: { ...data, status: 'failed' } }, 'Workflow failed [normalize-i18n-glossary]', 'circle-xmark', 'danger'],
        ['workflow.ended', { data: { ...data, status: 'killed' } }, 'Workflow stopped [normalize-i18n-glossary]', 'circle-stop', 'neutral'],
        ['workflow.ended', { data: { ...data, status: 'stopped' } }, 'Workflow stopped [normalize-i18n-glossary]', 'circle-stop', 'neutral'],
        ['workflow.ended', { data: { ...data, status: 'paused' } }, 'Workflow paused [normalize-i18n-glossary]', 'circle-pause', 'neutral'],
        ['workflow.ended', { reason: 'manual', data: { ...data, status: 'interrupted' } }, 'Workflow interrupted [normalize-i18n-glossary]', 'circle-stop', 'warning'],
        ['workflow.ended', { data: { ...data, status: 'odd' } }, 'Workflow ended (odd) [normalize-i18n-glossary]', 'sitemap', 'neutral'],
        ['workflow.ended', { data: {} }, 'Workflow ended', 'sitemap', 'neutral'],
    ]
    for (const [kind, fields, wording, icon, severity] of cases) {
        const vm = describe(kind, fields)
        assert.deepEqual([words(vm), vm.icon, vm.severity], [wording, icon, severity], `${kind} ${fields.data?.status ?? fields.data?.cause}`)
    }
})

test('an interrupted workflow names what ended its process; an end never says "automatic"', () => {
    const interrupted = (reason) => describe('workflow.ended', { actor: 'system', reason, data: { status: 'interrupted' } }).tags
    assert.deepEqual(interrupted('lost'), ['found dead at boot'])
    assert.deepEqual(interrupted('shutdown'), ['TwiCC shutdown'])
    assert.deepEqual(interrupted('manual'), ['process stopped'])
    assert.deepEqual(interrupted('error'), ['process crashed'])
    assert.deepEqual(interrupted('apply-settings'), ['process restarted to apply settings'])
    assert.deepEqual(interrupted('unknown-reason'), ['process stopped'])
    assert.deepEqual(describe('workflow.ended', { actor: 'system', data: { status: 'completed' } }).tags, [])
})

test('a long workflow name is cut, its full name and ids in the tooltip', () => {
    const name = 'n'.repeat(90)
    const vm = describe('workflow.started', { data: { run_id: 'wf_1', task_id: 't1', name } })
    assert.ok(vm.parts[1].text.length < name.length)
    assert.match(vm.tooltip, new RegExp(`Workflow: ${name}`))
    assert.match(vm.tooltip, /Run id: wf_1/)
    assert.match(vm.tooltip, /Task id: t1/)
})

test('compaction.completed: its trigger is a tag, once, and an unknown trigger has none', () => {
    assert.deepEqual(describe('compaction.completed', { actor: 'agent', data: { trigger: 'manual' } }).tags, ['manual'])
    assert.deepEqual(describe('compaction.completed', { actor: 'agent', data: { trigger: 'auto' } }).tags, ['automatic'])
    assert.deepEqual(describe('compaction.completed', { actor: 'system', data: { trigger: 'auto' } }).tags, ['automatic'])
    assert.deepEqual(describe('compaction.completed', { actor: 'agent', data: { trigger: 'other' } }).tags, [])
})
