import { streamPublicationRegistry } from './streamPublicationRegistry.js'

/**
 * Adaptive-rate text buffer for streaming blocks.
 *
 * Accumulates incoming deltas and drains them at a rate derived from
 * recent delta arrival patterns, producing smooth character-by-character
 * output via requestAnimationFrame.
 *
 * Rate estimation: a sliding window of the last N deltas records their
 * arrival time and character count. The drain rate (chars/ms) is
 * totalChars / timeSpan, so the buffer empties at roughly the same
 * pace as new text arrives — no visible pauses between deltas.
 */

const WINDOW_SIZE = 5    // number of recent deltas for rate calculation
const MAX_DT = 100       // cap frame dt (ms) to prevent jumps after tab switch
const DEFAULT_RATE = 200 / 1000  // 200 chars/s → 0.2 chars/ms (used for first delta)

class BlockBuffer {
    /**
     * @param {(displayedText: string) => void} onDrain
     *   Called each frame with the text to display (growing substring).
     */
    constructor(onDrain, { messageId = null, publicationIdentity = null, active = false } = {}) {
        this.messageId = messageId
        this.publicationIdentity = publicationIdentity
        this.active = active
        this.requestToken = null
        this.onDrain = onDrain
        this.fullText = ''
        this.displayedLength = 0
        this.deltaHistory = []      // [{ time, chars }]
        this.rateCharsPerMs = 0
        this.fractionalChars = 0
        this.rafId = null
        this.lastFrameTime = null
        this.firstDelta = true
    }

    pushDelta(text) {
        const now = performance.now()
        this.fullText += text
        if (!this.active) return

        if (this.firstDelta) {
            this.firstDelta = false
            this.deltaHistory.push({ time: now, chars: text.length })
            this.rateCharsPerMs = DEFAULT_RATE

            if (this.rafId === null) {
                this.lastFrameTime = now
                this._scheduleFrame()
            }
            return
        }

        this.deltaHistory.push({ time: now, chars: text.length })
        if (this.deltaHistory.length > WINDOW_SIZE) {
            this.deltaHistory.shift()
        }

        this._updateRate()

        if (this.rafId === null) {
            this.lastFrameTime = now
            this._scheduleFrame()
        }
    }

    _updateRate() {
        if (this.deltaHistory.length < 2) return

        const first = this.deltaHistory[0]
        const last = this.deltaHistory[this.deltaHistory.length - 1]
        const elapsed = last.time - first.time
        if (elapsed <= 0) return

        const totalChars = this.deltaHistory.reduce((sum, d) => sum + d.chars, 0)
        this.rateCharsPerMs = totalChars / elapsed
    }

    _scheduleFrame() {
        const token = {}
        this.requestToken = token
        this.rafId = requestAnimationFrame((time) => {
            if (this.requestToken !== token || !this.active) return
            this._onFrame(time)
        })
    }

    _onFrame(time) {
        const dt = Math.min(time - this.lastFrameTime, MAX_DT)
        this.lastFrameTime = time

        const remaining = this.fullText.length - this.displayedLength
        if (remaining <= 0) {
            this.rafId = null
            return
        }

        this.fractionalChars += this.rateCharsPerMs * dt
        let charsToShow = Math.floor(this.fractionalChars)
        this.fractionalChars -= charsToShow

        if (charsToShow < 1) charsToShow = 1
        charsToShow = Math.min(charsToShow, remaining)

        this.displayedLength += charsToShow
        this.onDrain(this.fullText.substring(0, this.displayedLength))

        if (this.displayedLength < this.fullText.length) {
            this._scheduleFrame()
        } else {
            this.rafId = null
        }
    }

    cancel() {
        this.requestToken = null
        if (this.rafId !== null) cancelAnimationFrame(this.rafId)
        this.rafId = null
    }

    resetRate() {
        this.deltaHistory = []
        this.fractionalChars = 0
        this.lastFrameTime = null
        this.rateCharsPerMs = 0
        this.firstDelta = true
    }

