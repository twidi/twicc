import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveContentAssetUrl } from './contentAssets.js'

test('localized bodies resolve all shared image path forms before the build-version query', () => {
    globalThis.__PUBLIC_ASSETS_VERSION__ = 'build-42'
    try {
        for (const path of ['screenshot.webp', './screenshot.webp', '/help/screenshot.webp']) {
            assert.equal(resolveContentAssetUrl(path, 'help'), '/help/screenshot.webp?v=build-42')
        }
        assert.equal(resolveContentAssetUrl('screenshot.webp', 'tips'), '/tips/screenshot.webp?v=build-42')
    } finally { delete globalThis.__PUBLIC_ASSETS_VERSION__ }
})

test('preserves external links, data images, fragments and other absolute application paths', () => {
    for (const path of ['https://example.com/image.png', '//example.com/image.png', 'data:image/png;base64,abc', '#section', '/projects']) {
        assert.equal(resolveContentAssetUrl(path, 'help'), path)
    }
    assert.equal(resolveContentAssetUrl('./screenshot.webp', 'help'), '/help/screenshot.webp')
})
