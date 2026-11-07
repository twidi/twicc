import en from '../i18n/locales/en.json' with { type: 'json' }
// Registration guard for the two agent-sharing gate keys (agent-sharing
// design §4 "Plumbing"). settings.js is not importable under node --test
// (extensionless imports), so the store-side registration points are
// asserted on the source text; constants.js is imported for real.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { SYNCED_SETTINGS_KEYS } from '../constants.js'

const KEYS = ['allowAgentSessionShares', 'allowAgentArtifactShares']
const settingsSource = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')

test('both keys are synced', () => {
    for (const key of KEYS) assert.ok(SYNCED_SETTINGS_KEYS.has(key), key)
})

test('both keys are registered at every store point', () => {
    for (const key of KEYS) {
        // SETTINGS_SCHEMA placeholder (synced keys use null).
        assert.match(settingsSource, new RegExp(`${key}: null,`), `${key} in SETTINGS_SCHEMA`)
        // Boolean validator.
        assert.match(settingsSource, new RegExp(`${key}: \\(v\\) => typeof v === 'boolean'`), `${key} validator`)
        // Outgoing sync payload.
        assert.match(settingsSource, new RegExp(`${key}: store\\.${key},`), `${key} in collectAllSyncedSettings`)
    }
    // Getters and setters.
    assert.match(settingsSource, /isAllowAgentSessionShares/, 'session getter')
    assert.match(settingsSource, /isAllowAgentArtifactShares/, 'artifact getter')
    assert.match(settingsSource, /setAllowAgentSessionShares\(enabled\)/, 'session setter')
    assert.match(settingsSource, /setAllowAgentArtifactShares\(enabled\)/, 'artifact setter')
})

test('the switch copy carries both consent disclosures, per switch (§4/§14)', () => {
    const popoverSource = readFileSync(
        new URL('../components/app/SettingsPopover.vue', import.meta.url), 'utf8')
    for (const kind of ['session', 'artifact']) {
        const key = `settings.sharing.agentPermissions.${kind}SharesHint`
        assert.ok(popoverSource.includes(`$t('${key}')`))
        const copy = en[key]
        assert.match(copy,
            new RegExp(`revoke any existing ${kind}\\s+share, including links created by you`),
            `${kind}: revoke-anything disclosure`)
        assert.match(copy,
            new RegExp(`read the URL of every existing\\s+${kind} share, including links created by you or by another agent`),
            `${kind}: read-all disclosure`)
    }
})