    snapshot(publish = true) {
        this.cancel()
        const changed = this.displayedLength !== this.fullText.length
        this.displayedLength = this.fullText.length
        this.resetRate()
        if (changed && publish) this.onDrain(this.fullText)
    }

    setActive(active) {
        if (this.active === active) return
        this.active = active
        if (active) this.snapshot()
        else this.cancel()
    }

    flush() {
        this.snapshot(this.active)
        return this.fullText
    }

    destroy() {
        this.active = false
        this.cancel()
        this.releaseBinding?.()
        this.releaseBinding = null
    }
}

// ── Registry ────────────────────────────────────────────────────────

const buffers = new Map()  // "sessionId:blockIndex" → BlockBuffer

function key(sessionId, blockIndex) {
    return `${sessionId}:${blockIndex}`
}

/**
 * Create a new buffer for a streaming block.
 * @param {string} sessionId
 * @param {number} blockIndex
 * @param {(displayedText: string) => void} onDrain
 */
export function initBuffer(sessionId, blockIndex, onDrain, options = {}) {
    const k = key(sessionId, blockIndex)
    const existing = buffers.get(k)
    if (existing) existing.destroy()
    buffers.delete(k)
    const { messageId = null, publicationIdentity = null, visibilityManaged = true } = options
    if (visibilityManaged && (!publicationIdentity || publicationIdentity.sessionId !== sessionId ||
        publicationIdentity.messageId !== messageId || publicationIdentity.blockIndex !== blockIndex)) {
        throw new Error('Managed streaming buffer requires a matching publication identity')
    }
    const buf = new BlockBuffer(onDrain, { ...options, active: visibilityManaged ? false : options.active ?? false })
    buffers.set(k, buf)
    if (visibilityManaged) {
        buf.releaseBinding = streamPublicationRegistry.bindBlock(publicationIdentity, {
            setActive(active) { if (buffers.get(k) === buf) buf.setActive(active) },
            snapshot() { if (buffers.get(k) === buf) buf.snapshot() },
        })
    }
}

/** Feed a delta into the buffer. */
export function feedDelta(sessionId, blockIndex, text) {
    const buf = buffers.get(key(sessionId, blockIndex))
    if (buf) buf.pushDelta(text)
}

/** Flush remaining buffer and destroy it. */
export function flushBuffer(sessionId, blockIndex) {
    const k = key(sessionId, blockIndex)
    const buf = buffers.get(k)
    if (buf) {
        const text = buf.flush()
        buf.destroy()
        buffers.delete(k)
        return text
    }
    return null
}

/** Destroy all buffers for a session. */
export function destroySessionBuffers(sessionId) {
    const prefix = sessionId + ':'
    for (const [k, buf] of buffers) {
        if (k.startsWith(prefix)) {
            buf.destroy()
            buffers.delete(k)
        }
    }
}

/** Destroy all buffers (e.g. on WebSocket reconnect). */
export function destroyAllBuffers() {
    for (const buf of buffers.values()) {
        buf.destroy()
    }
    buffers.clear()
}

function matchingBuffer(sessionId, messageId, blockIndex, publicationIdentity = null) {
    const buf = buffers.get(key(sessionId, blockIndex))
    return buf && buf.messageId === messageId && (!publicationIdentity || buf.publicationIdentity === publicationIdentity) ? buf : null
}

export function setBufferActive(sessionId, messageId, blockIndex, active) {
    const buf = matchingBuffer(sessionId, messageId, blockIndex)
    if (!buf) return false
    buf.setActive(active)
    return true
}

export function snapshotBuffer(sessionId, messageId, blockIndex) {
    const buf = matchingBuffer(sessionId, messageId, blockIndex)
    if (!buf) return false
    buf.snapshot()
    return true
}

export function isBufferActive(sessionId, messageId, blockIndex, publicationIdentity = null) {
    return !!matchingBuffer(sessionId, messageId, blockIndex, publicationIdentity)?.active
}
