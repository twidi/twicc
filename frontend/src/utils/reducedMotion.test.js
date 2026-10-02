// Run with: node --test src/utils/reducedMotion.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

import { isReducedMotion } from './reducedMotion.js'

const classes = (...names) => ({ document: { documentElement: { classList: { contains: (c) => names.includes(c) } } } })
const media = (matches) => ({ matchMedia: () => ({ matches }) })

test('reduced when <html> carries reduce-motion (the system preference or the setting)', () => {
    assert.equal(isReducedMotion(classes('reduce-motion')), true)
})

test('reduced when the system asks and the class is not there yet', () => {
    assert.equal(isReducedMotion(media(true)), true)
})

test('not reduced otherwise', () => {
    assert.equal(isReducedMotion(media(false)), false)
    assert.equal(isReducedMotion({}), false)
    assert.equal(isReducedMotion(classes('reduce-effects-only')), false)
})

// The system preference is read in one place. A new `@media (prefers-reduced-motion)` or
// matchMedia would ignore the "Reduce effects" setting: use `:root.reduce-motion` / isReducedMotion().
test('nothing but utils/reducedMotion.js reads prefers-reduced-motion', () => {
    const srcDir = join(dirname(fileURLToPath(import.meta.url)), '..')
    const offenders = []
    const walk = (dir) => {
        for (const name of readdirSync(dir)) {
            const path = join(dir, name)
            if (statSync(path).isDirectory()) walk(path)
            else if (/\.(css|vue|js)$/.test(name) && !name.endsWith('.test.js')) {
                const rel = relative(srcDir, path)
                if (rel === join('utils', 'reducedMotion.js')) continue
                if (/prefers-reduced-motion/.test(readFileSync(path, 'utf8'))) offenders.push(rel)
            }
        }
    }
    walk(srcDir)
    assert.deepEqual(offenders, [])
})

// Cascades, exits and the group reveal are movement and stagger: instant under reduced motion
// (system or setting). View transitions are a costly effect: off under "Reduce effects" only.
test('cascades, exits, group reveal and home cards skip their animation when motion is reduced', () => {
    const read = (rel) => readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', rel), 'utf8')
    assert.match(read('composables/useListCascade.js'), /function itemClass\(item\) \{\s*if \(isReducedMotion\(env\)\) return null/)
    assert.match(read('composables/useListCascade.js'), /function itemStyle\(item\) \{\s*if \(isReducedMotion\(env\)\) return null/)
    assert.match(read('composables/useListExit.js'), /const exits = isReducedMotion\(env\) \? \[\] : pickExits\(/)
    assert.match(read('composables/useGroupReveal.js'), /function noteToggle\(headKey, expanding, headHeight\) \{\s*(\/\/[^\n]*\n\s*)?if \(isReducedMotion\(env\)\) return/)
    assert.match(read('composables/useHomeCardCascade.js'), /if \(isReducedMotion\(\)\) return\s*elements\.sort/)
})

test('view transitions are skipped under "Reduce effects"', () => {
    const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'viewTransition.js'), 'utf8')
    assert.match(src, /depth > 0 \|\| isReduceEffects\(env\)\) \{\s*update\(\)\s*return/)
})
