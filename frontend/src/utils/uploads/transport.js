// frontend/src/utils/uploads/transport.js
// Transfer constants and the classification of server answers (spec §5.5,
// §6.4 creation, §6.5 transfer). Pure.

export const UPLOAD_HEADER = 'X-Twicc-Upload'
export const TUS_VERSION = '1.0.0'
export const CHUNK_SIZE = 8 * 1024 * 1024
export const RETRY_DELAYS = Object.freeze([0, 1000, 3000, 5000, 10000, 20000, 30000, 60000])
export const CREATION_TIMEOUT_MS = 30_000
export const DISK_FULL_MESSAGE = 'Not enough disk space on the server.'

/**
 * The tus URL of one upload.
 *
 * @param {string} id
 * @returns {string}
 */
export function uploadUrl(id) {
    return `/api/uploads/${id}/`
}

/**
 * True when an answer comes from the upload code (`X-Twicc-Upload: 1`), not
 * from a proxy or a tunnel with the same status.
 *
 * @param {(name: string) => (string|null|undefined)} getHeader
 * @returns {boolean}
 */
export function isUploadAnswer(getHeader) {
    try {
        return getHeader(UPLOAD_HEADER) === '1'
    } catch {
        return false
    }
}

/**
 * Classify the answer of a creation `POST` (§6.4).
 *
 * @param {{status: number, getHeader: Function}|null} answer - null: no answer
 * @returns {'created'|'unauthorized'|'refused'|'unanswered'}
 */
export function classifyCreationAnswer(answer) {
    if (!answer) return 'unanswered'
    const { status } = answer
    if (status === 200 || status === 201) return 'created'
    if (status === 401) return 'unauthorized'
    const fromUpload = isUploadAnswer(answer.getHeader)
    if (fromUpload && ((status >= 400 && status < 500) || status === 507)) return 'refused'
    return 'unanswered'
}

/**
 * Status and origin of a tus-js-client error.
 *
 * @param {object} err - a tus `DetailedError` (or a plain Error)
 * @returns {{status: number, fromUpload: boolean, hasResponse: boolean}}
 */
export function tusErrorInfo(err) {
    const res = err?.originalResponse
    if (!res) return { status: 0, fromUpload: false, hasResponse: false }
    const status = res.getStatus()
    return { status, fromUpload: isUploadAnswer(name => res.getHeader(name)), hasResponse: true }
}

/**
 * True for a status the client retries (§6.5 `onShouldRetry`): no response;
 * `409`, `423`, `>= 500` except `507`; a `4xx` other than `401` and `404`
 * without `X-Twicc-Upload` (a proxy `408`, `413`, `429`).
 *
 * @param {{status: number, fromUpload: boolean, hasResponse: boolean}} info
 * @returns {boolean}
 */
export function isRetryableInfo({ status, fromUpload, hasResponse }) {
    if (!hasResponse || status === 0) return true
    if (status === 409 || status === 423) return true
    if (status >= 500) return status !== 507
    if (status >= 400 && status < 500) return status !== 401 && status !== 404 && !fromUpload
    return false
}

/**
 * The `onShouldRetry` option of tus-js-client.
 *
 * @param {object} err
 * @returns {boolean}
 */
export function tusShouldRetry(err) {
    return isRetryableInfo(tusErrorInfo(err))
}

/**
 * The `onError` table of §6.5: what to do after tus-js-client gave up.
 *
 * @param {object} err - a tus `DetailedError`
 * @param {object|null} record - the entry's current server record
 * @returns {{action: 'pause', reason: 'error'|'network', message: string|null}
 *          | {action: 'unauthorized'} | {action: 'reconcile'}}
 */
export function classifyTusError(err, record) {
    if (err?.originalRequest == null) {
        return { action: 'pause', reason: 'error', message: 'The transfer could not start.' }
    }
    const info = tusErrorInfo(err)
    const { status, fromUpload } = info
    if (status === 401) return { action: 'unauthorized' }
    if (status === 404 || status === 410 || status === 422) return { action: 'reconcile' }
    if (status === 507) return { action: 'pause', reason: 'error', message: DISK_FULL_MESSAGE }
    const retryable = isRetryableInfo(info)
    if (retryable && record?.state === 'active' && record.error) {
        return { action: 'pause', reason: 'error', message: record.error }
    }
    if (status === 500 && fromUpload) {
        return { action: 'pause', reason: 'error', message: errorMessageOf(err) || 'Server error.' }
    }
    if (retryable) return { action: 'pause', reason: 'network', message: null }
    return { action: 'pause', reason: 'error', message: errorMessageOf(err) || statusMessage(status) }
}


/**
 * The `error` field of a JSON error body carried by a tus error, if any.
 *
 * @param {object} err
 * @returns {string|null}
 */
export function errorMessageOf(err) {
    try {
        const body = err?.originalResponse?.getBody?.()
        if (!body) return null
        const parsed = JSON.parse(body)
        return typeof parsed?.error === 'string' ? parsed.error : null
    } catch {
        return null
    }
}

/**
 * A short text for an HTTP status without a readable reason.
 *
 * @param {number} status - 0 for no answer
 * @returns {string}
 */
export function statusMessage(status) {
    if (!status) return 'The server did not answer.'
    if (status === 507) return DISK_FULL_MESSAGE
    if (status === 410) return 'The upload no longer exists on the server.'
    if (status === 404) return 'The upload was not found on the server.'
    if (status === 409) return 'The server is busy with this upload. Try again later.'
    return `The server answered with status ${status}.`
}
