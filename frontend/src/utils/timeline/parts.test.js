import assert from 'node:assert/strict'
import test from 'node:test'

import { ARROW_LABEL, arrowPart, mergeTextParts, splitLastWord, textPart, titlePart } from './parts.js'

test('the part constructors', () => {
    assert.deepEqual(textPart('a'), { type: 'text', text: 'a' })
    assert.deepEqual(titlePart('T'), { type: 'title', text: 'T' })
    assert.deepEqual(arrowPart(), { type: 'arrow', label: ARROW_LABEL })
    assert.equal(ARROW_LABEL, 'to')
})

test('adjacent text parts are glued; other parts stay where they are', () => {
    assert.deepEqual(mergeTextParts([textPart('a'), textPart('b'), arrowPart(), textPart('c'), titlePart('T'), textPart('d'), textPart('e')]),
        [textPart('ab'), arrowPart(), textPart('c'), titlePart('T'), textPart('de')])
    assert.deepEqual(mergeTextParts([]), [])
})

test('splitLastWord takes the last word off the last text or title part, so a tag can be glued to it', () => {
    assert.deepEqual(splitLastWord([textPart('Renamed '), titlePart('Old'), arrowPart(), titlePart('New long title')]), {
        lead: [textPart('Renamed '), titlePart('Old'), arrowPart(), titlePart('New long ')], last: titlePart('title'),
    })
    assert.deepEqual(splitLastWord([textPart('Titled '), titlePart('One')]), { lead: [textPart('Titled ')], last: titlePart('One') })
    assert.deepEqual(splitLastWord([textPart('Title cleared')]), { lead: [textPart('Title ')], last: textPart('cleared') })
    // Trailing spaces stay with the word; a part that is not text (a mention, an arrow) or empty is left whole.
    assert.deepEqual(splitLastWord([textPart('Done  ')]), { lead: [], last: textPart('Done  ') })
    const mention = { type: 'session', mention: { title: 'x' } }
    assert.deepEqual(splitLastWord([textPart('by '), mention]), { lead: [textPart('by '), mention], last: null })
    assert.deepEqual(splitLastWord([textPart('')]), { lead: [textPart('')], last: null })
    assert.deepEqual(splitLastWord([]), { lead: [], last: null })
})
