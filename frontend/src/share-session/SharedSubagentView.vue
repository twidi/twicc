<script setup>
import { computed, provide } from 'vue'
import { INLINE_ARTIFACT_CONTEXT } from '../inline-artifacts/context.js'
import ShareItemsList from './ShareItemsList.vue'
import { useDataStore } from '../stores/data'
import { getAgentDisplay } from '../utils/agentLabel'

const props = defineProps({ stack: { type: Array, required: true } })
const emit = defineEmits(['close', 'clear'])
const store = useDataStore()
provide(INLINE_ARTIFACT_CONTEXT, null)
const current = computed(() => props.stack[props.stack.length - 1])
// Same label as the owner's subagent tabs: the session slug, else the short id.
const agentLabel = (id) => {
    const { name, isFallback } = getAgentDisplay(id, store)
    return isFallback ? `Agent "${name}"` : name
}
</script>

<template>
    <div class="subagent-drawer glass-veil">
        <div class="subagent-backdrop" @click="emit('clear')"></div>
        <div class="subagent-panel">
            <header class="subagent-head">
                <nav class="crumbs">
                    <span v-for="(id, i) in stack" :key="id" class="crumb">
                        Agent "{{ agentLabel(id) }}"<span v-if="i < stack.length - 1"> ›</span>
                    </span>
                </nav>
                <wa-button size="small" appearance="plain" @click="emit('close')">
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
            </header>
            <ShareItemsList :key="current" :session-id="current" :parent-session-id="current" :last-line="100000" />
        </div>
    </div>
</template>

<style>
/* The veil's opacity is the drawer's own property, so it can fade with the slide (glass.css reads it). */
@property --glass-veil-opacity {
    syntax: '<number>';
    inherits: true;
    initial-value: 1;
}
</style>

<style scoped>
.subagent-drawer { position: fixed; inset: 0; z-index: 20; }
/* The veil is the drawer's glass-veil layer; the backdrop stays as the click target. */
.subagent-backdrop { position: absolute; inset: 0; }
/* A sub-agent opens from the right and takes 90% of the width. Its background is the page's own: the
   canvas (the gradient auras), fixed to the viewport like the page's, so the panel reads as the same
   surface. The head is transparent over it. */
.subagent-panel { position: absolute; top: 0; right: 0; bottom: 0; width: 90%;
    padding-inline: .5rem; background: var(--canvas-background); background-attachment: fixed;
    box-shadow: -4px 0 24px rgba(0,0,0,.3);
    display: flex; flex-direction: column; overflow: hidden; }
/* The inner ShareItemsList (flex:1; min-height:0 via the shell styles) owns the
   scroll, so the head stays fixed and the drawer never traps the wheel. */
.subagent-head { flex: 0 0 auto; display: flex; justify-content: space-between;
    align-items: center; padding: .5rem 1rem; }
.crumbs { display: flex; gap: .35rem; font-size: var(--wa-font-size-s); color: var(--wa-color-text-quiet); }

/* Opening and closing (the owner wraps this component in <Transition name="subagent-drawer">): the panel
   slides in from the right edge (the slide follows --motion-amount: reduced motion keeps the fade), and the
   veil fades with it. */
.subagent-drawer { transition: --glass-veil-opacity 300ms ease-in-out; }
.subagent-drawer-enter-from,
.subagent-drawer-leave-to { --glass-veil-opacity: 0; }
.subagent-drawer-enter-active .subagent-panel { transition: translate 320ms var(--motion-ease-out, ease-out); }
.subagent-drawer-leave-active .subagent-panel { transition: translate 240ms ease-in; }
.subagent-drawer-enter-from .subagent-panel,
.subagent-drawer-leave-to .subagent-panel { translate: calc(100% * var(--motion-amount, 1)) 0; }
</style>
