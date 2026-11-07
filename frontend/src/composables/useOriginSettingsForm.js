import { i18n } from '../i18n/index.js'
/**
 * The Settings form for the three public origins (External / Share / Peer).
 *
 * Extracted from SettingsPopover.vue so the wiring is executable code a test
 * can drive, instead of a shape a regex can only recognise. The component keeps
 * the template, the DOM refs and the lifecycle hooks; everything below is plain
 * reactivity with its dependencies injected.
 *
 * The contract each field follows:
 *
 * - the input is LOCAL text, never pushed to the store while typing;
 * - Apply validates the safe frontend subset, then sends ONE field with a
 *   correlation id — Python owns the canonical value and the relationships;
 * - a result settles a write only if its id is still pending AND the visible
 *   text is unchanged since Apply. Typing, a broadcast, or a disconnect can all
 *   invalidate a write in flight;
 * - a store broadcast refreshes the text only while it still shows the previous
 *   stored value, so it never overwrites what the user is typing.
 */
import { computed, ref, watch } from 'vue'

// Explicit .js extensions: this module is loaded directly by `node --test`,
// which does not resolve extension-less specifiers the way Vite does.
import { generateUUID } from '../utils/crypto.js'
import { checkPublicOriginInput, usablePublicOrigin } from '../utils/publicOrigin.js'
import {
    ORIGIN_SETTING_KEYS,
    PUBLIC_ORIGIN_ERROR,
    discardOriginSettingWrites,
    originSettingErrorMessage,
    publicOriginErrorMessage,
    refreshOriginInput,
    resolveOriginSettingResult,
    validateOriginSetting,
} from '../utils/originSettingsForm.js'

// The backend answers any correlated synced-settings write with this frame;
// useWebSocket re-dispatches it as a browser event.
export const ORIGIN_SETTINGS_RESULT_EVENT = 'twicc:synced-settings-result'

// Two distinct failures, two distinct messages. A refused send never left the
// browser, so "try again" is the whole truth. A send followed by a dropped
// connection may well have been applied server-side — claiming otherwise would
// be wrong half the time.
export const NOT_CONNECTED_ERROR = 'Not connected to the server — try again.'
export const CONNECTION_LOST_ERROR = 'Connection lost — this change may not have been saved. '
    + 'Check after reconnecting.'

export const PLAIN_HTTP_WARNING = 'Plain HTTP — tokens travel unencrypted. HTTPS is strongly recommended.'

const STORE_GETTERS = {
    publicBaseUrl: 'getPublicBaseUrl',
    shareBaseUrl: 'getShareBaseUrl',
    peerBaseUrl: 'getPeerBaseUrl',
}

function fieldMap(build) {
    return Object.fromEntries(ORIGIN_SETTING_KEYS.map(field => [field, build(field)]))
}

export function normalizedInputValue(value) {
    return checkPublicOriginInput(value).value ?? value.trim()
}

export function storedOriginError(value) {
    return usablePublicOrigin(value) || !value ? '' : PUBLIC_ORIGIN_ERROR
}

/**
 * @param {object} options
 * @param {object} options.settingsStore - exposes the three getters and sendOriginSetting()
 * @param {object} options.dataStore - exposes wsConnected
 * @param {string} options.locationHostname - the browser's hostname (Share host check)
 * @param {EventTarget} options.eventTarget - where the result event is dispatched
 */
