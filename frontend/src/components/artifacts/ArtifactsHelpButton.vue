<script setup>
// ArtifactsHelpButton.vue — a "What are artifacts?" button that opens the
// shared help dialog on the `what-are-artifacts` page. Opened without the
// "Don't show this again" switch (it's an on-demand reference, not a
// one-time nudge). Dropped into the empty states of the Artifacts view (the
// bookmark-list sidebar when nothing is bookmarked, and the main render
// pane's "Select an artifact" placeholder) so someone landing there can
// learn what artifacts are.
import { showHelp } from '../help/showHelp'

function open() {
    showHelp('what-are-artifacts', { showDontShowAgain: false })
}
</script>

<template>
    <wa-button appearance="outlined" variant="brand" size="small" @click="open">
        <wa-icon slot="start" name="circle-question"></wa-icon>
        {{ $t('artifacts.help.whatAreArtifacts') }}
    </wa-button>
</template>
