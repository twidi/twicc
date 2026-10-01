import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, '..', rel), 'utf8')
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()

// Visual refresh retouches: the tree/content splitters of the Files and Git tabs use the same
// divider affordance as the sidebar split and the layout splitters: the touch pill (.panel-grip,
// styles/surfaces.css), and a line that is half accent on hover and full accent while dragging.
for (const [name, file, cls] of [['Files', 'components/files/FilesPanel.vue', 'files-split-panel'], ['Git', 'components/git/GitPanel.vue', 'git-split-panel']]) {
    test(`${name}: the divider slot holds the shared touch pill, the old handle is gone`, () => {
        const sfc = read(file)
        assert.ok(sfc.includes('<span slot="divider" class="panel-grip" aria-hidden="true"></span>'))
        assert.ok(!sfc.includes('divider-handle'))
        assert.ok(!sfc.includes('grip-lines-vertical'))
    })

    test(`${name}: the divider line is half accent on hover, full accent while dragging`, () => {
        const sfc = read(file)
        assert.ok(sfc.includes('const { dragging: splitResizing } = useSplitDividerDragFlag(splitPanelRef)'))
        assert.ok(sfc.includes(`:class="{ 'keep-alive-hidden': keepAliveHidden, resizing: splitResizing }"`))
        const css = norm(sfc.slice(sfc.indexOf('<style')))
        assert.ok(css.includes('&::part(divider):hover { background-color: color-mix(in oklab, var(--wa-color-brand-fill-loud) 50%, transparent); }'), cls)
        assert.ok(css.includes('&.resizing::part(divider) { background-color: var(--wa-color-brand-fill-loud); }'), cls)
    })
}
