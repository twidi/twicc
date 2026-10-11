// The production build minifies CSS with Lightning CSS, which treats a property and its
// vendor-prefixed alias as one property and keeps only the last declaration when the value
// is not one it can parse (a var(), for instance). `backdrop-filter: var(--x);` followed by
// `-webkit-backdrop-filter: var(--x);` thus shipped as the -webkit- line alone, which Firefox
// ignores. Writing the prefixed alias first keeps both.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

const srcDir = join(dirname(fileURLToPath(import.meta.url)), '..')

function* styleFiles(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, entry.name)
        if (entry.isDirectory()) yield* styleFiles(path)
        else if (/\.(css|vue)$/.test(entry.name)) yield path
    }
}

/** CSS of a file (the <style> blocks of a .vue), comments blanked so line numbers hold. */
function cssOf(path) {
    const text = readFileSync(path, 'utf8')
    const css = path.endsWith('.vue')
        ? text.replace(/<style[^>]*>([\s\S]*?)<\/style>|[^\n]/g, (match, style) => style ?? (match === '\n' ? '\n' : ' '))
        : text
    return css.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '))
}

test('a vendor-prefixed declaration never follows its standard property in a rule', () => {
    const offenders = []
    for (const path of styleFiles(srcDir)) {
        const css = cssOf(path)
        for (const block of css.matchAll(/\{([^{}]*)\}/g)) {
            const seen = new Set()
            for (const decl of block[1].matchAll(/(?:^|;)\s*([-\w]+)\s*:/g)) {
                const property = decl[1].toLowerCase()
                const prefixed = property.match(/^-(?:webkit|moz|ms)-(.+)$/)
                if (prefixed && seen.has(prefixed[1])) {
                    const line = css.slice(0, block.index + 1 + decl.index).split('\n').length
                    offenders.push(`${relative(srcDir, path)}:${line} ${prefixed[1]} before ${property}`)
                } else if (!prefixed) {
                    seen.add(property)
                }
            }
        }
    }
    assert.deepEqual(offenders, [])
})
