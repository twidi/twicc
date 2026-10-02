import test from 'node:test'
import assert from 'node:assert/strict'

import { resolvePublicAssetUrl } from './publicAsset.js'

test('resolves a public path under the base URL, with or without the public prefix', () => {
    const expected = resolvePublicAssetUrl('icons/robot.svg')
    assert.equal(resolvePublicAssetUrl('frontend/public/icons/robot.svg'), expected)
    assert.match(expected, /^\/icons\/robot\.svg$/)
})

test('appends the build version as a cache-buster when defined', async () => {
    globalThis.__PUBLIC_ASSETS_VERSION__ = '123'
    try {
        assert.equal(resolvePublicAssetUrl('icons/robot.svg'), '/icons/robot.svg?v=123')
    } finally {
        delete globalThis.__PUBLIC_ASSETS_VERSION__
    }
})
