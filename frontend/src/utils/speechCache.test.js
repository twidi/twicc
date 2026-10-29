import test from 'node:test'
import assert from 'node:assert/strict'
import { SpeechCache } from './speechCache.js'

test('reading an entry does not extend its lifetime', () => {
    let t = 0
    const cache = new SpeechCache({ ttlMs: 1000, now: () => t })
    cache.set('a', new Blob(['x']))
    t = 900; assert.ok(cache.get('a'))
    t = 1000; assert.equal(cache.get('a'), null)
})

test('a read releases every expired entry', () => {
    let t = 0
    const cache = new SpeechCache({ ttlMs: 1000, now: () => t })
    cache.set('a', new Blob(['xx']))
    cache.set('b', new Blob(['yyy']))
    assert.equal(cache.totalBytes, 5)
    t = 1000
    assert.equal(cache.get('a'), null)
    assert.equal(cache.totalBytes, 0)
})

test('cache stores and returns a blob', () => {
    const cache = new SpeechCache()
    const blob = new Blob(['abc'])
    assert.equal(cache.set('a', blob), true)
    assert.equal(cache.get('a'), blob)
    assert.equal(cache.get('missing'), null)
    assert.equal(cache.totalBytes, 3)
})

test('cache expires entries after the TTL', () => {
    let t = 0
    const cache = new SpeechCache({ ttlMs: 1000, now: () => t })
    cache.set('a', new Blob(['x']))
    t = 999; assert.ok(cache.get('a'))
    t = 2000; assert.equal(cache.get('a'), null)
    assert.equal(cache.totalBytes, 0)
})

test('cache evicts least recently used to fit the size cap', () => {
    const cache = new SpeechCache({ maxBytes: 10 })
    cache.set('first', new Blob(['1111']))
    cache.set('second', new Blob(['2222']))
    assert.ok(cache.get('first'))
    cache.set('third', new Blob(['3333']))
    assert.ok(cache.get('first'))
    assert.equal(cache.get('second'), null)
    assert.ok(cache.get('third'))
    assert.equal(cache.totalBytes, 8)
})

test('cache replaces an entry stored under the same key', () => {
    const cache = new SpeechCache({ maxBytes: 10 })
    cache.set('a', new Blob(['1111']))
    const replacement = new Blob(['22'])
    cache.set('a', replacement)
    assert.equal(cache.get('a'), replacement)
    assert.equal(cache.totalBytes, 2)
})

test('cache refuses an audio larger than the cap', () => {
    const cache = new SpeechCache({ maxBytes: 3 })
    assert.equal(cache.set('big', new Blob(['1234'])), false)
    assert.equal(cache.get('big'), null)
    assert.equal(cache.totalBytes, 0)
})

test('cache accepts an audio exactly at the cap and clear empties it', () => {
    const cache = new SpeechCache({ maxBytes: 4 })
    assert.equal(cache.set('a', new Blob(['1234'])), true)
    cache.clear()
    assert.equal(cache.get('a'), null)
    assert.equal(cache.totalBytes, 0)
})

test('cache defaults to 50 MB and 10 minutes', () => {
    let t = 0
    const cache = new SpeechCache({ now: () => t })
    assert.equal(cache.set('a', new Blob(['x'])), true)
    t = 10 * 60 * 1000 - 1; assert.ok(cache.get('a'))
    t = 10 * 60 * 1000; assert.equal(cache.get('a'), null)
    assert.equal(cache.set('big', { size: 50 * 1024 * 1024 + 1 }), false)
})
