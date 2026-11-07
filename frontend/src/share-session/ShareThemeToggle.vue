<script setup>
// Footer color-scheme switcher for the share viewers that have no options menu
// (the doc viewer and the HTML-artifact shell). Cycles system → light → dark,
// persisting through the same viewer-prefs blob the session menu writes, so a
// choice carries across every share opened on this origin.
import { computed } from 'vue'
import { useSettingsStore } from '../stores/settings'

const ORDER = ['system', 'light', 'dark']
const ICON = { system: 'circle-half-stroke', light: 'sun', dark: 'moon' }
const LABEL_KEYS = { system: 'sharing.theme.system', light: 'sharing.theme.light', dark: 'sharing.theme.dark' }

// Drive the scheme through the store (not applyShareColorScheme directly): the
// store action updates the reactive _colorScheme AND applies it to the DOM, so
// content that reads the store — notably MarkdownContent's Mermaid re-render
// watcher on _effectiveColorScheme — actually reacts to the toggle.
const settings = useSettingsStore()
const scheme = computed(() => settings._colorScheme)
function cycle() {
    settings.setColorScheme(ORDER[(ORDER.indexOf(settings._colorScheme) + 1) % ORDER.length])
}
</script>

<template>
    <button type="button" class="share-theme-toggle"
            :title="$t(LABEL_KEYS[scheme])" :aria-label="$t(LABEL_KEYS[scheme])" @click="cycle">
        <wa-icon :name="ICON[scheme]"></wa-icon>
    </button>
</template>

<style>
/* Anchors the absolutely-positioned toggle; harmless on footers without one. */
.share-footer { position: relative; }
.share-theme-toggle {
    position: absolute;
    right: 0.5rem;
    top: 50%;
    transform: translateY(-50%);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.75rem;
    height: 1.75rem;
    padding: 0;
    border: 0;
    background: none;
    color: inherit;
    line-height: 0;
    cursor: pointer;
    opacity: 0.6;
}
.share-theme-toggle wa-icon { font-size: 0.9rem; }
.share-theme-toggle:hover { opacity: 1; }
</style>
