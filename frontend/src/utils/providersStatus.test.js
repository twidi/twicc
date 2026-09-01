// frontend/src/utils/providersStatus.test.js
//
// Run with:  node --test src/utils/providersStatus.test.js   (from the frontend dir)
//
// The vendor feed cannot be driven on demand, so the record shapes the backend
// writes for an incident's whole life (open → change of level → resolve →
// next incident) are replayed here against the pure rules, with
// acknowledgments interleaved the way a user would dismiss the toasts.

import test from 'node:test'
import assert from 'node:assert/strict'

import {
    RESOLVED_ANNOUNCE_WINDOW_MS,
    deriveEpisode,
    episodeKey,
    episodeToWire,
    formatIncidentWindow,
    isAcknowledged,
    planToast,
    toastSentence,
    toastTitle,
} from './providersStatus.js'

const T0 = Date.parse('2026-09-01T14:02:00Z')
const MIN = 60_000
const iso = (minutes) => new Date(T0 + minutes * MIN).toISOString()

const START = iso(0)
const NOW = T0 + 90 * MIN

const ANTHROPIC = { productLabel: 'Claude Code', vendorLabel: 'Anthropic', statusUrl: 'https://status.claude.com/' }

// Records as ``twicc/providers_status.py`` writes them.
const quiet = { status: 'operational', incident: null, acknowledged: null }
const record = (status, incident, acknowledged = null) => ({ status, incident, acknowledged })
const inc = (status, changed, resolved = null) => ({
    started_at: START, status, changed_at: iso(changed), resolved_at: resolved === null ? null : iso(resolved),
})
const ack = (status, changed) => ({ started_at: START, status, changed_at: iso(changed) })

const degraded = record('degraded_performance', inc('degraded_performance', 0))
const major = record('major_outage', inc('major_outage', 20))
const resolved = record('operational', inc('major_outage', 88, 88))

// ── deriveEpisode ───────────────────────────────────────────────────────────

test('nothing to announce: no record, no incident, or an unknown level', () => {
    assert.equal(deriveEpisode(undefined, NOW), null)
    assert.equal(deriveEpisode(null, NOW), null)
    assert.equal(deriveEpisode(quiet, NOW), null)
    assert.equal(deriveEpisode(record('meteor_strike', inc('meteor_strike', 0)), NOW), null)
    // A non-operational status with no incident has no identity to acknowledge.
    assert.equal(deriveEpisode(record('major_outage', null), NOW), null)
})

test('an ongoing outage is an outage episode: the incident start, the level, the transition date', () => {
    assert.deepEqual(deriveEpisode(degraded, NOW), {
        kind: 'outage', startedAt: START, status: 'degraded_performance', changedAt: iso(0), resolvedAt: null, lastStatus: 'degraded_performance',
    })
    // Change of level: same start, new level, new transition date.
    assert.deepEqual(deriveEpisode(major, NOW), {
        kind: 'outage', startedAt: START, status: 'major_outage', changedAt: iso(20), resolvedAt: null, lastStatus: 'major_outage',
    })
})

test('a resolved incident is a resolved episode carrying its window', () => {
    assert.deepEqual(deriveEpisode(resolved, NOW), {
        kind: 'resolved', startedAt: START, status: 'operational', changedAt: iso(88), resolvedAt: iso(88), lastStatus: 'major_outage',
    })
})

test('a record written before transitions were dated still derives an episode', () => {
    const undated = record('major_outage', { started_at: START, status: 'major_outage', resolved_at: null })
    assert.equal(deriveEpisode(undated, NOW).changedAt, START)
    const undatedResolved = record('operational', { started_at: START, status: 'major_outage', resolved_at: iso(88) })
    assert.equal(deriveEpisode(undatedResolved, NOW).changedAt, iso(88))
})

