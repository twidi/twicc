import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import { setTimeout as delay } from 'node:timers/promises'
import { SOUND_CONFIGS, getNotificationTones } from '../src/utils/notificationSoundDefinitions.js'

const repository = fileURLToPath(new URL('../../', import.meta.url))
const outputDirectory = join(repository, 'android/app/src/main/assets/notification-sounds')
const sourcePaths = [
    'frontend/scripts/generate-android-notification-sounds.mjs',
    'frontend/src/utils/notificationSoundDefinitions.js',
]
const format = { format: 1, sampleRate: 48000, channels: 1, bitsPerSample: 16 }
const endMargin = 0.05
const formatFingerprint = 'format=1;sampleRate=48000;channels=1;bitsPerSample=16;endMargin=0.05\n'
const soundIds = Object.keys(SOUND_CONFIGS)
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')
const framesFor = id => Math.ceil((Math.max(...getNotificationTones(id).map(tone => (tone.delay ?? 0) + tone.duration)) + endMargin) * format.sampleRate)

async function sourceFingerprints() {
    const sources = Object.fromEntries(await Promise.all(sourcePaths.map(async path => [path, sha256(await readFile(join(repository, path)))])))
    const sourceFingerprint = sha256(formatFingerprint + sourcePaths.map(path => `${sources[path]}  ${path}\n`).join(''))
    return { sources, sourceFingerprint }
}

/** Encode rendered mono samples using the canonical 44-byte PCM WAV header. */
export function encodePcm16Wav(samples, sampleRate) {
    const bytes = new Uint8Array(44 + samples.length * 2)
    const view = new DataView(bytes.buffer)
    const text = (offset, value) => [...value].forEach((character, index) => { bytes[offset + index] = character.charCodeAt(0) })
    text(0, 'RIFF')
    view.setUint32(4, bytes.length - 8, true)
    text(8, 'WAVE')
    text(12, 'fmt ')
    view.setUint32(16, 16, true)
    view.setUint16(20, 1, true)
    view.setUint16(22, 1, true)
    view.setUint32(24, sampleRate, true)
    view.setUint32(28, sampleRate * 2, true)
    view.setUint16(32, 2, true)
    view.setUint16(34, 16, true)
    text(36, 'data')
    view.setUint32(40, samples.length * 2, true)
    samples.forEach((sample, index) => {
        const clipped = Math.max(-1, Math.min(1, sample))
        view.setInt16(44 + index * 2, Math.round(clipped * (clipped < 0 ? 32768 : 32767)), true)
    })
    return bytes
}

/** Read-only verification. This path never starts a browser or writes outputs. */
export async function checkGeneratedSounds(outputDir) {
    const manifest = JSON.parse(await readFile(join(outputDir, 'manifest.json'), 'utf8'))
    const expectedSources = await sourceFingerprints()
    for (const [key, value] of Object.entries(format)) {
        if (manifest[key] !== value) throw new Error(`Invalid sound manifest ${key}`)
    }
    if (manifest.sourceFingerprint !== expectedSources.sourceFingerprint ||
        JSON.stringify(manifest.sources) !== JSON.stringify(expectedSources.sources)) {
        throw new Error('Stale Android notification sound sources; run npm run sounds:android:generate')
    }
    const expectedFiles = ['manifest.json', ...soundIds.map(id => `${id}.wav`)].sort()
    if (JSON.stringify((await readdir(outputDir)).sort()) !== JSON.stringify(expectedFiles)) {
        throw new Error('Missing or unexpected Android notification sound files')
    }
    if (!Array.isArray(manifest.entries) || manifest.entries.length !== soundIds.length) throw new Error('Invalid sound entries')
    for (const [index, id] of soundIds.entries()) {
        const entry = manifest.entries[index]
        const frames = framesFor(id)
        if (entry.id !== id || entry.file !== `${id}.wav` || entry.frames !== frames || frames <= 0) {
            throw new Error(`Invalid sound entry or duration: ${id}`)
        }
        const wav = await readFile(join(outputDir, entry.file))
        if (sha256(wav) !== entry.sha256) throw new Error(`Modified Android notification sound: ${id}`)
        if (wav.length !== 44 + frames * 2 || wav.toString('ascii', 0, 4) !== 'RIFF' ||
            wav.readUInt32LE(4) !== wav.length - 8 || wav.toString('ascii', 8, 16) !== 'WAVEfmt ' ||
            wav.readUInt32LE(16) !== 16 || wav.readUInt16LE(20) !== 1 || wav.readUInt16LE(22) !== 1 ||
            wav.readUInt32LE(24) !== format.sampleRate || wav.readUInt32LE(28) !== format.sampleRate * 2 ||
            wav.readUInt16LE(32) !== 2 || wav.readUInt16LE(34) !== 16 ||
            wav.toString('ascii', 36, 40) !== 'data' || wav.readUInt32LE(40) !== frames * 2 ||
            !wav.subarray(44).some(value => value !== 0)) {
            throw new Error(`Invalid or silent PCM16 WAV: ${id}`)
        }
    }
}

