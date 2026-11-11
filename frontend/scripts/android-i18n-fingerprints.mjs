import { createHash } from 'node:crypto'

const localePath = /^frontend\/src\/i18n\/locales\/[\w-]+\.json$/

/** A selected translation hashes its decoded UTF-8 text, independent of JSON formatting. */
export function fingerprintBytes(identifier, bytes, catalogs = new Map()) {
    const [path, key, extra] = identifier.split('#')
    if (key === undefined) return bytes
    if (!localePath.test(path) || !key || extra !== undefined) throw new Error(`Invalid translation fingerprint: ${identifier}`)
    if (!catalogs.has(path)) catalogs.set(path, JSON.parse(bytes.toString('utf8')))
    const catalog = catalogs.get(path)
    if (!Object.hasOwn(catalog, key) || typeof catalog[key] !== 'string') {
        throw new Error(`Missing translation: ${identifier}`)
    }
    return Buffer.from(catalog[key], 'utf8')
}

/** Keep whole-file checks for code and outputs; check only selected native messages in locale JSON. */
export function createAndroidFingerprints(sources, outputs, manifest) {
    const entries = [], catalogs = new Map()
    for (const [path, bytes] of Object.entries({ ...sources, ...outputs })) {
        const identifiers = localePath.test(path)
            ? Object.keys(manifest.messages).sort().map(key => `${path}#${key}`) : [path]
        for (const identifier of identifiers) {
            const hash = createHash('sha256').update(fingerprintBytes(identifier, bytes, catalogs)).digest('hex')
            entries.push(`${hash}  ${identifier}`)
        }
    }
    return entries.join('\n') + '\n'
}
