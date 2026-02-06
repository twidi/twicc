<script setup>
</script>

<template>
    <div class="devtools-files-panel">
        <wa-split-panel
            class="files-split-panel"
            :position-in-pixels="250"
            primary="start"
            snap="150px 250px 350px"
            snap-threshold="30"
        >
            <wa-icon slot="divider" name="grip-lines-vertical" class="divider-handle"></wa-icon>

            <!-- File tree (left panel) -->
            <div slot="start" class="files-tree-panel">
                <div class="panel-placeholder">File tree</div>
            </div>

            <!-- File content (right panel) -->
            <div slot="end" class="files-content-panel">
                <div class="panel-placeholder">File content</div>
            </div>
        </wa-split-panel>
    </div>
</template>

<style scoped>
.devtools-files-panel {
    height: 100%;
    overflow: hidden;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Split panel: file tree (start/primary) + file content (end)
   ═══════════════════════════════════════════════════════════════════════════ */

.files-split-panel {
    height: 100%;
    --min: 120px;
    --max: 60%;

    &::part(divider) {
        background-color: var(--wa-color-surface-border);
        width: 4px;
    }
}

/* Divider handle (visible on touch devices only) */
.divider-handle {
    color: var(--wa-color-surface-border);
    display: none;
    scale: 3;
}

@media (pointer: coarse) {
    .divider-handle {
        display: inline;
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
   Panel slots
   ═══════════════════════════════════════════════════════════════════════════ */

.files-tree-panel {
    height: 100%;
    overflow: auto;
    display: flex;
    flex-direction: column;
}

.files-content-panel {
    height: 100%;
    overflow: auto;
    display: flex;
    flex-direction: column;
}

/* Placeholder styling (temporary, until real components are built) */
.panel-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
