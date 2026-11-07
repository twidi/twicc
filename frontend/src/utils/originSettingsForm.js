import { i18n } from '../i18n/index.js'

// Pure per-field preparation for the three origin settings.
// Python owns validation, relationships, and canonical output.

import { checkPublicOriginInput, usablePublicOrigin } from './publicOrigin.js'

export const ORIGIN_SETTING_KEYS = ['publicBaseUrl', 'shareBaseUrl', 'peerBaseUrl']

export const PUBLIC_ORIGIN_ERROR = 'Enter a hostname or an HTTP(S) origin without a path, query, or fragment.'

export function publicOriginErrorMessage(error) {
    const code = error?.replace(/^invalid_origin_/, '')
    if (code === 'scheme') return i18n.global.t('settings.origins.errors.scheme')
    if (code === 'credentials') return i18n.global.t('settings.origins.errors.credentials')
    if (code === 'location_hostname') return i18n.global.t('settings.origins.errors.locationHostname')
    if (code === 'retained_stored_value') return i18n.global.t('settings.origins.errors.stored')
    if (code === 'origin_conflict_share_external_hostname') return i18n.global.t('settings.origins.errors.shareExternal')
    if (code === 'origin_conflict_share_peer_hostname') return i18n.global.t('settings.origins.errors.sharePeer')
    if (code === 'origin_conflict_ambiguous_authority') return i18n.global.t('settings.origins.errors.peerExternal')
    return i18n.global.t('settings.origins.errors.invalid')
}

export function originSettingErrorMessage(errors, field, messageFor) {
    return errors
        .filter(error => error.field === field)
        .map(error => knownOriginError(error.code) ? messageFor(error.code) : error.message || messageFor(error.code))
        .join(' ')
}

export function refreshOriginInput(currentInput, previousStored, nextStored) {
    return currentInput === (previousStored || '') ? (nextStored || '') : currentInput
}

export function discardOriginSettingWrites(pendingWrites, field) {
    for (const [requestId, write] of pendingWrites) {
        if (write.field === field) pendingWrites.delete(requestId)
    }
}

export function resolveOriginSettingResult(pendingWrites, payload, currentInput) {
    if (!payload || !['accepted', 'rejected'].includes(payload.status)) return null
    const write = pendingWrites.get(payload.request_id)
    if (!write) return null
    pendingWrites.delete(payload.request_id)
    if (currentInput !== write.input) return null
    const value = payload.settings?.[write.field]
    if (payload.status === 'accepted' && typeof value !== 'string') return null
    return {
        field: write.field,
        status: payload.status,
        value: payload.status === 'accepted' ? value : null,
        errors: payload.status === 'rejected' && Array.isArray(payload.errors) ? payload.errors : [],
    }
}

// `window.location.hostname` brackets an IPv6 literal (`[::1]`), while the
// parsed hint is the bare hostname (`::1`) — the same shape Python's
// `PublicOriginResult.hostname` carries. Compared as-is, the Share-vs-current
// host rule could never match on an IPv6 host. Strip the brackets on the
// browser side only: the bare form stays the contract.
function bareHostname(value) {
    return value.toLowerCase().replace(/^\[(.*)\]$/, '$1')
}

export function validateOriginSetting({ field, input, stored, locationHostname }) {
    if (!ORIGIN_SETTING_KEYS.includes(field)) {
        return { errors: [{ field, code: 'unknown_field' }], warning: null, patch: {} }
    }
    const checked = checkPublicOriginInput(input)
    if (checked.error) {
        return { errors: [{ field, code: checked.error }], warning: null, patch: {} }
    }
    if (field === 'shareBaseUrl' && checked.hostname && locationHostname
            && checked.hostname === bareHostname(locationHostname)) {
        return { errors: [{ field, code: 'location_hostname' }], warning: null, patch: {} }
    }
    const warning = field === 'peerBaseUrl' && checked.scheme === 'http' ? 'http' : null
    const patch = checked.value === (stored[field] || '') ? {} : { [field]: checked.value }
    const errors = !Object.keys(patch).length && checked.value && !usablePublicOrigin(checked.value)
        ? [{ field, code: 'retained_stored_value' }]
        : []
    return { errors, warning, patch }
}

function knownOriginError(code) {
    return typeof code === 'string' && (code.startsWith('invalid_origin_') || code.startsWith('origin_conflict_') || ['scheme', 'credentials', 'location_hostname', 'retained_stored_value'].includes(code))
}
