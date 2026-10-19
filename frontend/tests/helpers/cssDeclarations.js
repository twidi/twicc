import assert from 'node:assert/strict'

// Read the CSS of a component's `<style>` block in a node:test, without a browser.

/** The declarations of the first `selector { ... }` rule of a style block, as a property -> value map. */
export function declarationsOf(source, selector) {
    const start = source.indexOf(`\n${selector} {`)
    assert.notEqual(start, -1, `rule ${selector}`)
    const body = source.slice(source.indexOf('{', start) + 1, source.indexOf('}', start))
    return new Map(body.replace(/\/\*[\s\S]*?\*\//g, '').split(';').map((d) => d.trim()).filter(Boolean)
        .map((d) => [d.slice(0, d.indexOf(':')).trim(), d.slice(d.indexOf(':') + 1).trim()]))
}

/**
 * The pixels (16px root) of a length declaration: `px`, `rem`, `var(--x)` resolved from `vars` (a name -> value
 * map), and `calc()` arithmetic on them. A border shorthand counts its first token (the width).
 *
 * @param {string} value
 * @param {Map<string, string>} vars
 * @returns {number}
 */
export function cssPixels(value, vars) {
    let text = value.trim().split(/\s+(?![^(]*\))/)[0]
    for (let depth = 0; /var\(/.test(text); depth++) {
        assert.ok(depth < 10, `var() loop in ${value}`)
        text = text.replace(/var\((--[\w-]+)\)/g, (_, name) => {
            assert.ok(vars.has(name), `unknown ${name} in ${value}`)
            return vars.get(name)
        })
    }
    const expression = text.replace(/calc/g, '').replace(/(\d*\.?\d+)(rem|px)\b/g, (_, n, unit) => String(Number(n) * (unit === 'rem' ? 16 : 1)))
    assert.match(expression, /^[\d.\s+\-*/()]+$/, `not a length: ${value}`)
    return Function(`return (${expression})`)()
}