test('a resolved incident stops being announced after 24 hours', () => {
    const resolvedMs = Date.parse(iso(88))
    assert.notEqual(deriveEpisode(resolved, resolvedMs + RESOLVED_ANNOUNCE_WINDOW_MS), null)
    assert.equal(deriveEpisode(resolved, resolvedMs + RESOLVED_ANNOUNCE_WINDOW_MS + 1), null)
    // An ongoing outage has no expiry.
    assert.notEqual(deriveEpisode(major, resolvedMs + 365 * 24 * 60 * MIN), null)
})

test('a resolved incident with a garbage timestamp is ignored', () => {
    const broken = record('operational', { ...resolved.incident, resolved_at: 'yesterday-ish' })
    assert.equal(deriveEpisode(broken, NOW), null)
})

// ── acknowledgment ──────────────────────────────────────────────────────────

test('isAcknowledged matches start, level and transition date exactly', () => {
    const episode = deriveEpisode(major, NOW)
    assert.equal(isAcknowledged(major, episode), false)
    assert.equal(isAcknowledged(record(major.status, major.incident, ack('major_outage', 20)), episode), true)
    assert.equal(isAcknowledged(record(major.status, major.incident, ack('degraded_performance', 20)), episode), false)
    assert.equal(isAcknowledged(record(major.status, major.incident, ack('major_outage', 0)), episode), false)
    assert.equal(isAcknowledged(record(major.status, major.incident, { ...ack('major_outage', 20), started_at: iso(1) }), episode), false)
})

test('episode identity and wire shape', () => {
    const episode = deriveEpisode(resolved, NOW)
    assert.equal(episodeKey(episode), `${START}|${iso(88)}|operational`)
    assert.deepEqual(episodeToWire(episode), { started_at: START, status: 'operational', changed_at: iso(88) })
})

// ── planToast: the whole life of an incident ────────────────────────────────

test('outage → change of level → resolution → next incident, with dismissals in between', () => {
    // Quiet: nothing.
    assert.equal(planToast(quiet, NOW), null)

    // Degraded: a warning toast.
    const p1 = planToast(degraded, NOW)
    assert.equal(p1.type, 'warning')
    assert.equal(p1.key, `${START}|${iso(0)}|degraded_performance`)

    // The user dismisses it (anywhere): the same record, acknowledged → nothing.
    assert.equal(planToast(record(degraded.status, degraded.incident, ack('degraded_performance', 0)), NOW), null)

    // Up to major: a new transition of the same incident → an error toast,
    // although the previous level was acknowledged.
    const p2 = planToast(record(major.status, major.incident, ack('degraded_performance', 0)), NOW)
    assert.equal(p2.type, 'error')
    assert.equal(p2.key, `${START}|${iso(20)}|major_outage`)

    // Acknowledged at major: nothing.
    assert.equal(planToast(record(major.status, major.incident, ack('major_outage', 20)), NOW), null)

    // Resolution: a success toast, whatever was acknowledged before.
    const p3 = planToast(record(resolved.status, resolved.incident, ack('major_outage', 20)), NOW)
    assert.equal(p3.type, 'success')
    assert.equal(p3.key, `${START}|${iso(88)}|operational`)
    assert.equal(p3.episode.kind, 'resolved')

    // Acknowledged the resolution: nothing, and still nothing after 24h.
    const acked = record(resolved.status, resolved.incident, ack('operational', 88))
    assert.equal(planToast(acked, NOW), null)
    assert.equal(planToast(acked, NOW + 2 * RESOLVED_ANNOUNCE_WINDOW_MS), null)

    // Next incident: a new start, so a previous acknowledgment never matches.
    const next = {
        status: 'partial_outage',
        incident: { started_at: iso(180), status: 'partial_outage', changed_at: iso(180), resolved_at: null },
        acknowledged: ack('operational', 88),
    }
    const p4 = planToast(next, NOW + 200 * MIN)
    assert.equal(p4.type, 'warning')
    assert.equal(p4.key, `${iso(180)}|${iso(180)}|partial_outage`)
})

