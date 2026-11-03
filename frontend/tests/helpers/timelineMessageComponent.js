import * as Vue from 'vue'
import { compileComponent } from './scrollerComponentHarness.js'
import * as messageSnapshot from '../../src/utils/timeline/messageSnapshot.js'

// MarkdownContent uses DOM rendering and store-backed services. Keep this boundary double local;
// the compiled TimelineMessage still chooses when to load it and passes the complete saved source.
const MarkdownContent = {
    props: ['source'],
    setup: props => () => Vue.h('article', { 'data-markdown-source': props.source }, props.source),
}
export const Message = compileComponent(new URL('../../src/components/timeline/TimelineMessage.vue', import.meta.url), {
    '../../utils/timeline/messageSnapshot': messageSnapshot,
    '../ui/MarkdownContent.vue': MarkdownContent,
})
