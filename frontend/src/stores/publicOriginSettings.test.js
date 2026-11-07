// Contracts that cross a file boundary, or live in code no test can execute
// (a Pinia store definition, the ASGI consumer). The origin form's own
// behaviour is exercised for real in
// ../composables/useOriginSettingsForm.test.js — do not re-add source regexes
// for it here.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { ORIGIN_SETTINGS_RESULT_EVENT } from '../composables/useOriginSettingsForm.js'

const settingsSource = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')
const popoverSource = readFileSync(new URL('../components/app/SettingsPopover.vue', import.meta.url), 'utf8')
const websocketSource = readFileSync(new URL('../composables/useWebSocket.js', import.meta.url), 'utf8')
const browserSource = readFileSync(new URL('../components/browser/BrowserPane.vue', import.meta.url), 'utf8')
const backendSource = readFileSync(new URL('../../../src/twicc/asgi.py', import.meta.url), 'utf8')

const FIELDS = [
    ['Public', 'publicBaseUrl'],
    ['Share', 'shareBaseUrl'],
    ['Peer', 'peerBaseUrl'],
]

test('External, Share, and Peer origins expose a fail-closed getter', () => {
    for (const [name, key] of FIELDS) {
        assert.match(settingsSource, new RegExp(
            'getUsable' + name + 'BaseUrl:[^\\n]+usablePublicOrigin\\(state\\.' + key + '\\)',
        ))
    }
})

test('the store has one non-optimistic per-field send action', () => {
    for (const [name] of FIELDS) {
        assert.doesNotMatch(settingsSource, new RegExp('set' + name + 'BaseUrl\\('))
    }
    assert.match(settingsSource, /async sendOriginSetting\(field, value, requestId\)/)
    assert.match(settingsSource, /sendSyncedSettings\(\{ \[field\]: value \}, _settingsVersion, requestId\)/)
    assert.doesNotMatch(settingsSource, /this\.\w+BaseUrl = value/)
})

test('the correlation ID travels from the browser to the backend and back', () => {
    assert.match(websocketSource, /request_id: requestId/)
    assert.match(backendSource, /request_id = content\.get\("request_id"\)/)
    assert.match(backendSource, /"type": "synced_settings_result"/)
    assert.match(websocketSource, /applySyncedSettings\(msg\.settings, msg\.version\)/)
})

test('the form subscribes to the event name useWebSocket actually dispatches', () => {
    // The one link the form cannot verify on its own: it listens for a name a
    // different module chooses.
    assert.match(
        websocketSource,
        new RegExp("dispatchEvent\\(new CustomEvent\\('" + ORIGIN_SETTINGS_RESULT_EVENT + "'"),
    )
})

test('the popover delegates the origin wiring instead of holding it', () => {
    assert.match(popoverSource, /useOriginSettingsForm\(\{/)
    assert.match(popoverSource, /onMounted\(startOriginSettingsForm\)/)
    assert.match(popoverSource, /onBeforeUnmount\(stopOriginSettingsForm\)/)
    // No second copy of the correlation bookkeeping.
    assert.doesNotMatch(popoverSource, /pendingOriginWrites/)
    assert.doesNotMatch(popoverSource, /generateUUID/)
})

test('the former External URL label is now External address', () => {
    assert.match(popoverSource, /\$t\('settings\.general\.externalAddress\.label'\).*<wa-icon/)
    assert.doesNotMatch(popoverSource, />External URL <wa-icon/)
})

test('the Browser companion falls back when the External address is invalid', () => {
    assert.match(
        browserSource,
        /usablePublicOrigin\(settingsStore\.getPublicBaseUrl\) \|\| window\.location\.origin/,
    )
})
