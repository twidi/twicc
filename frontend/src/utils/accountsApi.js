// frontend/src/utils/accountsApi.js
//
// Thin client for the multi-user admin API (/api/admin/…). Every call resolves to
// `{ ok, data, error }`: `error` is a render-time UI message descriptor (resolve it
// with resolveUiMessage), never a translated string — it may stay on screen across
// a language switch.

import { apiFetch } from './api'
import { remoteUiMessage } from '../i18n/remoteMessages.js'
import { localUiMessage } from '../i18n/uiMessages.js'

async function request(url, { method = 'GET', body } = {}) {
    try {
        const response = await apiFetch(url, {
            method,
            headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
            body: body !== undefined ? JSON.stringify(body) : undefined,
        })
        let data = null
        try { data = await response.json() } catch { data = null }
        if (!response.ok) {
            return { ok: false, data, error: remoteUiMessage(data, data?.error || localUiMessage('accounts.errors.generic')) }
        }
        return { ok: true, data, error: null }
    } catch {
        return { ok: false, data: null, error: localUiMessage('accounts.errors.network') }
    }
}

export const listUsers = () => request('/api/admin/users/')
export const createUser = (payload) => request('/api/admin/users/', { method: 'POST', body: payload })
export const updateUser = (id, payload) => request(`/api/admin/users/${id}/`, { method: 'PATCH', body: payload })
export const deleteUser = (id) => request(`/api/admin/users/${id}/`, { method: 'DELETE' })
export const revokeUserSessions = (id) => request(`/api/admin/users/${id}/revoke-sessions/`, { method: 'POST' })
export const saveUserGrants = (id, grants) => request(`/api/admin/users/${id}/grants/`, { method: 'PUT', body: grants })

/** `kind` is 'project' or 'workspace'. */
export function getTargetAccess(kind, targetId) {
    return request(`/api/admin/${kind}s/${encodeURIComponent(targetId)}/access/`)
}

/** Set (level) or remove (null) one account's direct grant on a project/workspace. */
export function setTargetAccess(kind, targetId, userId, level) {
    return request(`/api/admin/${kind}s/${encodeURIComponent(targetId)}/access/${userId}/`, {
        method: 'PUT',
        body: { level: level || null },
    })
}
