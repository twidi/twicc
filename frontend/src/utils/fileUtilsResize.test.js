import { test } from 'node:test'
import assert from 'node:assert/strict'
import { resizeMediasForSend } from './fileUtils.js'

const image = (id, data = 'raw') => ({ id, type: 'image', mimeType: 'image/png', data })
const pdf = { id: 'p', type: 'pdf', mimeType: 'application/pdf', data: 'pdf' }

function fakeHelpers(dim) {
    const calls = []
    return {
        calls,
        getEffectiveImageDimension(args) {
            calls.push(args)
            return dim
        },
    }
}

test('returns the medias untouched when the provider asks for no resize', async () => {
    const medias = [image('a')]
    const resize = async () => assert.fail('must not resize')
    assert.equal(await resizeMediasForSend(medias, fakeHelpers(null), 'm', resize), medias)
})

test('passes model and image count to the helper and resizes only images', async () => {
    const helpers = fakeHelpers(1568)
    const seen = []
    const resize = async (data, mimeType, dim) => {
        seen.push(dim)
        return { data: `${data}-small`, mimeType }
    }
    const out = await resizeMediasForSend([image('a'), pdf, image('b')], helpers, 'sonnet', resize)
    assert.deepEqual(helpers.calls, [{ model: 'sonnet', numImages: 2 }])
    assert.deepEqual(seen, [1568, 1568])
    assert.equal(out[0].data, 'raw-small')
    assert.equal(out[1], pdf)
    assert.equal(out[2].data, 'raw-small')
})

test('keeps the same media object when the resize changes nothing', async () => {
    const media = image('a')
    const out = await resizeMediasForSend([media], fakeHelpers(2000), 'm', async (d, t) => ({ data: d, mimeType: t }))
    assert.equal(out[0], media)
})

test('tolerates missing helpers', async () => {
    const medias = [image('a')]
    assert.equal(await resizeMediasForSend(medias, null, 'm'), medias)
})
