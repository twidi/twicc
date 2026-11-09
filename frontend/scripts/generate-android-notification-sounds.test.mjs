import test from 'node:test'
import assert from 'node:assert/strict'
import { cp, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { encodePcm16Wav, checkGeneratedSounds, resolveScratchDirectory } from './generate-android-notification-sounds.mjs'

const assets = fileURLToPath(new URL('../../android/app/src/main/assets/notification-sounds/', import.meta.url))
const scratch = tmpdir()
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')

test('scratch guard rejects the repository root and descendants', () => {
    const repository = fileURLToPath(new URL('../../', import.meta.url))
    for (const path of [repository, join(repository, 'frontend/internal-scratch'), join(repository, 'frontend/..')]) {
        assert.throws(() => resolveScratchDirectory(path), /outside the repository/, path)
    }
})

test('scratch guard accepts sibling and outside paths without creating them', () => {
    const repository = fileURLToPath(new URL('../../', import.meta.url)).replace(/\/$/, '')
    for (const path of [repository + '-scratch', join(repository, '../session-scratch'), join(tmpdir(), 'sound-scratch')]) {
        assert.equal(resolveScratchDirectory(path), resolve(path))
    }
})

test('encodes mono PCM16 WAV with correct header, clipping and signed samples', () => {
    const wav = Buffer.from(encodePcm16Wav(new Float32Array([-2, -1, -0.5, 0, 0.5, 1, 2]), 48000))
    assert.equal(wav.toString('ascii', 0, 4), 'RIFF')
    assert.equal(wav.readUInt32LE(4), wav.length - 8)
    assert.equal(wav.toString('ascii', 8, 12), 'WAVE')
    assert.equal(wav.toString('ascii', 12, 16), 'fmt ')
    assert.equal(wav.readUInt32LE(16), 16)
    assert.equal(wav.readUInt16LE(20), 1)
    assert.equal(wav.readUInt16LE(22), 1)
    assert.equal(wav.readUInt32LE(24), 48000)
    assert.equal(wav.readUInt32LE(28), 96000)
    assert.equal(wav.readUInt16LE(32), 2)
    assert.equal(wav.readUInt16LE(34), 16)
    assert.equal(wav.toString('ascii', 36, 40), 'data')
    assert.equal(wav.readUInt32LE(40), 14)
    assert.deepEqual(Array.from({ length: 7 }, (_, index) => wav.readInt16LE(44 + index * 2)),
        [-32768, -32768, -16384, 0, 16384, 32767, 32767])
})

test('committed sounds have exact durations and non-silent mono 48 kHz PCM16 samples', async () => {
    await checkGeneratedSounds(assets)
    const manifest = JSON.parse(await readFile(join(assets, 'manifest.json')))
    assert.equal(manifest.format, 1)
    assert.equal(manifest.sampleRate, 48000)
    assert.equal(manifest.channels, 1)
    assert.equal(manifest.bitsPerSample, 16)
    // Ceil retains the full end margin, including floating-point sample boundaries.
    const frames = { beep: 9600, chime: 26401, ping: 7201, pop: 8640, bloop: 11040, tink: 11040, knock: 7440, sweep: 12960, signal: 15840, ripple: 13200 }
    assert.equal(manifest.entries.length, 10)
    for (const entry of manifest.entries) {
        assert.equal(entry.frames, frames[entry.id], entry.id)
        const wav = await readFile(join(assets, entry.file))
        assert.equal(wav.readUInt32LE(40) / 2, entry.frames)
        assert.ok(wav.subarray(44).some(value => value !== 0), entry.id)
        assert.equal(sha256(wav), entry.sha256)
    }
    assert.equal((await readdir(assets)).includes('none.wav'), false)
})

const mutations = {
    missing: directory => rm(join(directory, 'beep.wav')),
    extra: directory => writeFile(join(directory, 'none.wav'), 'unexpected'),
    modified: directory => writeFile(join(directory, 'beep.wav'), 'corrupt'),
    stale: async directory => {
        const path = join(directory, 'manifest.json')
        const manifest = JSON.parse(await readFile(path))
        manifest.sourceFingerprint = '0'.repeat(64)
        await writeFile(path, JSON.stringify(manifest))
    },
    duration: async directory => {
        const path = join(directory, 'manifest.json')
        const manifest = JSON.parse(await readFile(path))
        manifest.entries[0].frames += 1
        await writeFile(path, JSON.stringify(manifest))
    },
    structure: async directory => {
        const wavPath = join(directory, 'beep.wav')
        const wav = await readFile(wavPath)
        wav.writeUInt16LE(2, 22)
        await writeFile(wavPath, wav)
        const path = join(directory, 'manifest.json')
        const manifest = JSON.parse(await readFile(path))
        manifest.entries[0].sha256 = sha256(wav)
        await writeFile(path, JSON.stringify(manifest))
    },
    duplicate: async directory => {
        const path = join(directory, 'manifest.json')
        const manifest = JSON.parse(await readFile(path))
        manifest.entries[1] = manifest.entries[0]
        await writeFile(path, JSON.stringify(manifest))
    },
}
for (const [name, mutate] of Object.entries(mutations)) {
    test(`check rejects ${name} assets without modifying the fixture`, async () => {
        const directory = await mkdtemp(join(scratch, 'sound-check-'))
        try {
            await cp(assets, directory, { recursive: true })
            await mutate(directory)
            const before = await readFile(join(directory, 'manifest.json'))
            await assert.rejects(checkGeneratedSounds(directory))
            assert.deepEqual(await readFile(join(directory, 'manifest.json')), before)
        } finally {
            await rm(directory, { recursive: true, force: true })
        }
    })
}

test('check CLI needs neither Chromium nor a scratch directory', () => {
    const result = spawnSync(process.execPath, [fileURLToPath(new URL('./generate-android-notification-sounds.mjs', import.meta.url)), '--check', '--browser', '/does-not-exist'], { encoding: 'utf8' })
    assert.equal(result.status, 0, result.stderr)
})

test('failed browser launch leaves committed assets unchanged and removes its profile', async () => {
    const directory = await mkdtemp(join(scratch, 'sound-launch-'))
    try {
        const before = await readFile(join(assets, 'manifest.json'))
        const result = spawnSync(process.execPath, [
            fileURLToPath(new URL('./generate-android-notification-sounds.mjs', import.meta.url)),
            '--browser', join(directory, 'missing-browser'), '--scratch-dir', directory,
        ], { encoding: 'utf8', timeout: 10000 })
        assert.equal(result.status, 1, result.stderr)
        assert.match(result.stderr, /ENOENT/)
        assert.deepEqual(await readdir(directory), [])
        assert.deepEqual(await readFile(join(assets, 'manifest.json')), before)
        await checkGeneratedSounds(assets)
    } finally {
        await rm(directory, { recursive: true, force: true })
    }
})