async function connectDevTools(url, signal) {
    const socket = new WebSocket(url)
    const pending = new Map()
    let nextId = 0
    const rejectPending = error => {
        for (const request of pending.values()) request.reject(error)
        pending.clear()
    }
    socket.addEventListener('message', event => {
        const message = JSON.parse(event.data)
        const request = pending.get(message.id)
        if (!request) return
        pending.delete(message.id)
        if (message.error) request.reject(new Error(message.error.message))
        else request.resolve(message.result)
    })
    socket.addEventListener('close', () => rejectPending(new Error('Chromium DevTools closed')))
    socket.addEventListener('error', () => rejectPending(new Error('Chromium DevTools failed')))
    const abort = () => {
        rejectPending(signal.reason)
        socket.close()
    }
    signal.addEventListener('abort', abort, { once: true })
    try {
        await new Promise((resolve, reject) => {
            socket.addEventListener('open', resolve, { once: true })
            socket.addEventListener('error', () => reject(new Error('Cannot connect to Chromium DevTools')), { once: true })
            signal.addEventListener('abort', () => reject(signal.reason), { once: true })
        })
    } catch (error) {
        signal.removeEventListener('abort', abort)
        socket.close()
        throw error
    }
    return {
        call(method, params) {
            signal.throwIfAborted()
            return new Promise((resolve, reject) => {
                const id = ++nextId
                pending.set(id, { resolve, reject })
                socket.send(JSON.stringify({ id, method, params }))
            })
        },
        close() {
            signal.removeEventListener('abort', abort)
            socket.close()
        },
    }
}

async function renderSounds(browser, scratchDir) {
    await mkdir(scratchDir, { recursive: true })
    const profile = await mkdtemp(join(scratchDir, 'chromium-profile-'))
    const deadline = AbortSignal.timeout(60000)
    let browserError
    let diagnostics = ''
    const child = spawn(browser, [
        '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
        '--disable-background-networking', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
        `--user-data-dir=${profile}`, 'about:blank',
    ], { stdio: ['ignore', 'ignore', 'pipe'] })
    child.stderr.on('data', bytes => { diagnostics = (diagnostics + bytes.toString()).slice(-4000) })
    child.on('error', error => { browserError = error })
    const exited = new Promise(resolve => child.once('close', resolve))
    let devTools
    try {
        let port
        while (!port) {
            deadline.throwIfAborted()
            if (browserError) throw browserError
            if (child.exitCode !== null || child.signalCode !== null) throw new Error(`Chromium exited: ${diagnostics}`)
            try {
                port = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0])
            } catch (error) {
                if (error.code !== 'ENOENT') throw error
            }
            if (!port) await delay(50, undefined, { signal: deadline })
        }
        const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`, { signal: deadline })).json()
        const target = targets.find(target => target.type === 'page')
        if (!target) throw new Error('Chromium has no rendering page')
        devTools = await connectDevTools(target.webSocketDebuggerUrl, deadline)
        const source = await readFile(join(repository, sourcePaths[1]), 'utf8')
        const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
        const renders = []
        for (const id of soundIds) {
            const frames = framesFor(id)
            const expression = `(async () => {
                const { scheduleNotificationSound } = await import(${JSON.stringify(moduleUrl)});
                const context = new OfflineAudioContext(1, ${frames}, ${format.sampleRate});
                scheduleNotificationSound(context, ${JSON.stringify(id)}, 0);
                const audio = await context.startRendering();
                return Array.from(audio.getChannelData(0));
            })()`
            const result = await devTools.call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
            if (result.exceptionDetails) throw new Error(`Offline render failed for ${id}: ${JSON.stringify(result.exceptionDetails)}`)
            const samples = result.result.value
            if (!Array.isArray(samples) || samples.length !== frames || !samples.every(Number.isFinite) || !samples.some(value => value !== 0)) {
                throw new Error(`Invalid offline render: ${id}`)
            }
            renders.push({ id, frames, wav: encodePcm16Wav(Float32Array.from(samples), format.sampleRate) })
        }
        return renders
    } finally {
        devTools?.close()
        // Only this child belongs to the generator. Never kill a user's browser.
        if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM')
        await Promise.race([exited, delay(2000)])
        if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
        await exited
        await rm(profile, { recursive: true, force: true })
    }
}

async function main() {
    const { values } = parseArgs({ options: {
        browser: { type: 'string', default: '/usr/bin/google-chrome' },
        'scratch-dir': { type: 'string' },
        check: { type: 'boolean', default: false },
    } })
    if (values.check) {
        await checkGeneratedSounds(outputDirectory)
        console.log('Verified 10 Android notification sounds')
        return
    }
    if (!values['scratch-dir']) throw new Error('Generation requires --scratch-dir outside the repository')
    const scratchDir = resolve(values['scratch-dir'])
    if (scratchDir === repository || scratchDir.startsWith(repository + '/')) throw new Error('Use a scratch directory outside the repository')
    const renders = await renderSounds(values.browser, scratchDir)
    const manifest = { ...format, ...await sourceFingerprints(), entries: renders.map(({ id, frames, wav }) => ({ id, file: `${id}.wav`, frames, sha256: sha256(wav) })) }
    // Publish only after every OfflineAudioContext render succeeds.
    await mkdir(outputDirectory, { recursive: true })
    for (const { id, wav } of renders) await writeFile(join(outputDirectory, `${id}.wav`), wav)
    await writeFile(join(outputDirectory, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
    await checkGeneratedSounds(outputDirectory)
    console.log('Generated 10 Android notification sounds')
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
    main().catch(error => {
        console.error(error.message)
        process.exitCode = 1
    })
}
