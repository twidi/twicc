import { createHash } from 'node:crypto'
import { readFile, readdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const fingerprintPath = 'android/app/src/main/assets/i18n/sources.sha256'
const sourceRoots = ['android/app/src/main', 'android/app/src/gecko', 'android/app/src/webview', 'android/policy/src/main']

// This is a direct-literal guard, not a Kotlin parser or data-flow analysis.
// Tokenization removes comments and keeps strings atomic, including raw strings.
function tokenize(source) {
    const tokens = []
    const pattern = /\/\/[^\n]*|\/\*[\s\S]*?\*\/|"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[A-Za-z_][\w]*|\s+|./g
    for (const match of source.matchAll(pattern)) {
        const value = match[0]
        if (/^\s|^\/\//.test(value) || value.startsWith('/*')) continue
        tokens.push({ value, offset: match.index, string: value.startsWith('"') })
    }
    return tokens
}

/** Return obvious unlocalized presentation literals with source locations. */
export function scanKotlinSource(source) {
    const tokens = tokenize(source), failures = []
    const report = token => {
        if (!token?.string || !/[\p{L}]/u.test(token.value)) return
        failures.push({ line: source.slice(0, token.offset).split('\n').length, text: token.value })
    }
    const presentation = new Set(['text', 'hint', 'contentDescription', 'description', 'title', 'message', 'error'])
    const firstArg = new Set(['setText', 'setHint', 'setContentDescription', 'setTitle', 'setMessage',
        'setPositiveButton', 'setNegativeButton', 'setNeutralButton', 'setContentTitle', 'setContentText',
        'setSubText', 'setDescription', 'toast'])
    for (let index = 0; index < tokens.length; index++) {
        const token = tokens[index]
        if (presentation.has(token.value) && tokens[index + 1]?.value === '=') report(tokens[index + 2])
        if (tokens[index + 1]?.value !== '(') continue
        let depth = 0, end = index + 2
        const args = [], starts = [index + 2]
        for (; end < tokens.length; end++) {
            const value = tokens[end].value
            if (value === ')' && depth === 0) break
            if (['(', '[', '{'].includes(value)) depth++
            if ([')', ']', '}'].includes(value)) depth--
            if (value === ',' && depth === 0) starts.push(end + 1)
        }
        if (end === tokens.length) continue
        for (let n = 0; n < starts.length; n++) args.push(tokens.slice(starts[n], (starts[n + 1] ?? end + 1) - 1))
        const literal = n => report(args[n]?.[0])
        const call = tokens.slice(index + 2, end)
        if (firstArg.has(token.value)) literal(0)
        if (token.value === 'makeText' && tokens[index - 2]?.value === 'Toast') literal(1)
        if (token.value === 'NotificationChannel') literal(1)
        if (token.value === 'Builder' && tokens[index - 2]?.value === 'Action') literal(1)
        if (token.value === 'addAction') literal(1)
        if (token.value === 'add' && tokens[index - 2]?.value?.toLowerCase().includes('menu')) literal(args.length - 1)
        // Semantic metadata accompanies legacy fallback prose on the same producer.
        const semantic = call.some(t => ['ttsUiMessage', 'UiMessage', 'uiMessage', '"ui_message"'].includes(t.value))
        if (['TtsActionError', 'rejected'].includes(token.value) && !semantic) literal(1)
        const nextPut = tokens.slice(end + 1, end + 5).map(t => t.value).join(' ')
        if (token.value === 'put' && !nextPut.startsWith('. put ( "ui_message"')
            && ['"message"', '"error"', '"title"', '"body"'].includes(args[0]?.[0]?.value)) literal(1)
        if (token.value === 'mapOf' && !semantic) {
            for (const arg of args) {
                if (['"message"', '"error"', '"title"', '"body"'].includes(arg[0]?.value) && arg[1]?.value === 'to') report(arg[2])
            }
        }
    }
    return [...new Map(failures.map(value => [`${value.line}:${value.text}`, value])).values()]
}

/** The same SHA-256 contract is enforced with Java by Gradle, without Node. */
export async function verifyFingerprints(contents, read = path => readFile(resolve(root, path))) {
    const lines = contents.trim().split('\n'), failures = [], seen = new Set()
    for (const line of lines) {
        const match = /^([a-f0-9]{64})  ([\w./-]+)$/.exec(line)
        if (!match || match[2].startsWith('/') || match[2].split('/').some(part => part === '..') || seen.has(match[2])) {
            failures.push(`Invalid fingerprint entry: ${line}`)
            continue
        }
        const [, expected, path] = match
        seen.add(path)
        try {
            if (createHash('sha256').update(await read(path)).digest('hex') !== expected) failures.push(`Stale fingerprint: ${path}`)
        } catch { failures.push(`Missing fingerprint input: ${path}`) }
    }
    return failures
}

async function kotlinFiles(directory) {
    const entries = await readdir(resolve(root, directory), { withFileTypes: true })
    return (await Promise.all(entries.map(entry => entry.isDirectory() ? kotlinFiles(`${directory}/${entry.name}`)
        : entry.name.endsWith('.kt') ? [`${directory}/${entry.name}`] : []))).flat()
}

export async function checkAndroidI18nSource() {
    const failures = await verifyFingerprints(await readFile(resolve(root, fingerprintPath), 'utf8'))
    for (const path of (await Promise.all(sourceRoots.map(kotlinFiles))).flat()) {
        for (const failure of scanKotlinSource(await readFile(resolve(root, path), 'utf8'))) {
            failures.push(`${path}:${failure.line}: direct UI literal ${failure.text}`)
        }
    }
    return failures
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    const failures = await checkAndroidI18nSource()
    if (failures.length) { console.error(failures.join('\n')); process.exitCode = 1 }
    else console.log('Android i18n source and fingerprints checked')
}
