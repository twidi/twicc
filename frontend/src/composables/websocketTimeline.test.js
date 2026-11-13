import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

// Execute the actual dispatcher branch; the full module needs the browser app.
const source = readFileSync(new URL('./useWebSocket.js', import.meta.url), 'utf8')
const start = source.indexOf("case 'timeline_changed':")
const end = source.indexOf("case 'upload_state':", start)
const dispatch = new Function('window', 'msg', `switch (msg.type) { ${source.slice(start, end)} }`)

test('unchanged latest ID still forwards final resolution to waiting readers', () => {
    const target = new EventTarget()
    const details = []
    target.addEventListener('twicc:timeline-changed', (event) => details.push(event.detail))
    dispatch(target, { type: 'timeline_changed', latest_id: 42 })
    dispatch(target, { type: 'timeline_changed', latest_id: 42, finalized_ending_ids: [12, 31] })
    assert.deepEqual(details, [{ latestId: 42 }, { latestId: 42, finalizedEndingIds: [12, 31] }])
})
