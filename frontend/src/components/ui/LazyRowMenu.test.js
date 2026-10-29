import test from 'node:test'
import assert from 'node:assert/strict'
import { h, onMounted } from 'vue'
import { compileComponent, makeRenderer, flush, deferred, descendants } from '../../../tests/helpers/scrollerComponentHarness.js'

function fixture(t) {
    const tooltips = []
    const Tooltip = {
        props: ['for', 'hoist'],
        setup(props) { onMounted(() => tooltips.push({ for: props.for, hoist: props.hoist })); return () => null },
    }
    const Menu = compileComponent(new URL('./LazyRowMenu.vue', import.meta.url), { './AppTooltip.vue': Tooltip })
    const firstRender = deferred()
    const view = makeRenderer(40, element => {
        if (element.type === 'wa-dropdown') { element.open = false; element.updateComplete = firstRender.promise }
    })
    const selected = []
    const app = view.renderer.createApp({ render: () => h(Menu, {
        triggerId: 'menu-trigger-s1', label: 'Session actions', menuClass: 'session-menu', triggerClass: 'session-menu-trigger',
        hoistTooltip: true, onSelect: event => selected.push(event),
    }, { default: () => h('wa-dropdown-item', { value: 'rename' }) }) })
    app.mount(view.root)
    t.after(() => app.unmount())
    const find = type => descendants(view.root, node => node.type === type)
    return { find, firstRender, selected, tooltips }
}

test('a closed row renders only the trigger, with the shared and the list classes', async t => {
    const f = fixture(t)
    await flush()
    assert.equal(f.find('wa-dropdown').length, 0)
    assert.equal(f.find('wa-dropdown-item').length, 0, 'the items are not mounted')
    const [button] = f.find('wa-button')
    assert.equal(button.props.id, 'menu-trigger-s1')
    assert.deepEqual(button.props.class, 'sidebar-row-menu-trigger session-menu-trigger')
    assert.equal(button.parent.type, 'span')
    assert.equal(button.parent.props.class, 'sidebar-row-menu session-menu')
    assert.deepEqual(f.tooltips, [{ for: 'menu-trigger-s1', hoist: true }])
})

test('the first click mounts the dropdown closed and opens it after its first render', async t => {
    const f = fixture(t)
    await flush()
    f.find('wa-button')[0].props.onClick()
    await flush()
    const [dropdown] = f.find('wa-dropdown')
    assert.ok(dropdown, 'the dropdown is mounted')
    assert.equal(dropdown.props.class, 'sidebar-row-menu session-menu')
    assert.equal(f.find('span').length, 0, 'the bare trigger is gone')
    assert.equal(f.find('wa-dropdown-item').length, 1, 'the items are mounted')
    const [trigger] = f.find('wa-button')
    assert.equal(trigger.parent, dropdown)
    assert.equal(trigger.props.slot, 'trigger')
    assert.equal(trigger.props.id, 'menu-trigger-s1')
    assert.equal(dropdown.open, false, 'not opened before the first render completes')
    f.firstRender.resolve()
    await flush()
    assert.equal(dropdown.open, true)
    assert.deepEqual(f.tooltips.map(tip => tip.for), ['menu-trigger-s1', 'menu-trigger-s1'], 'the tooltip remounts on the new trigger')
})

test('a selection is forwarded and the dropdown stays mounted after it closes', async t => {
    const f = fixture(t)
    await flush()
    f.find('wa-button')[0].props.onClick()
    f.firstRender.resolve()
    await flush()
    const [dropdown] = f.find('wa-dropdown')
    const event = { detail: { item: { value: 'rename' } } }
    dropdown.props.onWaSelect(event)
    assert.deepEqual(f.selected, [event])
    dropdown.open = false
    await flush()
    assert.equal(f.find('wa-dropdown')[0], dropdown)
})
