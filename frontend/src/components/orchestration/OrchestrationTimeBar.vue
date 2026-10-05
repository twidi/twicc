<script setup>
// The thin bar under a node's settings row: when the node ran, relative to the whole tree. One neutral
// brand colour for every node (never the state's); a working node fades out toward the right end of the
// track. The geometry comes from ``computeTimeline`` (utils/orchestrationView.js).
defineProps({
    geometry: { type: Object, required: true }, // { left, width, live } in percent
    title: { type: String, default: null },
})
</script>

<template>
    <div class="otime" :title="title">
        <i
            class="otime-fill"
            :class="{ 'is-live': geometry.live }"
            :style="{ left: `${geometry.left}%`, width: `${geometry.width}%` }"
        ></i>
    </div>
</template>

<style scoped>
.otime {
    position: relative;
    height: 0.4rem;
    border-radius: 999px;
    background: color-mix(in oklab, var(--wa-color-neutral-50) 14%, transparent);
}

.otime-fill {
    position: absolute;
    top: 0;
    bottom: 0;
    border-radius: 999px;
    background: var(--wa-color-brand-60);
    opacity: 0.85;
}

/* Static gradient, no motion: it reads "still going" and reaches the end of the track. */
.otime-fill.is-live {
    background: linear-gradient(90deg, var(--wa-color-brand-60), color-mix(in oklab, var(--wa-color-brand-60) 15%, transparent));
}
</style>
