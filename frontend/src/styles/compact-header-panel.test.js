import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()

// Visual refresh retouches: the opened compact header (session and project) is a glass panel hanging
// under the title row, like the popovers. The glass class is set only at compact height (the rows are
// inline otherwise).
const HEADERS = [
    ['session header', 'components/session/detail/SessionHeader.vue', 'session-collapsible-rows', 'session-header', '../../../utils/compactHeight', 'var(--wa-space-xs)'],
    ['project header', 'components/project/ProjectDetailHeader.vue', 'detail-collapsible-rows', 'detail-header', '../../utils/compactHeight', 'var(--wa-space-m)'],
]

for (const [name, file, rows, header, importPath, gap] of HEADERS) {
    const sfc = readFileSync(join(here, '..', file), 'utf8')
    const style = norm(sfc.slice(sfc.indexOf('<style')))

    test(`${name}: the rows get the glass class only at compact height, from a ref (the styles read a class Vue cannot watch)`, () => {
        assert.ok(sfc.includes(`<div class="${rows}" :class="{ 'glass-surface': compactHeight }">`))
        // compactHeight comes from the shared ref; the same import may carry other names (the root font size).
        assert.ok(new RegExp(`import \\{ compactHeight[^}]*\\} from '${importPath}'`).test(sfc))
    })

    test(`${name}: the panel hangs inset under the title row with a full radius, and does not use opacity`, () => {
        const m = style.match(new RegExp(`:where\\(html\\.compact-height\\) \\.${rows} \\{([^}]*)\\}`))[1]
        for (const decl of ['position: absolute;', 'top: 100%;', 'left: var(--wa-space-xs);', 'right: var(--wa-space-xs);', 'border-radius: var(--wa-border-radius-l);', `gap: ${gap};`]) {
            assert.ok(m.includes(decl), decl)
        }
        assert.doesNotMatch(m, /opacity: [01];|background|box-shadow/)
    })

    test(`${name}: it reveals through --twicc-reveal (glass fades without stopping the blur) and a small move`, () => {
        const closed = style.match(new RegExp(`:where\\(html\\.compact-height\\) \\.${rows} \\{([^}]*)\\}`))[1]
        assert.ok(closed.includes('--twicc-reveal: 0;'))
        assert.ok(closed.includes('--twicc-reveal-filter: opacity(var(--twicc-reveal));'))
        assert.ok(closed.includes('translate: 0 calc(-0.5rem * var(--motion-amount));'))
        assert.ok(closed.includes('transition: --twicc-reveal var(--motion-dur-2) ease-in-out, translate var(--motion-dur-2) var(--motion-ease-out), visibility var(--motion-dur-2);'))
        const open = style.match(new RegExp(`:where\\(html\\.compact-height\\) \\.${header}\\.compact-expanded \\.${rows} \\{([^}]*)\\}`))[1]
        assert.ok(open.includes('--twicc-reveal: 1;') && open.includes('translate: 0 0;') && open.includes('visibility: visible;'))
    })
}