export function useOriginSettingsForm({
    settingsStore,
    dataStore,
    locationHostname,
    eventTarget,
}) {
    const inputs = fieldMap(() => ref(''))
    const errors = fieldMap(() => ref(''))
    // Peer-only: plain HTTP is allowed but worth a warning, next to the error.
    const peerBaseUrlWarning = ref('')
    const validationErrors = fieldMap(() => ref([]))
    const displayErrors = fieldMap(field => computed(() => {
        if (errors[field].value === NOT_CONNECTED_ERROR) return i18n.global.t('settings.origins.notConnected')
        if (errors[field].value === CONNECTION_LOST_ERROR) return i18n.global.t('settings.origins.connectionLost')
        if (errors[field].value === PUBLIC_ORIGIN_ERROR) return publicOriginErrorMessage('invalid_origin')
        return validationErrors[field].value.length
            ? originSettingErrorMessage(validationErrors[field].value, field, publicOriginErrorMessage)
            : errors[field].value
    }))
    const displayPeerWarning = computed(() => peerBaseUrlWarning.value
        ? i18n.global.t('settings.origins.plainHttp') : '')
    const peerBaseUrlConfirmation = ref(false)

    // Correlation ids of writes whose result has not arrived yet.
    const pendingWrites = new Map()

    const storedValue = field => settingsStore[STORE_GETTERS[field]] || ''
    const storedValues = () => fieldMap(storedValue)

    const normalized = fieldMap(field => computed(() => normalizedInputValue(inputs[field].value)))
    const modified = fieldMap(field => computed(() => normalized[field].value !== storedValue(field)))
    const applyIcon = fieldMap(
        field => computed(() => (modified[field].value ? 'triangle-exclamation' : 'check')),
    )

    /** Reload one field from the store — used when its Settings section opens. */
    function seedField(field) {
        inputs[field].value = storedValue(field)
        validationErrors[field].value = []
        errors[field].value = storedOriginError(inputs[field].value)
        if (field === 'peerBaseUrl') peerBaseUrlConfirmation.value = false
    }

    function onInputChange(field, value) {
        // Typing invalidates any result still in flight for THIS field only.
        discardOriginSettingWrites(pendingWrites, field)
        inputs[field].value = value
        errors[field].value = ''
        validationErrors[field].value = []
        if (field === 'peerBaseUrl') {
            peerBaseUrlWarning.value = ''
            peerBaseUrlConfirmation.value = false
        }
    }

    function setError(field, fieldErrors) {
        errors[field].value = ''
        validationErrors[field].value = fieldErrors
    }

    async function apply(field, confirmed = false) {
        errors[field].value = ''
        validationErrors[field].value = []
        if (field === 'peerBaseUrl') {
            peerBaseUrlWarning.value = ''
            peerBaseUrlConfirmation.value = false
        }
        const result = validateOriginSetting({
            field,
            input: inputs[field].value,
            stored: storedValues(),
            locationHostname,
        })
        // Before the early return below: the warning describes the value the
        // user is applying, so a no-op Apply on an already-stored plain-HTTP
        // address must keep showing it instead of silently clearing it.
        if (result.warning === 'http') peerBaseUrlWarning.value = PLAIN_HTTP_WARNING
        setError(field, result.errors)
        if (result.errors.length || !Object.keys(result.patch).length) return
        if (
            field === 'peerBaseUrl'
            && storedValue(field)
            && !confirmed
        ) {
            peerBaseUrlConfirmation.value = true
            return
        }
        const requestId = generateUUID()
        pendingWrites.set(requestId, { field, input: inputs[field].value })
        if (!await settingsStore.sendOriginSetting(field, result.patch[field], requestId)) {
            const pending = pendingWrites.get(requestId)
            pendingWrites.delete(requestId)
            if (pending && inputs[field].value === pending.input) errors[field].value = NOT_CONNECTED_ERROR
        }
    }

    function handleResult(payload) {
        const pending = pendingWrites.get(payload?.request_id)
        if (!pending) return
        const result = resolveOriginSettingResult(pendingWrites, payload, inputs[pending.field].value)
        if (!result) return
        if (result.status === 'accepted') {
            errors[result.field].value = ''
            validationErrors[result.field].value = []
            inputs[result.field].value = result.value
            return
        }
        setError(result.field, result.errors)
    }

    function onResultEvent(event) {
        handleResult(event.detail)
    }

    // One-click prefill from the External address, when it is usable.
    const canPrefillPeerBaseUrl = computed(() => {
        const external = usablePublicOrigin(settingsStore.getPublicBaseUrl)
        return Boolean(external) && external !== normalized.peerBaseUrl.value
    })

    function prefillPeerBaseUrlFromPublic() {
        inputs.peerBaseUrl.value = usablePublicOrigin(settingsStore.getPublicBaseUrl)
        errors.peerBaseUrl.value = ''
        validationErrors.peerBaseUrl.value = []
        peerBaseUrlWarning.value = ''
        peerBaseUrlConfirmation.value = false
    }

    function cancelPeerBaseUrlApply() {
        peerBaseUrlWarning.value = ''
        seedField('peerBaseUrl')
    }

    // Broadcasts update the store. They do not settle correlated writes.
    const stopWatchers = ORIGIN_SETTING_KEYS.map(field => watch(
        () => settingsStore[STORE_GETTERS[field]],
        (value, oldValue) => {
            inputs[field].value = refreshOriginInput(inputs[field].value, oldValue, value)
            if (field === 'peerBaseUrl') peerBaseUrlConfirmation.value = false
        },
    ))

    stopWatchers.push(watch(() => dataStore.wsConnected, connected => {
        if (connected) return
        // The result of an in-flight Apply cannot arrive on the replacement
        // socket. Report it per field instead of dropping the write silently.
        // Same guard as everywhere else: never write over a field the user
        // retyped since Apply.
        for (const { field, input } of pendingWrites.values()) {
            if (inputs[field].value === input) errors[field].value = CONNECTION_LOST_ERROR
        }
        pendingWrites.clear()
    }))

    function start() {
        eventTarget.addEventListener(ORIGIN_SETTINGS_RESULT_EVENT, onResultEvent)
    }

    function stop() {
        eventTarget.removeEventListener(ORIGIN_SETTINGS_RESULT_EVENT, onResultEvent)
        pendingWrites.clear()
        for (const stopWatcher of stopWatchers) stopWatcher()
    }

    return {
        // Field-indexed handles, for callers that iterate (and for tests).
        originInputs: inputs,
        originErrors: displayErrors,
        applyOriginSetting: apply,
        onOriginInputChange: onInputChange,
        handleOriginSettingsResult: handleResult,
        seedOriginField: seedField,
        startOriginSettingsForm: start,
        stopOriginSettingsForm: stop,
        pendingOriginWriteCount: () => pendingWrites.size,

        // Flat handles, bound directly by the template.
        publicBaseUrlInput: inputs.publicBaseUrl,
        publicBaseUrlError: displayErrors.publicBaseUrl,
        publicBaseUrlApplyIcon: applyIcon.publicBaseUrl,
        onPublicBaseUrlInputChange: event => onInputChange('publicBaseUrl', event.target.value),
        onPublicBaseUrlApply: () => apply('publicBaseUrl'),

        shareBaseUrlInput: inputs.shareBaseUrl,
        shareBaseUrlError: displayErrors.shareBaseUrl,
        shareBaseUrlApplyIcon: applyIcon.shareBaseUrl,
        onShareBaseUrlInputChange: event => onInputChange('shareBaseUrl', event.target.value),
        onShareBaseUrlApply: () => apply('shareBaseUrl'),

        peerBaseUrlInput: inputs.peerBaseUrl,
        peerBaseUrlError: displayErrors.peerBaseUrl,
        peerBaseUrlWarning: displayPeerWarning,
        peerBaseUrlConfirmation,
        peerBaseUrlApplyIcon: applyIcon.peerBaseUrl,
        onPeerBaseUrlInputChange: event => onInputChange('peerBaseUrl', event.target.value),
        onPeerBaseUrlApply: () => apply('peerBaseUrl'),
        confirmPeerBaseUrlApply: () => apply('peerBaseUrl', true),
        cancelPeerBaseUrlApply,

        canPrefillPeerBaseUrl,
        prefillPeerBaseUrlFromPublic,
    }
}