test('major → partial → major: every step is announced, the second major too', () => {
    // First major, dismissed.
    const m1 = record('major_outage', inc('major_outage', 0), ack('major_outage', 0))
    assert.equal(planToast(m1, NOW), null)

    // Down to partial: announced — "it is heading towards a resolution".
    const partial = record('partial_outage', inc('partial_outage', 10), ack('major_outage', 0))
    assert.equal(planToast(partial, NOW).key, `${START}|${iso(10)}|partial_outage`)

    // Dismissed, then back up to major: announced again — "it got worse
    // again" — although the level was already acknowledged once: a different
    // transition, a different date.
    const m2 = record('major_outage', inc('major_outage', 20), ack('partial_outage', 10))
    assert.equal(planToast(m2, NOW).key, `${START}|${iso(20)}|major_outage`)
    const m2AckedAsFirst = record('major_outage', inc('major_outage', 20), ack('major_outage', 0))
    assert.notEqual(planToast(m2AckedAsFirst, NOW), null)

    // Same level polled again is not a new transition: the backend writes
    // nothing, the record is identical, the acknowledged toast stays silent.
    const m2Acked = record('major_outage', inc('major_outage', 20), ack('major_outage', 20))
    assert.equal(planToast(m2Acked, NOW), null)
    assert.equal(planToast(m2Acked, NOW + 30 * MIN), null)
})

test('the metro case: outage and recovery both missed, the recovery is announced on return', () => {
    // Never acknowledged anything about this incident, resolved 2 hours ago.
    assert.equal(planToast(resolved, Date.parse(iso(88)) + 2 * 60 * MIN).type, 'success')
    // Came back three days later: too old to matter.
    assert.equal(planToast(resolved, Date.parse(iso(88)) + 3 * 24 * 60 * MIN), null)
})

test('a fresh install with a quiet vendor never announces a recovery', () => {
    assert.equal(planToast({ status: 'operational', incident: null, acknowledged: null }, NOW), null)
})

test('providers are independent records: one is a function of its own record only', () => {
    const records = {
        claude_code: record(major.status, major.incident, ack('major_outage', 20)),
        codex: degraded,
    }
    assert.equal(planToast(records.claude_code, NOW), null)
    assert.equal(planToast(records.codex, NOW).type, 'warning')
})

// ── wording ─────────────────────────────────────────────────────────────────

test('outage and recovery sentences', () => {
    assert.equal(toastTitle('Anthropic'), 'Anthropic status update')
    assert.equal(
        toastSentence(deriveEpisode(major, NOW), ANTHROPIC),
        "Claude Code is currently experiencing a major outage on Anthropic's side",
    )
    assert.equal(
        toastSentence(deriveEpisode(record('under_maintenance', inc('under_maintenance', 0)), NOW), ANTHROPIC),
        "Claude Code is currently under maintenance on Anthropic's side",
    )
    const opts = { locale: 'en-GB', timeZone: 'UTC', now: NOW }
    assert.equal(
        toastSentence(deriveEpisode(resolved, NOW), ANTHROPIC, opts),
        "Claude Code issues on Anthropic's side are now resolved (14:02 → 15:30)",
    )
})

test('the window shows times only on the current day, and the date otherwise', () => {
    const opts = { locale: 'en-GB', timeZone: 'UTC' }
    assert.equal(formatIncidentWindow(START, iso(88), { ...opts, now: NOW }), '14:02 → 15:30')
    // Seen the next day: both ends carry their date.
    const nextDay = formatIncidentWindow(START, iso(88), { ...opts, now: NOW + 24 * 60 * MIN })
    assert.match(nextDay, /^1 Sept?,? 14:02 → 1 Sept?,? 15:30$/)
    // An incident that crossed midnight: only the far end carries its date.
    const overnight = formatIncidentWindow(iso(9 * 60), iso(11 * 60), { ...opts, now: T0 + 12 * 60 * MIN })
    assert.match(overnight, /^1 Sept?,? 23:02 → 01:02$/)
    assert.equal(formatIncidentWindow('nope', iso(88), { ...opts, now: NOW }), '? → 15:30')
})
