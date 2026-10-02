<script setup>
// The "Shared with TwiCC" footer of every public share page (session, document, HTML artifact): the logo,
// large enough for the full robot, and the whole sentence form ONE link. The same height as the session
// page's header (3rem). `solid` paints the page surface behind it (the document and artifact pages); without
// it the footer is transparent, for the session page's glass bar (class `glass-sticky` passed by the owner).
// The slot is for what sits at the right edge of the bar (the theme toggle).
import BrandLogo from '../components/ui/BrandLogo.vue'

defineProps({
    solid: { type: Boolean, default: false },
})
</script>

<template>
    <footer class="share-footer" :class="{ 'share-footer--solid': solid }">
        <a class="share-footer-link" href="https://github.com/twidi/twicc" target="_blank" rel="noopener noreferrer">
            <BrandLogo :size="32" /> <span>Shared with <u>TwiCC</u></span>
        </a>
        <slot />
    </footer>
</template>

<style>
.share-footer { flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
    min-height: 3rem; position: relative; }
.share-footer--solid { background: var(--wa-color-surface-default); }
/* The logo and the whole sentence are one link. */
.share-footer-link { display: inline-flex; align-items: center; gap: .5rem; color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m); text-decoration: none; }
.share-footer-link:hover, .share-footer-link:focus-visible { color: var(--wa-color-text-normal); }
</style>
